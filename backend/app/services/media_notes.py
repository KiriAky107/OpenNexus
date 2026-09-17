"""幂等转录本导出，无需覆盖已编辑的笔记。"""
import asyncio
import hashlib
from contextlib import closing

from app.config import get_settings
from app.contracts import Message, MessageRole, ModelRequest, TranscriptNoteRequest
from app.database.db import connect, transaction
from app.errors import ApiError
from app.providers.base import ProviderError
from app.providers.registry import ProviderNotFoundError
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


def _transcript_text(job) -> str:
    if job.segments:
        rows = []
        for segment in job.segments:
            speaker = job.speaker_names.get(segment.speaker, segment.speaker) if segment.speaker else ""
            stamp = f"{int(segment.start_time // 60):02}:{int(segment.start_time % 60):02}"
            rows.append(f"[{stamp}] {speaker}：{segment.text}" if speaker else f"[{stamp}] {segment.text}")
        return "\n".join(rows)
    return job.text or ""


def _chunks(text: str, limit: int = 12000) -> list[str]:
    """按段落切分长转录，避免在中间截断句子。"""
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        if len(paragraph) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.extend(paragraph[index:index + limit] for index in range(0, len(paragraph), limit))
            continue
        current.append(paragraph)
        size += len(paragraph) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _complete(provider_id: str, model: str, system: str, content: str) -> str:
    from app.container import container
    try:
        provider = container.providers.get(provider_id).adapter
    except ProviderNotFoundError as exc:
        raise ApiError(404, "PROVIDER_NOT_FOUND", "所选模型提供商不存在或未启用。",
                       {"provider_id": provider_id}) from exc
    try:
        turn = await provider.complete(ModelRequest(
            provider_id=provider_id,
            model=model,
            system=system,
            messages=[Message(role=MessageRole.user, content=content)],
            temperature=0.2,
        ))
    except ProviderError as exc:
        raise ApiError(502, exc.code, exc.message, {"provider_id": provider_id}) from exc
    if not turn.text or not turn.text.strip():
        raise ApiError(502, "KNOWLEDGE_NOTE_EMPTY", "模型没有返回知识点笔记。")
    return turn.text.strip().removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()


async def _knowledge_markdown(job, provider_id: str, model: str, title: str) -> str:
    transcript = _transcript_text(job)
    if not transcript.strip():
        raise ApiError(409, "TRANSCRIPT_EMPTY", "转录内容为空，无法提取知识点。")
    system = (
        "你是一名严谨的课程笔记整理助手。只能依据提供的转录内容整理，不补写未出现的事实。"
        "输出中文 Markdown 正文，使用清晰的二级、三级标题；包含课程主题、核心概念、关键论证或步骤、"
        "重要例子、待复习问题。合并口语重复，保留专业术语和必要条件。不要使用代码围栏，也不要写处理说明。"
    )
    parts = _chunks(transcript)
    summaries: list[str] = []
    for index, part in enumerate(parts, 1):
        summaries.append(await _complete(
            provider_id, model, system,
            f"这是课程转录的第 {index}/{len(parts)} 部分。请提取可供最终整合的知识点：\n\n{part}",
        ))
    if len(summaries) == 1:
        body = summaries[0]
    else:
        body = await _complete(
            provider_id, model, system,
            "请将以下分段知识点合并成一篇完整课程笔记，消除重复并保持逻辑顺序：\n\n"
            + "\n\n".join(f"### 分段 {index}\n{summary}" for index, summary in enumerate(summaries, 1)),
        )
    return "\n".join([
        f"<!-- knowledge-note:{job.job_id}:{job.revision}:{provider_id}:{model} -->",
        f"# {title}", "", f"[查看完整转录稿](/#/media?job={job.job_id})", "", body,
    ])


async def create_transcript_artifacts(job_id, options):
    """为完成的转录生成可回听的全文和模型整理的知识点笔记。"""
    transcript_options = TranscriptNoteRequest(
        title=options.title,
        folder=options.folder,
        update_existing=options.update_existing,
        include_timestamps=options.include_timestamps,
        include_speakers=options.include_speakers,
    )
    transcript_note = await create_transcript_note(job_id, transcript_options)
    job = require_job(job_id)
    knowledge_title = options.knowledge_title or f"{options.title} · 知识点"
    identity = (str(get_settings().db_path), job_id, "knowledge")
    lock = _locks.setdefault(identity, asyncio.Lock())
    async with lock:
        signature = "knowledge:" + hashlib.sha256(options.model_copy(update={
            "update_existing": False,
            "knowledge_title": knowledge_title,
        }).model_dump_json(exclude={"update_existing"}).encode()).hexdigest()
        with closing(connect()) as conn:
            row = conn.execute(
                "SELECT note_id FROM media_notes WHERE job_id=? AND revision=? AND options_hash=?",
                (job_id, job.revision, signature),
            ).fetchone()
        if row:
            knowledge_note = await note_service.get_note(row[0])
            if knowledge_note is not None:
                return {"transcript": transcript_note, "knowledge_note": knowledge_note}
        markdown = await _knowledge_markdown(job, options.provider_id, options.model, knowledge_title)
        note_title = f"{knowledge_title} · {job_id[-8:]}-r{job.revision}-{signature[-6:]}"
        knowledge_note = await note_service.create_note(
            title=note_title,
            markdown=markdown,
            folder=options.folder,
            tags=["课程笔记", "知识点"],
        )
        with closing(connect()) as conn, transaction(conn):
            conn.execute("INSERT OR IGNORE INTO media_notes VALUES (?,?,?,?)",
                         (job_id, job.revision, signature, knowledge_note.note_id))
        return {"transcript": transcript_note, "knowledge_note": knowledge_note}


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
