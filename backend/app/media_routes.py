"""媒体存储和持久的转录控制。"""
from __future__ import annotations

import asyncio
import json
import hashlib
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.contracts import TranscriptEditRequest, TranscriptNoteRequest, TranscriptionJob
from app.database.db import connect, transaction
from app.errors import ApiError
from app.services import transcription_service as jobs
from app.services.attachment_service import attachment_path

router = APIRouter(prefix="/api/media", tags=["Media"])
from app.providers.routing import MAX_LOCAL_MEDIA_BYTES

MAX_UPLOAD_BYTES = MAX_LOCAL_MEDIA_BYTES
MEDIA_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4", ".webm", ".txt", ".md", ".docx", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".webp"}


@router.post("/attachments", status_code=201)
async def upload_attachment(request: Request, filename: str = Query(min_length=1, max_length=255),
                            idempotency_key: str | None = Header(None, min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")):
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        raise ApiError(422, "UNSUPPORTED_MEDIA", "Unsupported attachment extension.")
    identity = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else uuid4().hex
    attachment_id = f"media_{identity}{suffix}"
    destination = attachment_path(attachment_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{uuid4().hex}.upload")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "ATTACHMENT_TOO_LARGE", "Attachment exceeds 128 MiB.")
                digest.update(chunk)
                stream.write(chunk)
        if not size:
            raise ApiError(422, "EMPTY_ATTACHMENT", "Attachment is empty.")
        content_hash = digest.hexdigest()
        if idempotency_key:
            with closing(connect()) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS media_upload_idempotency (idempotency_key TEXT PRIMARY KEY, attachment_id TEXT NOT NULL, filename TEXT NOT NULL, content_hash TEXT NOT NULL)")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute("SELECT attachment_id,filename,content_hash FROM media_upload_idempotency WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                    if row:
                        if row["filename"] != Path(filename).name or row["content_hash"] != content_hash:
                            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一上传标识不能用于不同附件。")
                        existing = attachment_path(row["attachment_id"])
                        if not existing.is_file() or hashlib.sha256(existing.read_bytes()).hexdigest() != content_hash:
                            raise ApiError(409, "IDEMPOTENCY_EXPIRED", "该上传标识对应的附件已不存在，请开始一次新提交。")
                        attachment_id = row["attachment_id"]
                    else:
                        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != content_hash:
                            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一上传标识不能用于不同附件。")
                        if not destination.exists():
                            temporary.replace(destination)
                        conn.execute("INSERT INTO media_upload_idempotency VALUES (?,?,?,?)",
                                     (idempotency_key, attachment_id, Path(filename).name, content_hash))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        elif destination.exists():
            if hashlib.sha256(destination.read_bytes()).digest() != digest.digest():
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一上传标识不能用于不同附件。")
        else:
            temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"attachment_id": attachment_id, "filename": Path(filename).name, "size": size}


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: str):
    path = attachment_path(attachment_id)
    if not path.is_file():
        raise ApiError(404, "ATTACHMENT_NOT_FOUND", "Attachment was not found.")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})


@router.get("/transcriptions")
async def list_jobs(status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    if status is not None and status not in jobs.TERMINAL | {"queued", "running", "processing"}:
        raise ApiError(422, "INVALID_STATUS", "Unknown transcription status.")
    return jobs.list_transcriptions(status, limit, offset)


@router.post("/transcriptions/{job_id}/cancel", response_model=TranscriptionJob)
async def cancel_job(job_id: str):
    return await jobs.cancel(job_id)


@router.post("/transcriptions/{job_id}/retry", response_model=TranscriptionJob, status_code=202)
async def retry_job(job_id: str):
    return await jobs.retry(job_id)


@router.patch("/transcriptions/{job_id}", response_model=TranscriptionJob)
async def edit_job(job_id: str, request: TranscriptEditRequest):
    return jobs.edit(job_id, request)


@router.get("/transcriptions/{job_id}/revisions")
async def revisions(job_id: str):
    current = jobs.require_job(job_id)
    with closing(connect()) as conn:
        rows = conn.execute("SELECT job_json FROM media_revisions WHERE job_id=? ORDER BY revision", (job_id,)).fetchall()
    return {"items": [TranscriptionJob.model_validate_json(row[0]) for row in rows] + [current]}


@router.get("/transcriptions/{job_id}/events")
async def stream_events(job_id: str, request: Request, after: int = Query(-1, ge=-1),
                        last_event_id: str | None = Header(None)):
    jobs.require_job(job_id)
    if last_event_id is not None:
        try:
            after = max(after, int(last_event_id))
        except ValueError as exc:
            raise ApiError(422, "INVALID_EVENT_CURSOR", "Last-Event-ID must be an integer.") from exc

    async def stream():
        cursor = after
        idle = 0
        while not await request.is_disconnected():
            batch = jobs.events(job_id, cursor)
            for event in batch:
                cursor = event["sequence"]
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if len(batch) == 200:
                continue
            if jobs.require_job(job_id).status in jobs.TERMINAL:
                # 重新读取一次：读取该批次后可能已提交完成。
                if jobs.events(job_id, cursor):
                    continue
                return
            idle += 1
            if idle % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/transcriptions/{job_id}/notes", status_code=201)
async def create_note(job_id: str, request: TranscriptNoteRequest):
    from app.services.media_notes import create_transcript_note
    return await create_transcript_note(job_id, request)


@router.get("/attachments/{attachment_id}/cleanup-impact")
async def cleanup_impact(attachment_id: str):
    attachment_path(attachment_id)
    with closing(connect()) as conn:
        records = conn.execute("SELECT job_json FROM media_jobs").fetchall()
        affected = [TranscriptionJob.model_validate_json(row[0]) for row in records]
        affected = [job for job in affected if job.attachment_id == attachment_id]
        note_ids = []
        for job in affected:
            note_ids.extend(row[0] for row in conn.execute("SELECT note_id FROM media_notes WHERE job_id=?", (job.job_id,)))
    return {"job_ids": [job.job_id for job in affected], "retained_note_ids": sorted(set(note_ids)),
            "message": "清理原附件、转写正文、修订和术语记录；已保存笔记保留，音频链接将失效。"}


@router.delete("/attachments/{attachment_id}")
async def cleanup_attachment(attachment_id: str):
    from app.local_models.runtime import runtime
    impact = await cleanup_impact(attachment_id)
    affected = [jobs.require_job(job_id) for job_id in impact["job_ids"]]
    if runtime.media_in_use(attachment_path(attachment_id)) or any(job.status not in jobs.TERMINAL for job in affected):
        raise ApiError(409, "MEDIA_IN_USE", "Wait for media processing to finish before cleanup.")
    for path in (attachment_path(attachment_id), attachment_path(f"{attachment_id}.txt")):
        path.unlink(missing_ok=True)
    with closing(connect()) as conn, transaction(conn):
        for job in affected:
            job.text = job.original_text = None
            job.segments = []; job.original_segments = []; job.speaker_names = {}; job.corrections = []
            job.model_snapshot = {}
            job.status = "cancelled"; job.error_code = "MEDIA_PURGED"; job.error_message = "附件与转写内容已清理。"
            job.updated_at = jobs.now()
            conn.execute("UPDATE media_jobs SET job_json=?,status=?,request_json='{}' WHERE job_id=?",
                         (job.model_dump_json(), job.status, job.job_id))
            conn.execute("DELETE FROM media_revisions WHERE job_id=?", (job.job_id,))
            conn.execute("DELETE FROM media_events WHERE job_id=?", (job.job_id,))
            jobs._event(conn, job, "Purged")
    return impact
