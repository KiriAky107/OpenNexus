"""Durability, cancellation and optimistic editing without model downloads."""
import asyncio
from contextlib import closing

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
        # Simulate a persisted job left behind by a stopped process.
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
        response = client.delete('/api/media/attachments/lecture.txt')
        assert first['note_id'] in response.json()['retained_note_ids']
        cleaned = client.get(f'/api/media/transcriptions/{job_id}').json()
        assert cleaned['text'] is None and cleaned['original_text'] is None and cleaned['corrections'] == []
        assert client.post(f'/api/media/transcriptions/{job_id}/retry').status_code == 409
        assert client.get('/api/media/attachments/lecture.txt').status_code == 404


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
        await index_service.rebuild(IndexRebuildRequest())
        assert len(calls) >= 2 and all(calls)
    asyncio.run(scenario())
