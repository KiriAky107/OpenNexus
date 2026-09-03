"""转写作业：API 优先，本地模型回退；保留已有 Host 文本入口。"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from uuid import uuid4

from app.contracts import TranscriptionJob
from app.errors import ApiError
from app.services.attachment_service import attachment_path

_jobs: OrderedDict[str, TranscriptionJob] = OrderedDict()
MAX_JOBS = 100


async def create_transcription(attachment_id: str, language: str | None = None, *, diarization: bool = False) -> TranscriptionJob:
    from app.container import container

    source = attachment_path(attachment_id)
    job = TranscriptionJob(
        job_id=f"transcription_{uuid4().hex}",
        attachment_id=attachment_id,
        status="processing",
        created_at=datetime.now(timezone.utc),
    )
    try:
        if diarization:
            # Speaker verification and diarization are different capabilities.
            raise ApiError(501, "DIARIZATION_NOT_IMPLEMENTED", "说话人分离将在阶段 F 接入，当前不能忽略 diarization 请求。")
        transcript = source if source.suffix.lower() in {".txt", ".md"} else attachment_path(f"{attachment_id}.txt")
        # A saved transcript remains an explicit import path, never faked ASR.
        if transcript.is_file() and (source == transcript or container.model_routing.configuration().transcription is None):
            with transcript.open("rb") as handle:
                content = handle.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ApiError(413, "TRANSCRIPT_TOO_LARGE", "Transcript exceeds 1 MiB.")
            job.text = content.decode("utf-8")
            if not job.text.strip():
                raise ApiError(422, "TRANSCRIPT_EMPTY", "Transcript is empty.")
            job.source = "sidecar"
        else:
            result = await container.model_routing.transcribe(source, language)
            job.text = result.text
            job.source = result.source
            job.fallback_reason = result.fallback_reason
        job.status = "completed"
    except ApiError as exc:
        job.status = "failed"
        job.error_code = exc.code
        job.error_message = exc.message
        job.fallback_reason = exc.details.get("fallback_reason")
    except (OSError, UnicodeError):
        job.status = "failed"
        job.error_code = "TRANSCRIPT_UNREADABLE"
        job.error_message = "Transcript could not be read."
    _jobs[job.job_id] = job
    while len(_jobs) > MAX_JOBS:
        _jobs.popitem(last=False)
    return job.model_copy(deep=True)


def get_transcription(job_id: str) -> TranscriptionJob | None:
    job = _jobs.get(job_id)
    return job.model_copy(deep=True) if job else None
