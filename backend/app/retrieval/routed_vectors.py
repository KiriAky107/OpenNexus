"""Optional API embeddings, isolated from the stable hash/sqlite-vec index.

The runtime's model_id is the authoritative space ID (including provider URL,
endpoint, model and dimensions); equal dimensions alone never imply compatibility.
This phase uses a lazy, rebuildable SQLite side table instead of a schema migration.
Search scans only current blocks in one database snapshot and requires complete
coverage. Cosine ranking costs O(blocks * dimensions) with an O(top_k) heap; this
small-vault implementation should become a per-space ANN index at larger scale.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from app.database.db import connect, transaction
from app.errors import ApiError
from app.operation_logs import log_event
from app.retrieval.vectorstore import VectorHit
from app.retrieval.provenance import record_embedding
from app.retrieval.hybrid import rrf_fuse

logger = logging.getLogger(__name__)


class EmbeddingResult(Protocol):
    vectors: list[list[float]]
    source: str
    model_id: str
    dimensions: int
    fallback_reason: str | None


class EmbeddingRuntime(Protocol):
    async def embed(self, texts: list[str], *, local_only=False) -> EmbeddingResult: ...


@dataclass(frozen=True)
class RemoteEmbeddings:
    space_id: str
    dimensions: int
    vectors: list[list[float]]
    source: str = "api"


def get_model_routing() -> EmbeddingRuntime | None:
    """Lazy integration hook; tests can inject a runtime without any network I/O."""
    from app.container import container

    return getattr(container, "model_routing", None)


def _unit_vector(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions:
        raise ValueError("embedding dimension mismatch")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
        raise ValueError("embedding must be numeric")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("embedding must be finite")
    scale = max(abs(value) for value in vector)
    if scale == 0:
        raise ValueError("embedding must be nonzero")
    # Scaling first avoids overflow/underflow for finite but extreme API values.
    scaled = [value / scale for value in vector]
    norm = math.sqrt(math.fsum(value * value for value in scaled))
    return [value / norm for value in scaled]


async def embed_remote(texts: list[str], *, accept_local=False, strict=False, local_only=False) -> RemoteEmbeddings | None:
    """Return validated API vectors, or None to use the caller's local baseline.

    Do not use the runtime's local result: the caller may have injected its own
    embedding/store pair. Exception deliberately excludes cancellation.
    """
    if not texts:
        return None
    try:
        runtime = get_model_routing()
        if runtime is None:
            if strict:
                raise ApiError(503, "EMBEDDING_UNAVAILABLE", "Embedding 服务未就绪，请检查模型路由和本地运行环境。")
            return None
        result = await runtime.embed(texts, local_only=True) if local_only else await runtime.embed(texts)
        if result.source != "api" and not accept_local:
            record_embedding(fallback_reason=result.fallback_reason)
            return None
        if not isinstance(result.model_id, str) or not result.model_id or result.model_id == "hash-v1":
            raise ValueError("API embedding needs a distinct space ID")
        if type(result.dimensions) is not int or result.dimensions <= 0:
            raise ValueError("invalid embedding dimensions")
        if len(result.vectors) != len(texts):
            raise ValueError("embedding count mismatch")
        return RemoteEmbeddings(
            space_id=result.model_id,
            dimensions=result.dimensions,
            vectors=[_unit_vector(vector, result.dimensions) for vector in result.vectors],
            source=result.source,
        )
    except Exception as exc:
        log_event('vectors', 'embedding.failed', level='ERROR' if strict else 'WARNING', error=exc,
                  count=len(texts), fallback='none' if strict else 'local_index')
        # Avoid logging provider exceptions containing credentials or note text.
        record_embedding(fallback_reason="REMOTE_EMBEDDING_UNAVAILABLE")
        logger.warning("Remote embedding unavailable (%s); using local index", type(exc).__name__)
        if strict:
            if isinstance(exc, ApiError):
                raise
            raise ApiError(503, "EMBEDDING_UNAVAILABLE", "Embedding 调用失败或返回无效，请检查模型路由、API 和本地模型运行状态。") from exc
        return None


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routed_block_vectors (
            space_id TEXT NOT NULL,
            block_id TEXT NOT NULL REFERENCES blocks(block_id) ON DELETE CASCADE,
            dimensions INTEGER NOT NULL CHECK (dimensions > 0),
            vector TEXT NOT NULL,
            PRIMARY KEY (space_id, block_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS routed_block_vectors_block_id
        ON routed_block_vectors(block_id)
    """)


def store_remote(
    conn: sqlite3.Connection, block_ids: list[str], batch: RemoteEmbeddings | None,
) -> None:
    """Best-effort side-index write inside the caller's metadata transaction.

    A savepoint prevents partial remote batches and isolates storage failures from
    note saving. Replacing/deleting blocks cascades all old spaces automatically.
    """
    if batch is None:
        return
    try:
        conn.execute("SAVEPOINT routed_vectors_write")
        try:
            if len(block_ids) != len(batch.vectors):
                raise ValueError("block/vector count mismatch")
            _ensure_table(conn)
            conn.executemany(
                """INSERT INTO routed_block_vectors (space_id, block_id, dimensions, vector)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (space_id, block_id) DO UPDATE SET
                       dimensions = excluded.dimensions, vector = excluded.vector""",
                [
                    (batch.space_id, block_id, batch.dimensions, json.dumps(vector, allow_nan=False))
                    for block_id, vector in zip(block_ids, batch.vectors)
                ],
            )
        except BaseException:
            conn.execute("ROLLBACK TO routed_vectors_write")
            raise
        finally:
            conn.execute("RELEASE routed_vectors_write")
    except Exception as exc:
        logger.warning("Remote vector storage unavailable (%s); local index retained", type(exc).__name__)


async def search_remote(query: str, *, top_k: int, accept_local=False, strict=False) -> list[VectorHit] | None:
    """None means fallback, including any missing/invalid current-block vector.

    Read coverage and vectors together so concurrent note updates cannot produce
    an apparently complete subset. Never fill missing remote hits with local hits.
    """
    if accept_local:
        conn = connect()
        try:
            policies = {bool(row[0]) for row in conn.execute("SELECT DISTINCT embedding_local_only FROM blocks")}
        finally:
            conn.close()
        if True in policies:
            return await _search_partitioned(query, policies, top_k=top_k, strict=strict)
    batch = await embed_remote([query], accept_local=accept_local, strict=strict)
    if batch is None:
        return None

    record_embedding(attempted_space={"model_id": batch.space_id, "dimensions": batch.dimensions})
    try:
        conn = connect()
        try:
            with transaction(conn):
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'routed_block_vectors'"
                ).fetchone()
                if exists is None:
                    record_embedding(fallback_reason="REMOTE_INDEX_MISSING")
                    if not conn.execute("SELECT 1 FROM blocks LIMIT 1").fetchone():
                        return []
                    if strict:
                        raise ValueError("semantic index missing")
                    return None
                rows = conn.execute(
                    """SELECT b.block_id, r.vector
                       FROM blocks AS b
                       LEFT JOIN routed_block_vectors AS r
                         ON r.block_id = b.block_id AND r.space_id = ? AND r.dimensions = ?
                       ORDER BY b.block_id""",
                    (batch.space_id, batch.dimensions),
                )

                def hits():
                    for row in rows:
                        if row["vector"] is None:
                            raise ValueError("remote space has incomplete block coverage")
                        vector = _unit_vector(json.loads(row["vector"]), batch.dimensions)
                        score = math.fsum(a * b for a, b in zip(batch.vectors[0], vector))
                        yield VectorHit(id=row["block_id"], score=max(0.0, min(1.0, score)))

                try:
                    result = heapq.nlargest(top_k, hits(), key=lambda hit: hit.score)
                finally:
                    # Exceptions may retain the generator/traceback; finalize its
                    # cursor now so a subsequent rebuild can acquire a write lock.
                    rows.close()
                record_embedding(source=batch.source, model_id=batch.space_id,
                                 dimensions=batch.dimensions, fallback_reason=None)
                return result
        finally:
            conn.close()
    except Exception as exc:
        record_embedding(fallback_reason="REMOTE_INDEX_UNAVAILABLE")
        logger.debug("Remote vector search unavailable (%s); using local index", type(exc).__name__)
        if strict:
            raise ApiError(409, "SEMANTIC_INDEX_UNAVAILABLE",
                           "Embedding 已可用，但当前模型的向量索引缺失、不完整或已失效。请在「设置 → 索引与模型」中重建全部索引。",
                           {"model_id": batch.space_id, "dimensions": batch.dimensions, "source": batch.source}) from exc
        return None


async def _search_partitioned(query: str, policies: set[bool], *, top_k: int, strict: bool):
    """Embed per policy; rank each space independently and fuse ranks, not vectors."""
    batches = {}
    for policy in sorted(policies):
        batch = await embed_remote([query], accept_local=True, strict=strict, local_only=policy)
        if batch is None:
            return None
        batches[policy] = batch
    conn = connect()
    try:
        with transaction(conn):
            # Query vectors are ready before opening the single read snapshot.
            current = {bool(row[0]) for row in conn.execute("SELECT DISTINCT embedding_local_only FROM blocks")}
            if current != policies:
                raise ValueError("embedding policies changed while querying")
            ranked = []
            for policy, batch in batches.items():
                rows = conn.execute(
                    "SELECT b.block_id,r.vector FROM blocks b LEFT JOIN routed_block_vectors r "
                    "ON r.block_id=b.block_id AND r.space_id=? AND r.dimensions=? "
                    "WHERE b.embedding_local_only=? ORDER BY b.block_id",
                    (batch.space_id, batch.dimensions, int(policy)),
                )
                def hits():
                    for row in rows:
                        if row['vector'] is None:
                            raise ValueError("incomplete policy coverage")
                        vector = _unit_vector(json.loads(row['vector']), batch.dimensions)
                        score = math.fsum(a * b for a, b in zip(batch.vectors[0], vector))
                        yield VectorHit(id=row['block_id'], score=max(0.0, min(1.0, score)))
                try:
                    ranked.append(heapq.nlargest(top_k, hits(), key=lambda hit: hit.score))
                finally:
                    rows.close()
            spaces = [{"source": b.source, "model_id": b.space_id, "dimensions": b.dimensions,
                       "local_only": policy} for policy, b in batches.items()]
            record_embedding(source="mixed" if len({b.source for b in batches.values()}) > 1 else batch.source,
                             spaces=spaces, fallback_reason=None)
            if len(ranked) == 1:
                return ranked[0]
            fused = rrf_fuse([[hit.id for hit in group] for group in ranked])
            return [VectorHit(id=key, score=score) for key, score in
                    sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:top_k]]
    except Exception as exc:
        record_embedding(source="unavailable", fallback_reason="REMOTE_INDEX_UNAVAILABLE")
        if strict:
            raise ApiError(409, "SEMANTIC_INDEX_UNAVAILABLE", "部分索引分区缺失或已失效，请重建全部索引。") from exc
        return None
    finally:
        conn.close()
