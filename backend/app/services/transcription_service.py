"""转写适配层；第一阶段消费文本附件或桌面 Host 预生成的旁路文本。"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.contracts import TranscriptionJob
from app.services.attachment_service import attachment_path

_jobs: OrderedDict[str, TranscriptionJob] = OrderedDict()
MAX_JOBS = 100


def create_transcription(attachment_id: str, language: str | None = None) -> TranscriptionJob:
    # TODO(ai-core): 第二阶段接入本地 ASR 队列后，保留相同 Job 契约替换此同步降级实现。
    del language  # 预生成 transcript 暂不需要语言识别。
    source = attachment_path(attachment_id)
    transcript = source if source.suffix.lower() in {".txt", ".md"} else Path(f"{source}.txt")
    job = TranscriptionJob(
        job_id=f"transcription_{uuid4().hex}",
        attachment_id=attachment_id,
        status="completed" if transcript.is_file() else "failed",
        text=transcript.read_text(encoding="utf-8") if transcript.is_file() else None,
        error_code=None if transcript.is_file() else "TRANSCRIPTION_BACKEND_UNAVAILABLE",
        error_message=(
            None
            if transcript.is_file()
            else "No host-generated transcript is available; local speech models are phase two."
        ),
        created_at=datetime.now(timezone.utc),
    )
    _jobs[job.job_id] = job
    while len(_jobs) > MAX_JOBS:
        _jobs.popitem(last=False)
    return job.model_copy(deep=True)


def get_transcription(job_id: str) -> TranscriptionJob | None:
    job = _jobs.get(job_id)
    return job.model_copy(deep=True) if job else None
