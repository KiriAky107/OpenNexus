"""Note 服务：Markdown 文件读写 + 解析 + 索引编排。

Markdown 文件是笔记正文的持久化载体（Vault），SQLite/FTS5/向量是可重建索引。
本服务负责在两者之间保持一致：写文件后解析并写入元数据、FTS5 与向量。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app import repository
from app.config import get_settings
from app.contracts import Note, NoteBlock, NoteSummary
from app.errors import ApiError
from app.knowledge.parser import ParsedNote, parse_note
from app.retrieval.embedding import HashEmbeddingProvider
from app.retrieval.vectorstore import SqliteVecStore, VectorRecord

# 轻量实现实例（无状态，可直接复用）；接入真实模型后替换为对应 Provider
embedding = HashEmbeddingProvider()
vector_store = SqliteVecStore()


def _vault() -> Path:
    return get_settings().vault_path


def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", title).strip()
    return name or "untitled"


def _rel_path(folder: str | None, title: str) -> str:
    folder_part = folder.strip().strip("/") if folder else ""
    name = _safe_name(title)
    if not name.endswith(".md"):
        name += ".md"
    return f"{folder_part}/{name}" if folder_part else name


def _abs_path(rel_path: str) -> Path:
    return _vault() / rel_path


def _read_markdown(rel_path: str) -> str:
    path = _abs_path(rel_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_markdown(rel_path: str, markdown: str) -> None:
    path = _abs_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _delete_markdown(rel_path: str) -> None:
    path = _abs_path(rel_path)
    if path.exists():
        path.unlink()


async def index_note(parsed: ParsedNote) -> None:
    """把解析结果写入元数据 + FTS5 + 向量（三层可重建索引）。"""
    vectors = await embedding.embed_documents([block.content for block in parsed.blocks])
    repository.replace_note_metadata(
        note_id=parsed.note_id,
        title=parsed.title,
        file_path=parsed.file_path,
        folder=parsed.folder,
        tags=parsed.tags,
        created_at=parsed.created_at,
        updated_at=parsed.updated_at,
        blocks=parsed.blocks,
    )
    records = [
        VectorRecord(id=block.block_id, vector=vector)
        for block, vector in zip(parsed.blocks, vectors)
    ]
    await vector_store.upsert(records)
    repository.set_index_meta({"embedding_model": embedding.model_id, "embedding_dim": str(embedding.dim)})


async def create_note(*, title: str, markdown: str, folder: str | None, tags: list[str]) -> Note:
    rel_path = _rel_path(folder, title)
    _write_markdown(rel_path, markdown)
    now = datetime.now(timezone.utc)
    parsed = parse_note(
        markdown=markdown, file_path=rel_path, folder=folder or "", tags=tags,
        created_at=now, updated_at=now,
    )
    parsed.title = title  # 显式传入的 title 优先于正文推导（与 update_note 保持一致）
    await index_note(parsed)
    return _build_note(parsed.note_id, parsed.title, parsed.file_path, parsed.tags,
                       parsed.created_at, parsed.updated_at, parsed.blocks, markdown)


async def get_note(note_id: str) -> Note | None:
    record = repository.get_note_record(note_id)
    if record is None:
        return None
    markdown = _read_markdown(record.file_path)
    return _build_note(record.note_id, record.title, record.file_path, record.tags,
                       record.created_at, record.updated_at, record.blocks, markdown)


async def update_note(
    note_id: str, *, title: str | None = None, markdown: str | None = None, tags: list[str] | None = None
) -> Note:
    record = repository.get_note_record(note_id)
    if record is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id})

    new_md = _read_markdown(record.file_path) if markdown is None else markdown
    _write_markdown(record.file_path, new_md)

    now = datetime.now(timezone.utc)
    parsed = parse_note(
        markdown=new_md, file_path=record.file_path, folder=record.folder, tags=tags,
        created_at=record.created_at, updated_at=now,
    )
    if title is not None:
        parsed.title = title  # 显式传入的 title 覆盖正文推导结果

    await index_note(parsed)
    return _build_note(parsed.note_id, parsed.title, parsed.file_path, parsed.tags,
                       parsed.created_at, parsed.updated_at, parsed.blocks, new_md)


async def delete_note(note_id: str) -> bool:
    record = repository.get_note_record(note_id)
    if record is None:
        return False
    block_ids = repository.delete_note(note_id)
    await vector_store.delete(block_ids)
    _delete_markdown(record.file_path)
    return True


def list_notes(*, limit: int, offset: int, folder: str | None, tag: str | None) -> tuple[list[NoteSummary], int]:
    items, total = repository.list_note_summaries(limit=limit, offset=offset, folder=folder, tag=tag)
    return [NoteSummary(**item) for item in items], total


def _build_note(
    note_id: str, title: str, file_path: str, tags: list[str],
    created_at: datetime, updated_at: datetime, blocks: list[NoteBlock], markdown: str,
) -> Note:
    return Note(
        note_id=note_id,
        title=title,
        file_path=file_path,
        tags=tags,
        created_at=created_at,
        updated_at=updated_at,
        markdown=markdown,
        blocks=blocks,
    )
