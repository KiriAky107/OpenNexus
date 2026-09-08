"""SQLite Repository：笔记元数据、Block 与 FTS5 的读写。

向量（vec_blocks）不在这里处理，交给 Retrieval 基础设施层的 VectorStore（见
app/retrieval/vectorstore.py）。本层只负责 notes / blocks / blocks_fts 三张表的访问，
返回领域记录（NoteRecord / BlockHit / FtsHit），不负责业务编排。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime

from app.contracts import NoteBlock
from app.database.db import connect_knowledge as connect, transaction
from app.textutils import segment


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class NoteRecord:
    note_id: str
    title: str
    file_path: str
    folder: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    blocks: list[NoteBlock] = field(default_factory=list)


@dataclass
class BlockHit:
    """检索时返回的完整 Block 上下文，用于组装 Citation 与 metadata 过滤。"""

    block_id: str
    note_id: str
    title: str
    file_path: str
    folder: str
    heading_path: list[str]
    content: str
    start_offset: int
    end_offset: int
    tags: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass
class FtsHit:
    block_id: str
    note_id: str
    bm25: float


@dataclass(frozen=True, slots=True)
class NoteLocation:
    note_id: str
    title: str
    file_path: str
    folder: str


def replace_note_metadata(
    *,
    conn: sqlite3.Connection,
    note_id: str,
    title: str,
    file_path: str,
    folder: str,
    tags: list[str],
    created_at: datetime,
    updated_at: datetime,
    blocks: list[NoteBlock],
) -> list[str]:
    """整体替换一条笔记的元数据、Block 与 FTS5 索引。

    不在此处开启/提交事务：由调用方（index_note）在同一连接上把「元数据 + 向量」包进
    单个事务，保证原子性。返回替换前的旧 block_id 列表，供调用方清理失效向量。
    """
    old_block_ids = [
        row["block_id"]
        for row in conn.execute("SELECT block_id FROM blocks WHERE note_id = ?", (note_id,))
    ]
    conn.execute(
        """
        INSERT INTO notes (note_id, title, file_path, folder, tags, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            title = excluded.title,
            file_path = excluded.file_path,
            folder = excluded.folder,
            tags = excluded.tags,
            updated_at = excluded.updated_at
        """,
        (note_id, title, file_path, folder, json.dumps(tags, ensure_ascii=False),
         _iso(created_at), _iso(updated_at)),
    )
    conn.execute("DELETE FROM blocks WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM blocks_fts WHERE note_id = ?", (note_id,))
    for position, block in enumerate(blocks):
        conn.execute(
            """
            INSERT INTO blocks
                (block_id, note_id, heading_path, start_offset, end_offset,
                 content, content_hash, token_count, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (block.block_id, note_id, json.dumps(block.heading_path, ensure_ascii=False),
             block.start_offset, block.end_offset, block.content,
             block.content_hash, block.token_count, position),
        )
        # FTS5 存分词后的可检索文本；原文仍由 blocks.content 保留用于展示
        conn.execute(
            "INSERT INTO blocks_fts (block_id, note_id, heading_path, content) VALUES (?, ?, ?, ?)",
            (block.block_id, note_id, segment(" ".join(block.heading_path)), segment(block.content)),
        )
    return old_block_ids


def delete_note(
    note_id: str, *, conn: sqlite3.Connection | None = None
) -> list[str]:
    """删除笔记及其 Block、FTS5 索引；返回被删除的 block_id 供向量层清理。"""
    owns = conn is None
    conn = conn or connect()
    try:
        block_ids = [
            row["block_id"]
            for row in conn.execute("SELECT block_id FROM blocks WHERE note_id = ?", (note_id,))
        ]
        with transaction(conn) if owns else nullcontext():
            conn.execute("DELETE FROM blocks_fts WHERE note_id = ?", (note_id,))
            conn.execute("DELETE FROM notes WHERE note_id = ?", (note_id,))  # blocks 级联删除
        return block_ids
    finally:
        if owns:
            conn.close()


def get_note_record(note_id: str) -> NoteRecord | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM notes WHERE note_id = ?", (note_id,)).fetchone()
        if row is None:
            return None
        blocks = [
            _block_from_row(b)
            for b in conn.execute("SELECT * FROM blocks WHERE note_id = ? ORDER BY position", (note_id,))
        ]
        return NoteRecord(
            note_id=row["note_id"],
            title=row["title"],
            file_path=row["file_path"],
            folder=row["folder"],
            tags=json.loads(row["tags"] or "[]"),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            blocks=blocks,
        )
    finally:
        conn.close()


def list_note_summaries(
    *, limit: int = 50, offset: int = 0, folder: str | None = None, tag: str | None = None
) -> tuple[list, int]:
    conn = connect()
    try:
        where: list[str] = []
        params: list[str] = []
        if folder:
            where.append("folder = ?")
            params.append(folder)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(notes.tags) AS j WHERE j.value = ?)")
            params.append(tag)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total = conn.execute(f"SELECT COUNT(*) FROM notes {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM notes {where_sql} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        items = [
            {
                "note_id": r["note_id"],
                "title": r["title"],
                "file_path": r["file_path"],
                "tags": json.loads(r["tags"] or "[]"),
                "created_at": _parse_dt(r["created_at"]),
                "updated_at": _parse_dt(r["updated_at"]),
            }
            for r in rows
        ]
        return items, total
    finally:
        conn.close()


def fts_search(match: str, limit: int = 100) -> list[FtsHit]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT block_id, note_id, bm25(blocks_fts) AS rank
            FROM blocks_fts
            WHERE blocks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        return [FtsHit(block_id=r["block_id"], note_id=r["note_id"], bm25=r["rank"]) for r in rows]
    finally:
        conn.close()


def list_note_locations(*, conn: sqlite3.Connection | None = None) -> list[NoteLocation]:
    """返回 Workspace 构树和目录事务所需的最小笔记位置集合。"""

    owns = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            "SELECT note_id, title, file_path, folder FROM notes ORDER BY file_path"
        ).fetchall()
        return [
            NoteLocation(
                note_id=row["note_id"],
                title=row["title"],
                file_path=row["file_path"],
                folder=row["folder"],
            )
            for row in rows
        ]
    finally:
        if owns:
            conn.close()


def update_note_location(
    *,
    conn: sqlite3.Connection,
    note_id: str,
    title: str,
    file_path: str,
    folder: str,
    updated_at: datetime,
) -> None:
    """更新文件位置和展示标题；Block/FTS/向量内容不变，无需重新生成。"""

    cursor = conn.execute(
        """
        UPDATE notes
        SET title = ?, file_path = ?, folder = ?, updated_at = ?
        WHERE note_id = ?
        """,
        (title, file_path, folder, _iso(updated_at), note_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(note_id)


_FTS_FROM = """
        FROM blocks_fts
        JOIN blocks AS b ON b.block_id = blocks_fts.block_id
        JOIN notes AS n ON n.note_id = b.note_id
    """


def _fts_where(
    match: str,
    folders: list[str],
    note_ids: list[str],
    tags: list[str],
    created_from: datetime | None,
    created_to: datetime | None,
    updated_from: datetime | None,
    updated_to: datetime | None,
) -> tuple[str, list[object]]:
    """构建 FTS 过滤 WHERE 子句（不含 WHERE 关键字），返回 (where_sql, params)。

    fts_search_page 与 fts_score_bounds 共用，保证计数与取数口径一致。
    """
    where = ["blocks_fts MATCH ?"]
    params: list[object] = [match]

    def add_in(column: str, values: list[str]) -> None:
        if not values:
            return
        placeholders = ",".join("?" * len(values))
        where.append(f"{column} IN ({placeholders})")
        params.extend(values)

    add_in("n.folder", folders)
    add_in("n.note_id", note_ids)
    if tags:
        placeholders = ",".join("?" * len(tags))
        where.append(
            f"EXISTS (SELECT 1 FROM json_each(n.tags) AS tag WHERE tag.value IN ({placeholders}))"
        )
        params.extend(tags)

    for column, lower, upper in (
        ("n.created_at", created_from, created_to),
        ("n.updated_at", updated_from, updated_to),
    ):
        if lower is not None:
            where.append(f"julianday({column}) >= julianday(?)")
            params.append(_iso(lower))
        if upper is not None:
            where.append(f"julianday({column}) <= julianday(?)")
            params.append(_iso(upper))

    return " AND ".join(where), params


def fts_search_page(
    *,
    match: str,
    limit: int,
    offset: int,
    folders: list[str],
    note_ids: list[str],
    tags: list[str],
    created_from: datetime | None,
    created_to: datetime | None,
    updated_from: datetime | None,
    updated_to: datetime | None,
    bm25_max: float | None = None,
) -> tuple[list[FtsHit], int]:
    """执行带元数据过滤的 FTS 精确分页，并返回过滤后的完整命中数。

    bm25_max 非空时按 bm25 截止值过滤（用于阈值过滤的精确分页），计数与取数同口径。
    """
    where_sql, params = _fts_where(
        match, folders, note_ids, tags,
        created_from, created_to, updated_from, updated_to,
    )
    if bm25_max is not None:
        where_sql += " AND bm25(blocks_fts) <= ?"
        params.append(bm25_max)

    conn = connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) {_FTS_FROM} WHERE {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT blocks_fts.block_id, blocks_fts.note_id, bm25(blocks_fts) AS rank
            {_FTS_FROM}
            WHERE {where_sql}
            ORDER BY rank
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return (
            [FtsHit(block_id=row["block_id"], note_id=row["note_id"], bm25=row["rank"])
             for row in rows],
            total,
        )
    finally:
        conn.close()


def fts_score_bounds(
    *,
    match: str,
    folders: list[str],
    note_ids: list[str],
    tags: list[str],
    created_from: datetime | None,
    created_to: datetime | None,
    updated_from: datetime | None,
    updated_to: datetime | None,
) -> tuple[float, float] | None:
    """返回 metadata 过滤后的 FTS 命中集里 bm25 的 (min, max)，无命中时返回 None。

    用于阈值过滤：min-max 归一化是 bm25 的线性函数，据此可把阈值换算为 bm25 截止值。
    """
    where_sql, params = _fts_where(
        match, folders, note_ids, tags,
        created_from, created_to, updated_from, updated_to,
    )
    conn = connect()
    try:
        # bm25() 不能作为聚合函数参数，也不能用在被聚合的子查询里；改用 ORDER BY 取首尾两行
        lo_row = conn.execute(
            f"SELECT bm25(blocks_fts) AS rank {_FTS_FROM} WHERE {where_sql}"
            " ORDER BY rank ASC LIMIT 1",
            params,
        ).fetchone()
        if lo_row is None or lo_row["rank"] is None:
            return None
        hi_row = conn.execute(
            f"SELECT bm25(blocks_fts) AS rank {_FTS_FROM} WHERE {where_sql}"
            " ORDER BY rank DESC LIMIT 1",
            params,
        ).fetchone()
        return (float(lo_row["rank"]), float(hi_row["rank"]))
    finally:
        conn.close()


def get_block_hits(block_ids: list[str]) -> list[BlockHit]:
    if not block_ids:
        return []
    conn = connect()
    try:
        placeholders = ",".join("?" * len(block_ids))
        rows = conn.execute(
            f"""
            SELECT b.block_id, b.note_id, b.heading_path, b.start_offset, b.end_offset, b.content,
                   n.title, n.file_path, n.folder, n.tags, n.created_at, n.updated_at
            FROM blocks b
            JOIN notes n ON n.note_id = b.note_id
            WHERE b.block_id IN ({placeholders})
            """,
            block_ids,
        ).fetchall()
        return [_block_hit_from_row(r) for r in rows]
    finally:
        conn.close()


def set_index_meta(
    kv: dict[str, str], *, conn: sqlite3.Connection | None = None
) -> None:
    """写入索引元信息；传入连接时加入调用方现有事务。"""
    owns = conn is None
    conn = conn or connect()
    try:
        with transaction(conn) if owns else nullcontext():
            for key, value in kv.items():
                conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", (key, value))
    finally:
        if owns:
            conn.close()


def get_index_meta() -> dict[str, str]:
    conn = connect()
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM index_meta")}
    finally:
        conn.close()


def clear_all(*, conn: sqlite3.Connection | None = None) -> None:
    """Clear rebuildable metadata using the caller's transaction when provided."""
    owns = conn is None
    conn = conn or connect()
    try:
        with transaction(conn) if owns else nullcontext():
            conn.execute("DELETE FROM blocks_fts")
            conn.execute("DELETE FROM blocks")
            conn.execute("DELETE FROM notes")
    finally:
        if owns:
            conn.close()


def stats() -> dict[str, int]:
    conn = connect()
    try:
        notes = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
        blocks = conn.execute("SELECT COUNT(*) AS c FROM blocks").fetchone()["c"]
        return {"notes": notes, "blocks": blocks}
    finally:
        conn.close()


def _block_from_row(row) -> NoteBlock:
    return NoteBlock(
        block_id=row["block_id"],
        note_id=row["note_id"],
        heading_path=json.loads(row["heading_path"] or "[]"),
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        content=row["content"],
        content_hash=row["content_hash"],
        token_count=row["token_count"],
    )


def _block_hit_from_row(row) -> BlockHit:
    return BlockHit(
        block_id=row["block_id"],
        note_id=row["note_id"],
        title=row["title"],
        file_path=row["file_path"],
        folder=row["folder"],
        heading_path=json.loads(row["heading_path"] or "[]"),
        content=row["content"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        tags=json.loads(row["tags"] or "[]"),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )
