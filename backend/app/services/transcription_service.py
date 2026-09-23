"""持久媒体作业和可重播事件； HTTP 排队，工具等待。"""
from __future__ import annotations
import asyncio
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4
from app.config import get_settings
from app.contracts import TranscriptionJob, TranscriptionRequest, TranscriptEditRequest
from app.database.db import connect, transaction
from app.errors import ApiError
from app.services.attachment_service import attachment_path

TERMINAL = {"completed", "failed", "cancelled"}
_tasks: dict[tuple[str, str], asyncio.Task] = {}

def now():
    return datetime.now(timezone.utc)

def task_key(job_id):
    return str(get_settings().db_path), job_id

def get_transcription(job_id: str) -> TranscriptionJob | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT job_json FROM media_jobs WHERE job_id=?", (job_id,)).fetchone()
    return TranscriptionJob.model_validate_json(row[0]) if row else None

def require_job(job_id):
    job = get_transcription(job_id)
    if job is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Transcription job not found.")
    return job

def _event(conn, job, event, data=None):
    sequence = conn.execute("SELECT COALESCE(MAX(sequence),-1)+1 FROM media_events WHERE job_id=?", (job.job_id,)).fetchone()[0]
    conn.execute("INSERT INTO media_events VALUES (?,?,?,?,?)", (job.job_id, sequence, event,
        json.dumps(data or {"status": job.status, "progress": job.progress}), now().isoformat()))

def save(job, event):
    job.updated_at = now()
    with closing(connect()) as conn, transaction(conn):
        conn.execute("UPDATE media_jobs SET status=?,job_json=?,updated_at=? WHERE job_id=?",
            (job.status, job.model_dump_json(), job.updated_at.isoformat(), job.job_id))
        _event(conn, job, event)

def list_transcriptions(status=None, limit=50, offset=0):
    where, args = (" WHERE status=?", [status]) if status else ("", [])
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM media_jobs" + where, args).fetchone()[0]
        rows = conn.execute("SELECT job_json FROM media_jobs" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return {"items": [TranscriptionJob.model_validate_json(row[0]) for row in rows], "page": {"total": total, "limit": limit, "offset": offset}}

def events(job_id, after=-1):
    require_job(job_id)
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM media_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 200", (job_id, after)).fetchall()
    return [{"job_id": job_id, "sequence": r["sequence"], "event": r["event"], "data": json.loads(r["data_json"]), "timestamp": r["timestamp"]} for r in rows]

def recover_interrupted():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT job_json FROM media_jobs WHERE status IN ('queued','running','processing')").fetchall()
    for row in rows:
        job = TranscriptionJob.model_validate_json(row[0])
        if task_key(job.job_id) not in _tasks:
            job.status, job.error_code = "failed", "TRANSCRIPTION_INTERRUPTED"
            job.error_message = "AI Core stopped before completion. Retry to start a new attempt."
            job.completed_at = now()
            save(job, "Failed")

async def shutdown():
    tasks = [t for k, t in list(_tasks.items()) if k[0] == str(get_settings().db_path)]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

async def create_transcription(attachment_id, language=None, *, diarization=False, local_only=False,
        word_timestamps=False, idempotency_key=None, terminology=None, wait=True, previous_job_id=None):
    request = TranscriptionRequest(attachment_id=attachment_id, language=language, diarization=diarization,
        local_only=local_only, word_timestamps=word_timestamps, idempotency_key=idempotency_key, terminology=terminology or {})
    source = attachment_path(attachment_id)
    actual = source if source.is_file() else attachment_path(f"{attachment_id}.txt")
    if not actual.is_file():
        raise ApiError(404, "ATTACHMENT_NOT_FOUND", "Attachment was not found.")
    from app.providers.routing import MAX_LOCAL_MEDIA_BYTES, MAX_MEDIA_BYTES
    if not 0 < actual.stat().st_size <= (MAX_LOCAL_MEDIA_BYTES if local_only else MAX_MEDIA_BYTES):
        raise ApiError(413, "ATTACHMENT_TOO_LARGE", "仅本地处理最大支持 128 MiB；超过 25 MiB 的录音请启用仅本地处理。")
    digest = await asyncio.to_thread(lambda: hashlib.sha256(actual.read_bytes()).hexdigest())
    from app.container import container
    from app.local_models.runtime import configuration
    from app.local_models.catalog import CATALOG
    routing = container.model_routing.snapshot()
    route = routing.configuration()
    binding = None if local_only else route.transcription
    snapshot = {"local_runtime": configuration().model_dump(), "models": {k:v.revision for k,v in CATALOG.items()},
                "transcription": binding.model_dump() if binding else None}
    if binding:
        provider = routing.providers.get_any(binding.provider_id).config
        snapshot["provider"] = provider.model_dump(exclude={"credential_id"})
    fingerprint = hashlib.sha256((digest + request.model_dump_json(exclude={"idempotency_key"}) + json.dumps(snapshot, sort_keys=True)).encode()).hexdigest()
    job = TranscriptionJob(job_id=f"transcription_{uuid4().hex}", attachment_id=attachment_id, status="queued",
        created_at=now(), updated_at=now(), language=language, local_only=local_only, previous_job_id=previous_job_id, model_snapshot=snapshot)
    existing = None
    with closing(connect()) as conn, transaction(conn):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='media_upload_idempotency'").fetchone():
            uploaded = conn.execute("SELECT filename FROM media_upload_idempotency WHERE attachment_id=? LIMIT 1", (attachment_id,)).fetchone()
            if uploaded:
                job.filename = uploaded[0]
        if idempotency_key:
            existing = conn.execute("SELECT job_json,fingerprint FROM media_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This key was used for different input.")
            job = TranscriptionJob.model_validate_json(existing["job_json"])
        else:
            conn.execute("INSERT INTO media_jobs VALUES (?,?,?,?,?,?,?,?)", (job.job_id, job.status,
                job.model_dump_json(), request.model_dump_json(), job.created_at.isoformat(), job.updated_at.isoformat(), idempotency_key, fingerprint))
            _event(conn, job, "Queued")
    key = task_key(job.job_id)
    if not existing:
        task = asyncio.create_task(_execute(job.job_id, request, routing))
        _tasks[key] = task
        task.add_done_callback(lambda finished: _tasks.pop(key, None))
    if wait and key in _tasks:
        try:
            await _tasks[key]
        except asyncio.CancelledError:
            await cancel(job.job_id)
            raise
        return require_job(job.job_id)
    return job

async def _execute(job_id, request, routing=None):
    from app.container import container
    job = require_job(job_id)
    if job.status in TERMINAL:
        return
    from app.local_models.runtime import runtime_context, runtime_progress, RuntimeConfig
    from app.contracts import TranscriptSegment
    token = runtime_context.set(RuntimeConfig.model_validate(job.model_snapshot.get("local_runtime", {})))
    def progress(message):
        if message.get("reset"):
            job.segments = []; job.progress = 0
            save(job, "AttemptRestarted")
            return
        job.progress = max(0.0, min(0.99, message["progress"]))
        job.segments.append(TranscriptSegment.model_validate(message["segment"]))
        save(job, "SegmentReady")
    progress_token = runtime_progress.set(progress)
    job.status, job.started_at = "running", now()
    save(job, "TranscriptionStarted")
    cancelled = False
    try:
        source = attachment_path(job.attachment_id)
        transcript = source if source.suffix.lower() in {".txt", ".md"} else attachment_path(f"{job.attachment_id}.txt")
        if transcript.is_file() and (source == transcript or not source.exists()):
            def read_transcript():
                with transcript.open("rb") as stream:
                    return stream.read(1024 * 1024 + 1)
            content = await asyncio.to_thread(read_transcript)
            if len(content) > 1024 * 1024:
                raise ApiError(413, "TRANSCRIPT_TOO_LARGE", "Transcript exceeds 1 MiB.")
            job.text, job.source = content.decode("utf-8"), "sidecar"
        else:
            result = await (routing or container.model_routing).transcribe(source, request.language, local_only=request.local_only)
            job.text, job.source, job.fallback_reason = result.text, result.source, result.fallback_reason
            job.segments = getattr(result, "segments", []) or []
            job.warnings.extend(getattr(result, "warnings", []) or [])
        if not job.text or not job.text.strip():
            raise ApiError(422, "TRANSCRIPT_EMPTY", "Transcript is empty.")
        if request.diarization:
            if job.segments:
                from app.local_models.runtime import runtime
                from app.providers.base import ProviderError
                try:
                    result = await runtime.infer("eres2netv2", "diarization", {"source": str(source.resolve()),
                        "segments": [s.model_dump() for s in job.segments]})
                    for segment, speaker in zip(job.segments, result["speakers"], strict=True):
                        segment.speaker = speaker
                    job.warnings.append("DIARIZATION_SEGMENT_LEVEL")
                    if result.get("unassigned_segments"):
                        job.warnings.append("DIARIZATION_PARTIAL")
                except ProviderError:
                    job.warnings.append("DIARIZATION_UNAVAILABLE")
            else:
                job.warnings.append("DIARIZATION_UNAVAILABLE")
        if request.word_timestamps:
            job.warnings.append("WORD_TIMESTAMPS_UNAVAILABLE")
        job.original_text, job.original_segments = job.text, [s.model_copy(deep=True) for s in job.segments]
        for original, replacement in request.terminology.items():
            if original and original != replacement and original in job.text:
                job.text = job.text.replace(original, replacement)
                for segment in job.segments:
                    segment.text = segment.text.replace(original, replacement)
                job.corrections.append({"original": original, "replacement": replacement, "source": "terminology_postprocessing"})
        job.status, job.progress = "completed", 1
    except asyncio.CancelledError:
        cancelled = True
        job.status, job.error_code = "cancelled", "TRANSCRIPTION_CANCELLED"
    except ApiError as exc:
        job.status, job.error_code, job.error_message = "failed", exc.code, exc.message
        job.fallback_reason = exc.details.get("fallback_reason")
    except Exception:
        job.status, job.error_code, job.error_message = "failed", "TRANSCRIPTION_FAILED", "Transcription could not be completed."
    job.completed_at = now()
    save(job, {"completed": "Completed", "cancelled": "Cancelled", "failed": "Failed"}[job.status])
    runtime_context.reset(token)
    runtime_progress.reset(progress_token)
    if cancelled:
        raise asyncio.CancelledError

async def cancel(job_id):
    job = require_job(job_id)
    if job.status in TERMINAL:
        return job
    task = _tasks.get(task_key(job_id))
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    job = require_job(job_id)
    if job.status not in TERMINAL:
        job.status, job.error_code, job.completed_at = "cancelled", "TRANSCRIPTION_CANCELLED", now()
        save(job, "Cancelled")
    return job

async def retry(job_id):
    if require_job(job_id).error_code == "MEDIA_PURGED":
        raise ApiError(409, "MEDIA_PURGED", "Purged jobs cannot be retried.")
    if require_job(job_id).status not in {"failed", "cancelled"}:
        raise ApiError(409, "TRANSCRIPTION_NOT_RETRYABLE", "Only failed or cancelled jobs can be retried.")
    with closing(connect()) as conn:
        raw = conn.execute("SELECT request_json FROM media_jobs WHERE job_id=?", (job_id,)).fetchone()[0]
    request = TranscriptionRequest.model_validate_json(raw)
    return await create_transcription(**request.model_dump(exclude={"idempotency_key"}), wait=False, previous_job_id=job_id)

def edit(job_id, request: TranscriptEditRequest):
    with closing(connect()) as conn, transaction(conn):
        row = conn.execute("SELECT job_json FROM media_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "Transcription job not found.")
        job = TranscriptionJob.model_validate_json(row[0])
        if job.status != "completed":
            raise ApiError(409, "TRANSCRIPT_NOT_READY", "Only completed transcripts can be edited.")
        if job.revision != request.revision:
            raise ApiError(409, "VERSION_CONFLICT", "Transcript has changed; reload before saving.")
        ids = [s.segment_id for s in request.segments]
        if len(ids) != len(set(ids)) or request.segments != sorted(request.segments, key=lambda s: s.start_time):
            raise ApiError(422, "INVALID_SEGMENTS", "Segments must have unique IDs and ordered timestamps.")
        conn.execute("INSERT INTO media_revisions VALUES (?,?,?)", (job_id, job.revision, job.model_dump_json()))
        job.text, job.segments, job.speaker_names = request.text, request.segments, request.speaker_names
        job.revision += 1
        job.updated_at = now()
        conn.execute("UPDATE media_jobs SET job_json=?,updated_at=? WHERE job_id=?", (job.model_dump_json(), job.updated_at.isoformat(), job_id))
        _event(conn, job, "Revised", {"revision": job.revision})
    return job
