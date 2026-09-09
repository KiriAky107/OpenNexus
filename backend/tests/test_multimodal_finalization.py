"""最终回归：设备恢复、持久事实和受保护的写入。"""
import asyncio
import json
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.errors import ApiError
from app.providers.base import ProviderError


@pytest.mark.parametrize('code,retries', [('LOCAL_CUDA_OOM', True), ('LOCAL_CUDA_INIT_FAILED', True),
                                          ('LOCAL_INFERENCE_FAILED', False), ('LOCAL_RUNTIME_DEPENDENCY_MISSING', False)])
def test_cuda_retries_only_device_failures_in_reaped_process(monkeypatch, code, retries):
    import app.local_models.runtime as module
    from app.services import model_diagnostics
    from app.services.usage_service import connection
    monkeypatch.setattr(module, 'configuration', lambda: module.RuntimeConfig(device='cuda'))
    monkeypatch.setattr(module, 'read_state', lambda key: {'status': 'installed'})
    monkeypatch.setattr(module, 'interpreter', lambda *_: Path(sys.executable))
    events = []

    class Process:
        def __init__(self):
            from types import SimpleNamespace
            self.stdin = SimpleNamespace(write=self.write, drain=self.drain, close=lambda: None)
            self.stdout = asyncio.StreamReader()
            self.returncode = None
            self.device = None
        def write(self, raw):
            self.device = json.loads(raw)['config']['device']
            events.append('start-' + self.device)
            result = {'error_code': code} if self.device == 'cuda' else {'result': [[1, 0]], 'usage': {'input_tokens': 2}, 'diagnostics': {'actual_device': 'cpu'}}
            self.stdout.feed_data((json.dumps(result) + '\n').encode())
            self.stdout.feed_eof()
        async def drain(self):
            pass
        async def close(self):
            pass
        async def wait(self):
            self.returncode = 0
            events.append('reaped-' + self.device)
        def kill(self):
            self.returncode = -9

    async def spawn(*args, **kwargs):
        if events:
            assert events[-1] == 'reaped-cuda'
        return Process()
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)

    async def scenario():
        runtime = module.Runtime()
        if retries:
            assert await runtime.infer('bekko', 'embedding', {'texts': ['private text']}) == [[1, 0]]
        else:
            with pytest.raises(ProviderError) as error:
                await runtime.infer('bekko', 'embedding', {'texts': ['private text']})
            assert error.value.code == code
        assert not runtime.active and not runtime.waiters
    asyncio.run(scenario())
    assert events == (['start-cuda', 'reaped-cuda', 'start-cpu', 'reaped-cpu'] if retries else ['start-cuda', 'reaped-cuda'])
    records = model_diagnostics.recent()
    assert records[0]['error_code'] == code
    assert 'private text' not in json.dumps(records)
    if retries:
        assert records[-1]['requested_device'] == 'cuda' and records[-1]['actual_device'] == 'cpu'
        assert records[-1]['fallback_reason'] == code
        assert records[0]['request_id'] == records[1]['request_id']
        assert records[0]['attempt_id'] != records[1]['attempt_id']
    with closing(connection()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM model_usage').fetchone()[0] == (2 if retries else 1)


def test_cpu_failure_does_not_loop_and_interactive_precedes_index(monkeypatch):
    import app.local_models.runtime as module
    async def scenario():
        runtime = module.Runtime()
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        async def execute(key, operation, payload, config, diagnostics):
            order.append(payload['name'])
            if payload['name'] == 'running':
                entered.set()
                await release.wait()
            return {'result': []}
        monkeypatch.setattr(runtime, '_execute', execute)
        first = asyncio.create_task(runtime.infer('bekko', 'embedding', {'name': 'running'}))
        await entered.wait()
        background = asyncio.create_task(runtime.infer('bekko', 'embedding', {'name': 'index'}, priority=20))
        query = asyncio.create_task(runtime.infer('bekko', 'embedding', {'name': 'query'}, priority=0))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, background, query)
        assert order == ['running', 'query', 'index']
        calls = []
        async def failed(key, operation, payload, config, diagnostics):
            calls.append(config.device)
            raise ProviderError('LOCAL_CUDA_OOM', 'simulated')
        monkeypatch.setattr(runtime, '_execute', failed)
        monkeypatch.setattr(module, 'configuration', lambda: module.RuntimeConfig(device='cuda'))
        with pytest.raises(ProviderError):
            await runtime.infer('bekko', 'embedding', {})
        assert calls == ['cuda', 'cpu'] and not runtime.active
    asyncio.run(scenario())


def test_durable_diagnostics_are_bounded_and_disk_size_is_real():
    from app.services import model_diagnostics
    from app.local_models import manager
    for index in range(205):
        model_diagnostics.record(model='bekko', status='failed', error_code='TEST', payload='secret', elapsed_seconds=index)
    records = model_diagnostics.recent()
    assert len(records) == 200 and records[0]['elapsed_seconds'] == 5
    assert 'secret' not in json.dumps(records)
    path = manager.model_path('bekko')
    path.mkdir(parents=True)
    (path / 'weights.partial').write_bytes(b'1234567')
    assert manager.disk_bytes('bekko') == 7


def test_upload_key_replay_and_content_conflict():
    from app.main import app
    with TestClient(app) as client:
        headers = {'Idempotency-Key': 'stable-upload-123456'}
        first = client.post('/api/media/attachments?filename=lecture.txt', content=b'original', headers=headers)
        again = client.post('/api/media/attachments?filename=lecture.txt', content=b'original', headers=headers)
        assert first.status_code == again.status_code == 201
        assert first.json()['attachment_id'] == again.json()['attachment_id']
        assert client.post('/api/media/attachments?filename=lecture.txt', content=b'changed', headers=headers).status_code == 409
        changed_name = client.post('/api/media/attachments?filename=lecture.md', content=b'original', headers=headers)
        assert changed_name.status_code == 409 and changed_name.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
        assert client.get('/api/media/attachments/' + first.json()['attachment_id']).content == b'original'


def test_updated_transcript_note_keeps_identity_and_rejects_user_edits():
    from app.contracts import TranscriptNoteRequest, TranscriptEditRequest, IndexRebuildRequest
    from app.services import transcription_service as jobs, note_service, index_service
    from app.services.media_notes import create_transcript_note
    from app.services.attachment_service import attachment_path
    path = attachment_path('lecture.txt')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('original', encoding='utf-8')
    async def scenario():
        job = await jobs.create_transcription('lecture.txt', local_only=True)
        options = TranscriptNoteRequest(title='Lecture')
        first = await create_transcript_note(job.job_id, options)
        await index_service.rebuild(IndexRebuildRequest())
        jobs.edit(job.job_id, TranscriptEditRequest(revision=1, text='revised'))
        update = options.model_copy(update={'update_existing': True})
        second = await create_transcript_note(job.job_id, update)
        assert first.note_id == second.note_id and 'revised' in second.markdown
        assert 'embedding_local_only: true' in second.markdown
        again = await create_transcript_note(job.job_id, update)
        assert again.note_id == first.note_id
        await note_service.update_note(first.note_id, markdown='User edits')
        jobs.edit(job.job_id, TranscriptEditRequest(revision=2, text='third revision'))
        with pytest.raises(ApiError) as error:
            await create_transcript_note(job.job_id, update)
        assert error.value.code == 'NOTE_CONTENT_CONFLICT'
        assert (await note_service.get_note(first.note_id)).markdown == 'User edits'
        copy = await create_transcript_note(job.job_id, options)
        assert copy.note_id != first.note_id
    asyncio.run(scenario())


def test_audio_usage_is_separate_and_unknown_durations_stay_null():
    from app.services.usage_service import UsageAttempt, aggregate
    now = datetime.now(timezone.utc)
    first = UsageAttempt('local', 'asr', 'local', 'transcription', source='local')
    first.observe({'audio_seconds': 2.25, 'usage': {}})
    first.persist(); first.persist()
    unknown = UsageAttempt('remote', 'asr', 'openai_compatible', 'transcription')
    unknown.persist()
    result = aggregate(now - timedelta(days=1), now + timedelta(days=1))
    assert result['audio_request_count'] == 2 and result['audio_covered_requests'] == 1
    assert result['audio_seconds'] == 2.25 and result['totals']['input_tokens'] is None
    remote = aggregate(now - timedelta(days=1), now + timedelta(days=1), source='api')
    assert remote['audio_seconds'] is None


def test_request_rule_import_rejects_credentials_and_host_fields():
    from app.main import app
    with TestClient(app) as client:
        path = '/api/providers/request-rules/validate'
        body = {'version': 1, 'request_overrides': [{'body': {'enable_thinking': False}}]}
        assert client.post(path, json=body).status_code == 200
        for bad in ({'api_key': 'secret'}, {'nested': {'authorization': 'secret'}}, {'stream': False}):
            body['request_overrides'][0]['body'] = bad
            assert client.post(path, json=body).status_code == 422


@pytest.mark.parametrize('stream', [False, True])
def test_inference_probe_uses_adapter_body_and_no_vault_context(monkeypatch, stream):
    import httpx
    from app.container import container
    from app.main import app
    original = container.provider_factory.build
    requests = []
    def respond(request):
        data = json.loads(request.content)
        requests.append(data)
        assert data['enable_thinking'] is False and data['stream'] == stream
        assert data['messages'] == [{'role': 'user', 'content': 'Reply with OK.'}]
        assert not data.get('tools')
        if stream:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":null}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]})
    def build(config):
        adapter = original(config)
        adapter.transport = httpx.MockTransport(respond)
        return adapter
    monkeypatch.setattr(container.provider_factory, 'build', build)
    with TestClient(app) as client:
        response = client.post('/api/providers/request-probe', json={'stream': stream, 'provider': {
            'name': 'Probe', 'provider_type': 'openai_compatible', 'base_url': 'https://fixture.invalid/v1',
            'default_model': 'test', 'request_overrides': [{'body': {'enable_thinking': False}}]}})
        assert response.status_code == 200, response.text
        assert len(requests) == 1
