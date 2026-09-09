"""幂等转录本导出，无需覆盖已编辑的笔记。"""
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
        options_hash = hashlib.sha256(options.model_copy(update={"update_existing": False}).model_dump_json(exclude={"update_existing"}).encode()).hexdigest()
        with closing(connect()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS media_note_baselines (note_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL)")
            previous = conn.execute("SELECT m.note_id,b.content_hash FROM media_notes m LEFT JOIN media_note_baselines b ON b.note_id=m.note_id WHERE m.job_id=? AND m.options_hash=? ORDER BY m.revision DESC LIMIT 1", (job_id, options_hash)).fetchone()
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
            # 保留 Vault 中的索引策略，包括以后的重建。
            lines = ["---", "embedding_local_only: true", "---", "", *lines]
        markdown = "\n".join(lines)
        if options.update_existing:
            if previous is None or previous[1] is None:
                raise ApiError(409, "NOTE_UPDATE_BASELINE_MISSING", "没有可安全更新的导出记录，请先创建新笔记。")
            current = await note_service.get_note(previous[0])
            if current is None:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "已导出笔记不存在。")
            # 如果 Vault 写入后链接失败，则恢复成功更新。
            if current.markdown == markdown:
                note = current
            else:
                note = await note_service.update_note(previous[0], markdown=markdown, expected_content_hash=previous[1])
        else:
            note = await _create_note(title, markdown, options, marker)
        with closing(connect()) as conn, transaction(conn):
            conn.execute("INSERT OR IGNORE INTO media_notes VALUES (?,?,?,?)", (job_id, job.revision, options_hash, note.note_id))
            conn.execute("INSERT OR REPLACE INTO media_note_baselines VALUES (?,?)", (note.note_id, hashlib.sha256(markdown.encode()).hexdigest()))
        return note


async def _create_note(title, markdown, options, marker):
    try:
        note = await note_service.create_note(title=title, markdown=markdown, folder=options.folder, tags=["转写"])
    except ApiError as exc:
        if exc.code != "RESOURCE_CONFLICT" or "note_id" not in exc.details:
            raise
        # 恢复笔记创建成功后、关联任务前发生的崩溃。
        note = await note_service.get_note(exc.details["note_id"])
        if note is None or marker not in note.markdown:
            raise
    return note
