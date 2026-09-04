"""Idempotent transcript export without overwriting an edited note."""
import asyncio
import hashlib
from contextlib import closing

from app.config import get_settings
from app.database.db import connect, transaction
from app.errors import ApiError
from app.services import note_service
from app.services.transcription_service import require_job

_locks = {}


async def create_transcript_note(job_id, options):
    identity = (str(get_settings().db_path), job_id)
    lock = _locks.setdefault(identity, asyncio.Lock())
    async with lock:
        job = require_job(job_id)
        if job.status != "completed":
            raise ApiError(409, "TRANSCRIPT_NOT_READY", "Only completed transcripts can become notes.")
        options_hash = hashlib.sha256(options.model_dump_json().encode()).hexdigest()
        with closing(connect()) as conn:
            row = conn.execute("SELECT note_id FROM media_notes WHERE job_id=? AND revision=? AND options_hash=?",
                               (job_id, job.revision, options_hash)).fetchone()
        if row:
            return await note_service.get_note(row[0])
        marker = f"<!-- transcription:{job_id}:{job.revision}:{options_hash} -->"
        title = f"{options.title} · {job_id[-8:]}-r{job.revision}-{options_hash[:6]}"
        lines = [marker, f"# {options.title}", "", f"[源音频](/#/media?job={job_id})", ""]
        if job.segments:
            for segment in job.segments:
                prefix = []
                if options.include_timestamps:
                    seconds = segment.start_time
                    label = f"{int(seconds // 60):02}:{int(seconds % 60):02}"
                    prefix.append(f"[{label}](/#/media?job={job_id}&time={seconds})")
                if options.include_speakers and segment.speaker:
                    prefix.append(job.speaker_names.get(segment.speaker, segment.speaker))
                lines.append(" ".join([*prefix, segment.text]))
                lines.append("")
        else:
            lines.append(job.text or "")
        if job.local_only:
            # Persist the indexing policy in the Vault, including later rebuilds.
            lines = ["---", "embedding_local_only: true", "---", "", *lines]
        try:
            note = await note_service.create_note(title=title, markdown="\n".join(lines), folder=options.folder, tags=["转写"])
        except ApiError as exc:
            if exc.code != "RESOURCE_CONFLICT" or "note_id" not in exc.details:
                raise
            # Recover a crash between successful note creation and linking the job.
            note = await note_service.get_note(exc.details["note_id"])
            if note is None or marker not in note.markdown:
                raise
        with closing(connect()) as conn, transaction(conn):
            conn.execute("INSERT OR IGNORE INTO media_notes VALUES (?,?,?,?)", (job_id, job.revision, options_hash, note.note_id))
        return note
