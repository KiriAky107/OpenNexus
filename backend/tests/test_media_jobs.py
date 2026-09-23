"""无需模型下载的耐久性、取消和乐观编辑。"""
import asyncio
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.contracts import TranscriptEditRequest
from app.database.db import connect
from app.errors import ApiError
from app.services import transcription_service as jobs
from app.services.attachment_service import attachment_path


def text_attachment():
    path = attachment_path("lecture.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("原始识别内容", encoding="utf-8")
    return path


def test_idempotency_edit_history_and_event_replay():
    text_attachment()

    async def scenario():
        first = await jobs.create_transcription("lecture.txt", idempotency_key="submit-1")
        repeated = await jobs.create_transcription("lecture.txt", idempotency_key="submit-1")
        assert first.job_id == repeated.job_id
        assert first.status == "completed"
        with pytest.raises(ApiError) as conflict:
            await jobs.create_transcription("lecture.txt", language="en", idempotency_key="submit-1")
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
        revised = jobs.edit(first.job_id, TranscriptEditRequest(revision=1, text="校对内容"))
        assert revised.original_text == "原始识别内容"
        assert revised.revision == 2
        with pytest.raises(ApiError) as stale:
            jobs.edit(first.job_id, TranscriptEditRequest(revision=1, text="覆盖"))
        assert stale.value.code == "VERSION_CONFLICT"
        with closing(connect()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM media_revisions").fetchone()[0] == 1
        events = jobs.events(first.job_id)
        assert [e["event"] for e in events] == ["Queued", "TranscriptionStarted", "Completed", "Revised"]
        assert jobs.events(first.job_id, events[-2]["sequence"]) == events[-1:]

    asyncio.run(scenario())


def test_cancel_before_start_retry_and_restart_recovery():
    text_attachment()

    async def scenario():
        job = await jobs.create_transcription("lecture.txt", wait=False)
        cancelled = await jobs.cancel(job.job_id)
        assert cancelled.status == "cancelled"
        next_job = await jobs.retry(job.job_id)
        assert next_job.previous_job_id == job.job_id
        assert next_job.job_id != job.job_id
        await jobs._tasks[jobs.task_key(next_job.job_id)]
        assert jobs.require_job(next_job.job_id).status == "completed"
        # 模拟已停止进程留下的持久作业。
        cancelled.status = "running"
        jobs.save(cancelled, "TranscriptionStarted")
        jobs.recover_interrupted()
        assert jobs.require_job(job.job_id).error_code == "TRANSCRIPTION_INTERRUPTED"

    asyncio.run(scenario())


def test_controlled_upload_and_async_http_flow():
    from app.main import app
    with TestClient(app) as client:
        assert client.post("/api/media/attachments?filename=a.wav", content=b"").status_code == 422
        uploaded = client.post("/api/media/attachments?filename=lecture.txt", content="真实转写文本".encode())
        assert uploaded.status_code == 201
        attachment_id = uploaded.json()["attachment_id"]
        assert client.get(f"/api/media/attachments/{attachment_id}").content == "真实转写文本".encode()
        response = client.post("/api/media/transcriptions", json={"attachment_id": attachment_id})
        assert response.status_code == 202 and response.json()["status"] == "queued"
        job_id = response.json()["job_id"]
        events = client.get(f"/api/media/transcriptions/{job_id}/events")
        assert "event: Completed" in events.text
        assert client.get("/api/media/transcriptions").json()["page"]["total"] == 1
        assert client.get(f"/api/media/transcriptions/{job_id}").json()["text"] == "真实转写文本"
        assert client.get(f"/api/media/transcriptions/{job_id}/events", headers={"Last-Event-ID": "bad"}).status_code == 422


def test_terminology_export_and_privacy_cleanup():
    from app.main import app
    text_attachment()
    with TestClient(app) as client:
        created = client.post('/api/media/transcriptions', json={'attachment_id':'lecture.txt','terminology':{'识别':'校对'}}).json()
        job_id = created['job_id']
        client.get(f'/api/media/transcriptions/{job_id}/events')
        job = client.get(f'/api/media/transcriptions/{job_id}').json()
        assert job['text'] == '原始校对内容' and job['original_text'] == '原始识别内容'
        first = client.post(f'/api/media/transcriptions/{job_id}/notes', json={'title':'课程'}).json()
        again = client.post(f'/api/media/transcriptions/{job_id}/notes', json={'title':'课程'}).json()
        assert first['note_id'] == again['note_id']
        artifacts = client.post(f'/api/media/transcriptions/{job_id}/artifacts', json={
            'title': '课程', 'knowledge_title': '课程知识点',
            'provider_id': 'mock', 'model': 'mock-1',
        })
        assert artifacts.status_code == 201
        assert artifacts.json()['transcript']['note_id'] == first['note_id']
        assert artifacts.json()['knowledge_note']['note_id'] != first['note_id']
        repeated = client.post(f'/api/media/transcriptions/{job_id}/artifacts', json={
            'title': '课程', 'knowledge_title': '课程知识点',
            'provider_id': 'mock', 'model': 'mock-1',
        })
        assert repeated.json()['knowledge_note']['note_id'] == artifacts.json()['knowledge_note']['note_id']
        response = client.delete('/api/media/attachments/lecture.txt')
        assert first['note_id'] in response.json()['retained_note_ids']
        cleaned = client.get(f'/api/media/transcriptions/{job_id}').json()
        assert cleaned['text'] is None and cleaned['original_text'] is None and cleaned['corrections'] == []
        assert client.post(f'/api/media/transcriptions/{job_id}/retry').status_code == 409
        assert client.get('/api/media/attachments/lecture.txt').status_code == 404


def test_media_note_links_do_not_depend_on_rebuildable_note_projection():
    with closing(connect()) as conn:
        foreign_tables = {row[2] for row in conn.execute("PRAGMA foreign_key_list(media_notes)")}
    assert foreign_tables == {"media_jobs"}


def test_desktop_revision_conflict_recovers_marker_matched_note(monkeypatch):
    from app.services import note_service
    from app.services.media_notes import _create_note

    marker = "<!-- transcription:job:1:hash -->"
    recovered = SimpleNamespace(note_id="stable-note", title="Generated", markdown=f"{marker}\nbody")

    async def conflict(**_kwargs):
        raise ApiError(409, "REVISION_CONFLICT", "already written")

    monkeypatch.setattr(note_service, "create_note", conflict)
    monkeypatch.setattr(
        note_service, "list_notes",
        lambda **_kwargs: ([SimpleNamespace(note_id="stable-note", title="Generated")], 1),
    )

    async def get_note(note_id):
        return recovered if note_id == "stable-note" else None

    monkeypatch.setattr(note_service, "get_note", get_note)
    result = asyncio.run(_create_note(
        "Generated", recovered.markdown, SimpleNamespace(folder=""), marker
    ))
    assert result is recovered


def test_transcript_export_recovers_link_from_another_vault(monkeypatch):
    from app.contracts import TranscriptNoteRequest
    from app.services import note_service
    from app.services.media_notes import create_transcript_note

    text_attachment()

    async def scenario():
        job = await jobs.create_transcription("lecture.txt")
        options = TranscriptNoteRequest(title="跨库课程")
        original = await create_transcript_note(job.job_id, options)
        with closing(connect()) as conn:
            conn.execute(
                "UPDATE media_notes SET note_id=? WHERE job_id=?",
                ("note-from-another-vault", job.job_id),
            )
            conn.commit()

        real_get_note = note_service.get_note

        async def get_note(note_id):
            if note_id == "note-from-another-vault":
                return None
            return await real_get_note(note_id)

        monkeypatch.setattr(note_service, "get_note", get_note)
        recovered = await create_transcript_note(job.job_id, options)
        assert recovered.note_id == original.note_id
        with closing(connect()) as conn:
            linked = conn.execute(
                "SELECT note_id FROM media_notes WHERE job_id=?",
                (job.job_id,),
            ).fetchone()[0]
        assert linked == original.note_id

    asyncio.run(scenario())


def test_artifact_host_writes_use_distinct_child_operations(monkeypatch):
    from app import host_bridge
    from app.contracts import TranscriptArtifactsRequest
    from app.services import media_notes

    operations = []
    text_attachment()
    job = asyncio.run(jobs.create_transcription("lecture.txt"))

    async def transcript(_job_id, _options):
        operations.append(host_bridge.operation_id.get())
        return SimpleNamespace(note_id="transcript-note")

    async def knowledge(*_args):
        return "<!-- knowledge-note:test -->\n# Knowledge"

    async def create(*_args, **_kwargs):
        operations.append(host_bridge.operation_id.get())
        return SimpleNamespace(note_id="knowledge-note")

    monkeypatch.setattr(media_notes, "create_transcript_note", transcript)
    monkeypatch.setattr(media_notes, "_knowledge_markdown", knowledge)
    monkeypatch.setattr(media_notes, "_create_note", create)

    token = host_bridge.operation_id.set("11111111-1111-4111-8111-111111111111")
    try:
        result = asyncio.run(media_notes.create_transcript_artifacts(
            job.job_id,
            TranscriptArtifactsRequest(
                title="Transcript", knowledge_title="Knowledge",
                provider_id="mock", model="mock-1",
            ),
        ))
    finally:
        host_bridge.operation_id.reset(token)

    assert result["transcript"].note_id == "transcript-note"
    assert result["knowledge_note"].note_id == "knowledge-note"
    assert len(operations) == 2
    assert operations[0] != operations[1]
    assert all(operation and operation != "11111111-1111-4111-8111-111111111111" for operation in operations)


def test_course_note_blocks_are_recomposed_with_markdown_and_plot_tools(monkeypatch):
    from app.container import container
    from app.services.media_notes import _compose_course_blocks

    names = []
    original = container.tools.execute

    async def execute(call, context):
        names.append(call.name)
        return await original(call, context)

    monkeypatch.setattr(container.tools, "execute", execute)
    markdown = """## 算法
```python
left += 1
```
```mermaid
flowchart LR
  A --> B
```
```function_plot
domain: -4, 4
range: -1, 8
y = x^2
```"""
    rendered = asyncio.run(_compose_course_blocks(markdown, "media-test"))
    assert names == ["markdown.compose", "markdown.compose", "function_plot.compose"]
    assert "```python\nleft += 1\n```" in rendered
    assert "```mermaid\nflowchart LR" in rendered
    assert "```function-plot\ndomain: -4, 4" in rendered


def test_course_note_rejects_invalid_function_plot():
    from app.services.media_notes import _compose_course_blocks

    with pytest.raises(ApiError) as invalid:
        asyncio.run(_compose_course_blocks(
            "```function-plot\ndomain: -4, 4\ny = __import__('os')\n```",
            "media-test",
        ))
    assert invalid.value.code == "KNOWLEDGE_NOTE_VISUAL_INVALID"


def test_readable_export_names_do_not_overwrite_user_notes():
    from app.contracts import TranscriptNoteRequest
    from app.services import note_service
    from app.services.media_notes import create_transcript_note
    text_attachment()

    async def scenario():
        user_note = await note_service.create_note(title="课程", markdown="Do not overwrite", folder=None, tags=[])
        job = await jobs.create_transcription("lecture.txt")
        options = TranscriptNoteRequest(title="课程")
        created = await create_transcript_note(job.job_id, options)
        repeated = await create_transcript_note(job.job_id, options)
        assert created.title == "课程（2）"
        assert repeated.note_id == created.note_id
        assert (await note_service.get_note(user_note.note_id)).markdown == "Do not overwrite"
        assert "transcription:" not in created.markdown
        import re
        assert not re.search(r"[a-f0-9]{64}", created.markdown)
        assert created.file_path.endswith("课程（2）.md")
    asyncio.run(scenario())


def test_artifact_status_survives_reload_and_reports_partial_success():
    from app.main import app
    text_attachment()
    with TestClient(app) as client:
        job = client.post('/api/media/transcriptions', json={'attachment_id':'lecture.txt'}).json()
        client.get(f'/api/media/transcriptions/{job["job_id"]}/events')
        endpoint = f'/api/media/transcriptions/{job["job_id"]}'
        assert client.get(endpoint + '/artifacts').json() == {"transcript": None, "knowledge_note": None}
        note = client.post(endpoint + '/notes', json={"title": "课程转录稿"}).json()
        artifacts = client.get(endpoint + '/artifacts').json()
        assert artifacts['transcript']['note_id'] == note['note_id']
        assert artifacts['knowledge_note'] is None
        both = client.post(endpoint + '/artifacts', json={"title":"课程转录稿", "knowledge_title":"课程知识点", "provider_id":"mock", "model":"mock-1"})
        assert both.status_code == 201
        assert client.get(endpoint + '/artifacts').json()['knowledge_note']['title'] == "课程知识点"


def test_local_only_export_and_rebuild_keep_local_embedding_policy(monkeypatch):
    from types import SimpleNamespace
    from app.contracts import TranscriptNoteRequest, IndexRebuildRequest
    from app.local_models.runtime import LocalEmbedding
    from app.retrieval import routed_vectors
    from app.services import note_service, index_service
    from app.services.media_notes import create_transcript_note
    calls = []
    class Routing:
        async def embed(self, texts, *, local_only=False):
            calls.append(local_only)
            assert local_only
            return SimpleNamespace(source='local', model_id='local-test', dimensions=2,
                                   vectors=[[1.0, 0.0] for _ in texts], fallback_reason=None)
    monkeypatch.setattr(routed_vectors, 'get_model_routing', lambda: Routing())
    monkeypatch.setattr(note_service, 'embedding', LocalEmbedding())
    text_attachment()
    async def scenario():
        job = await jobs.create_transcription('lecture.txt', local_only=True)
        note = await create_transcript_note(job.job_id, TranscriptNoteRequest(title='Private'))
        assert note.markdown.startswith('---\nembedding_local_only: true\n---')
        await note_service.update_note(note.note_id, markdown=note.markdown.replace(
            'embedding_local_only: true', 'embedding_local_only: true # keep local'))
        await index_service.rebuild(IndexRebuildRequest())
        assert len(calls) >= 3 and all(calls)
    asyncio.run(scenario())
