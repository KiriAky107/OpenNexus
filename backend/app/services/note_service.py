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
from app.database.db import connect, transaction
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


def _normalize_folder(folder: str | None) -> str:
    """清洗 folder 为安全的相对目录，拒绝 `..`/`.`/绝对路径/盘符/空字节，防路径逃逸。"""
    if not folder:
        return ""
    if "\x00" in folder:
        raise ApiError(400, "INVALID_PATH", "folder must not contain NUL bytes", {"folder": folder})
    segments: list[str] = []
    for part in re.split(r"[\\/]+", folder):
        if part == "":
            continue
        if part in (".", ".."):
            raise ApiError(400, "INVALID_PATH", "folder must not contain '.' or '..'", {"folder": folder})
        if ":" in part:
            raise ApiError(400, "INVALID_PATH", "folder must be a relative path", {"folder": folder})
        segments.append(part)
    return "/".join(segments)


def _rel_path(folder: str | None, title: str) -> tuple[str, str]:
    """由 folder + title 生成安全的相对路径，返回 (rel_path, 清洗后的 folder)。"""
    clean_folder = _normalize_folder(folder)
    name = _safe_name(title)
    if not name.endswith(".md"):
        name += ".md"
    rel = f"{clean_folder}/{name}" if clean_folder else name
    return rel, clean_folder


def _abs_path(rel_path: str) -> Path:
    """把相对路径解析为 Vault 内的绝对路径；越界即报 400，杜绝路径逃逸。"""
    if not rel_path or "\x00" in rel_path:
        raise ApiError(400, "INVALID_PATH", "invalid file path", {"file_path": rel_path})
    root = _vault().resolve()
    candidate = (_vault() / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise ApiError(400, "INVALID_PATH", "path escapes vault", {"file_path": rel_path})
    return candidate


def _read_markdown(rel_path: str) -> str:
    path = _abs_path(rel_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_markdown(rel_path: str, markdown: str) -> None:
    path = _abs_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _create_markdown(rel_path: str, markdown: str) -> None:
    """排他创建 Markdown；目标已存在时返回资源冲突，不覆盖用户文件。"""
    path = _abs_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(markdown)
    except FileExistsError as exc:
        raise ApiError(
            409,
            "RESOURCE_CONFLICT",
            "a note already exists at this path",
            {"file_path": rel_path},
        ) from exc


def _delete_markdown(rel_path: str) -> None:
    path = _abs_path(rel_path)
    if path.exists():
        path.unlink()


async def index_note(parsed: ParsedNote) -> None:
    """把解析结果写入元数据 + FTS5 + 向量（三层可重建索引），单事务保证原子性。

    元数据与向量在同一连接、同一事务内提交，避免「新元数据已提交、向量写入失败」的
    半提交状态。替换元数据时拿到旧 block_id：清理已删除/内容变化的旧向量，只为新增
    block 写向量（内容未变的 block 其向量仍有效，无需重复写入）。
    """
    vectors = await embedding.embed_documents([block.content for block in parsed.blocks])
    conn = connect()
    try:
        with transaction(conn):
            old_block_ids = repository.replace_note_metadata(
                conn=conn,
                note_id=parsed.note_id,
                title=parsed.title,
                file_path=parsed.file_path,
                folder=parsed.folder,
                tags=parsed.tags,
                created_at=parsed.created_at,
                updated_at=parsed.updated_at,
                blocks=parsed.blocks,
            )
            old_ids = set(old_block_ids)
            new_ids = {block.block_id for block in parsed.blocks}
            stale_ids = [bid for bid in old_ids if bid not in new_ids]
            if stale_ids:
                await vector_store.delete(stale_ids, conn=conn)
            missing_ids = [bid for bid in new_ids if bid not in old_ids]
            records = [
                VectorRecord(id=block.block_id, vector=vector)
                for block, vector in zip(parsed.blocks, vectors)
                if block.block_id in missing_ids
            ]
            await vector_store.upsert(records, conn=conn)
            repository.set_index_meta(
                {"embedding_model": embedding.model_id, "embedding_dim": str(embedding.dim)},
                conn=conn,
            )
    finally:
        conn.close()


async def create_note(*, title: str, markdown: str, folder: str | None, tags: list[str]) -> Note:
    rel_path, clean_folder = _rel_path(folder, title)
    now = datetime.now(timezone.utc)
    parsed = parse_note(
        # 创建时空标签视为「未显式指定」，由 frontmatter 推导（创建无「清空」语义）
        markdown=markdown, file_path=rel_path, folder=clean_folder, tags=tags or None,
        created_at=now, updated_at=now,
    )
    parsed.title = title  # 显式传入的 title 优先于正文推导（与 update_note 保持一致）
    if repository.get_note_record(parsed.note_id) is not None:
        raise ApiError(
            409,
            "RESOURCE_CONFLICT",
            "a note already exists at this path",
            {"note_id": parsed.note_id, "file_path": rel_path},
        )
    _create_markdown(rel_path, markdown)
    try:
        await index_note(parsed)
    except BaseException:
        _delete_markdown(rel_path)  # 索引失败时回滚，避免「文件已写、索引缺失」的部分提交
        raise
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

    old_md = _read_markdown(record.file_path)
    new_md = old_md if markdown is None else markdown
    # PATCH 语义：tags=None 保持原标签；[] 清空；非空列表替换（区别于 create 的 frontmatter 推导）
    effective_tags = record.tags if tags is None else tags
    _write_markdown(record.file_path, new_md)

    now = datetime.now(timezone.utc)
    try:
        parsed = parse_note(
            markdown=new_md, file_path=record.file_path, folder=record.folder, tags=effective_tags,
            created_at=record.created_at, updated_at=now,
        )
        if title is not None:
            parsed.title = title  # 显式传入的 title 覆盖正文推导结果

        await index_note(parsed)
    except BaseException:
        _write_markdown(record.file_path, old_md)  # 索引失败时回滚正文，避免部分提交
        raise
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
