"""VectorStore 统一接口与 sqlite-vec 实现。

vec0 虚拟表返回的 distance 是欧氏距离（非平方）。入库前向量已做 L2 归一化，
因此 distance² = 2(1-cos)，余弦相似度 = 1 - distance² / 2。
"""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import sqlite_vec

from app.database.db import connect_knowledge as connect, transaction


@dataclass
class VectorRecord:
    id: str
    vector: list[float]


@dataclass
class VectorHit:
    id: str
    score: float  # 余弦相似度 [0,1]


@runtime_checkable
class VectorStore(Protocol):
    """统一向量存储接口（与文档一致）。上层只依赖此抽象，不读 vec0 内部表。"""

    async def upsert(self, records: list[VectorRecord]) -> None: ...
    async def delete(self, ids: list[str]) -> None: ...
    async def search(self, vector: list[float], *, top_k: int) -> list[VectorHit]: ...
    async def count(self) -> int: ...


class SqliteVecStore:
    """sqlite-vec 默认实现。"""

    async def upsert(self, records: list[VectorRecord], *, conn: sqlite3.Connection | None = None) -> None:
        if not records:
            return
        owns = conn is None
        conn = conn or connect()
        try:
            with transaction(conn) if owns else nullcontext():
                for record in records:
                    # vec0 不支持 UPDATE，采用 delete-then-insert 实现幂等 upsert，避免主键冲突
                    conn.execute("DELETE FROM vec_blocks WHERE block_id = ?", (record.id,))
                    conn.execute(
                        "INSERT INTO vec_blocks (block_id, embedding) VALUES (?, ?)",
                        (record.id, sqlite_vec.serialize_float32(record.vector)),
                    )
        finally:
            if owns:
                conn.close()

    async def delete(self, ids: list[str], *, conn: sqlite3.Connection | None = None) -> None:
        if not ids:
            return
        owns = conn is None
        conn = conn or connect()
        try:
            with transaction(conn) if owns else nullcontext():
                for bid in ids:
                    conn.execute("DELETE FROM vec_blocks WHERE block_id = ?", (bid,))
        finally:
            if owns:
                conn.close()

    async def search(self, vector: list[float], *, top_k: int) -> list[VectorHit]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT block_id, distance FROM vec_blocks WHERE embedding MATCH ? AND k = ?",
                (sqlite_vec.serialize_float32(vector), top_k),
            ).fetchall()
            return [
                VectorHit(id=row["block_id"], score=max(0.0, 1.0 - row["distance"] ** 2 / 2.0))
                for row in rows
            ]
        finally:
            conn.close()

    async def clear(self, *, conn: sqlite3.Connection | None = None) -> None:
        owns = conn is None
        conn = conn or connect()
        try:
            with transaction(conn) if owns else nullcontext():
                conn.execute("DELETE FROM vec_blocks")
        finally:
            if owns:
                conn.close()

    async def count(self) -> int:
        conn = connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM vec_blocks").fetchone()[0]
        finally:
            conn.close()
