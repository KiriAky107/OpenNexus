import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.local_models import manager
from app.local_models.runtime import Runtime
from app.providers.base import ProviderError


@pytest.mark.parametrize('threaded', [False, True])
def test_large_embedding_result_crosses_pipe_limit_without_truncation(monkeypatch, tmp_path, threaded):
    import app.local_models.runtime as module
    import app.local_models.process as process_module
    from app.local_models.protocol import response_lines
    vector = [0.012345678901234567] * 384
    result = {'result': [vector] * 2111}
    assert len(json.dumps(result).encode()) > 16 * 1024 * 1024
    assert max(map(len, response_lines(result, 'embedding'))) < 16 * 1024 * 1024
    worker = tmp_path / 'large_worker.py'
    protocol_dir = Path(module.__file__).parent
    worker.write_text(
        'import sys,json\n'
        f'sys.path.insert(0, {str(protocol_dir)!r})\n'
        'from protocol import response_lines\n'
        'request=json.load(sys.stdin)\n'
        'vector=[0.012345678901234567]*384\n'
        'for line in response_lines({"result":[vector]*len(request["payload"]["texts"])}, "embedding"):\n'
        ' sys.stdout.write(line)\n', encoding='utf-8')
    monkeypatch.setattr(module, 'read_state', lambda key: {'status': 'installed'})
    monkeypatch.setattr(module, 'interpreter', lambda *_: Path(sys.executable))
    original_async = asyncio.create_subprocess_exec
    original_threaded = process_module.ThreadedProcess

    async def spawn(*args, **kwargs):
        if threaded:
            raise NotImplementedError
        return await original_async(sys.executable, str(worker), **kwargs)

    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(process_module, 'ThreadedProcess',
                        lambda args, **kwargs: original_threaded((sys.executable, str(worker)), **kwargs))
    actual = asyncio.run(Runtime().infer('bekko', 'embedding', {'texts': ['test'] * 2111}))
    assert actual == result['result']


def test_download_resumes_partial_and_checks_digest(monkeypatch):
    payload = b'verified-model-weights'
    entry = {'path':'model.safetensors','size':len(payload),'hash':hashlib.sha256(payload).hexdigest(),
             'algorithm':'sha256','url':'https://fixture.invalid/weights'}
    async def manifest(client, spec):
        return [entry]
    monkeypatch.setattr(manager, '_manifest', manifest)
    path = manager.model_path('bekko')
    path.mkdir(parents=True)
    (path/'model.safetensors.partial').write_bytes(payload[:5])
    requests = []
    def respond(request):
        requests.append(request)
        assert request.headers['range'] == 'bytes=5-'
        return httpx.Response(206, headers={'content-range':f'bytes 5-{len(payload)-1}/{len(payload)}'},content=payload[5:])
    original = httpx.AsyncClient
    monkeypatch.setattr(manager.httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(respond)))
    asyncio.run(manager._download('bekko'))
    assert manager.read_state('bekko')['status'] == 'installed'
    assert (path/'model.safetensors').read_bytes() == payload
    assert manager.valid_file(path/'model.safetensors',entry)
    (path/'model.safetensors').write_bytes(b'x'*len(payload))
    assert not manager.valid_file(path/'model.safetensors',entry)
    assert len(requests) == 1


def test_local_model_missing_is_explicit():
    with pytest.raises(ProviderError) as error:
        asyncio.run(Runtime().infer('qwen3-asr','transcription',{'source':'missing.wav'}))
    assert error.value.code == 'LOCAL_MODEL_NOT_INSTALLED'


def test_cancel_reaps_active_model_process(monkeypatch):
    import app.local_models.runtime as module
    monkeypatch.setattr(module,'read_state',lambda key:{'status':'installed'})
    monkeypatch.setattr(module,'interpreter',lambda *_:Path(sys.executable))
    class Input:
        def write(self, value):
            request = json.loads(value)
            assert request['config']['device'] == 'cpu'
        async def drain(self):
            pass
        def close(self):
            pass
    class Process:
        returncode = None
        stdin = Input()
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.killed = False
        def kill(self):
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
        async def wait(self):
            return self.returncode
    async def scenario():
        started = asyncio.Event()
        process = Process()
        async def spawn(*args, **kwargs):
            assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
            started.set()
            return process
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
        runtime = Runtime()
        task = asyncio.create_task(runtime.infer('qwen3-asr','transcription',{'source':'fixture.wav'}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and not runtime.active
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_subprocess_fallback_runs_and_reaps_real_worker(monkeypatch, tmp_path, cancel):
    import app.local_models.runtime as module
    import app.local_models.process as process_module

    monkeypatch.setattr(module, 'read_state', lambda key: {'status': 'installed'})
    monkeypatch.setattr(module, 'interpreter', lambda *_: Path(sys.executable))
    worker = tmp_path / 'worker.py'
    worker.write_text(
        'import json,sys,time\n'
        'request=json.load(sys.stdin)\n'
        'print(json.dumps({"progress": 1}),flush=True)\n'
        + ('time.sleep(60)\n' if cancel else '')
        + 'print(json.dumps({"result": [[1.0,0.0]], "usage": {"input_tokens": 2}}),flush=True)\n',
        encoding='utf-8',
    )
    processes = []
    original = process_module.ThreadedProcess

    def spawn(args, **kwargs):
        process = original((sys.executable, str(worker)), **kwargs)
        processes.append(process)
        return process

    async def unsupported(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', unsupported)
    monkeypatch.setattr(process_module, 'ThreadedProcess', spawn)

    async def scenario():
        runtime = Runtime()
        started = asyncio.Event()
        token = module.runtime_progress.set(lambda message: started.set())
        try:
            task = asyncio.create_task(runtime.infer('bekko', 'embedding', {'texts': ['test']}))
            await asyncio.wait_for(started.wait(), 10)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert await task == [[1.0, 0.0]]
            assert not runtime.active and not runtime.active_files and not runtime.waiters
            assert processes[0].returncode is not None
            assert processes[0].process.stdin.closed
            assert processes[0].process.stdout.closed
        finally:
            module.runtime_progress.reset(token)

    asyncio.run(scenario())
