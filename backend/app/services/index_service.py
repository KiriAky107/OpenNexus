"""索引服务：后台重建、快照校验与原子替换，不在模型计算期间锁住笔记编辑。"""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import repository
from app.config import get_settings
from app.contracts import IndexJob, IndexRebuildRequest, IndexStatus
from app.errors import ApiError
from app.knowledge.parser import parse_note
from app.services.note_service import index_note, prepare_note_index
from app.database.db import connect, transaction
from app.services.coordination import _vault_mutation_lock
from app.retrieval.vectorstore import SqliteVecStore
from app.local_models.runtime import LocalEmbedding
from app.services import note_service

vector_store = SqliteVecStore()

_jobs: dict[str, IndexJob] = {}
_active_job_id: str | None = None
_last_completed_at: datetime | None = None
_last_error: str | None = None
MAX_JOBS = 100
_background_task: asyncio.Task | None = None
_logger = logging.getLogger(__name__)


def _remember_job(job: IndexJob) -> None:
    _jobs[job.job_id] = job
    while len(_jobs) > MAX_JOBS:
        oldest = next(iter(_jobs))
        _jobs.pop(oldest, None)


def _scan_vault() -> list[tuple[str, str, str, datetime, datetime]]:
    """扫描 Vault 下所有 Markdown，返回 (rel_path, folder, markdown, created, updated)。

    先读入内存：若文件读取失败，rebuild 尚未清空旧索引，不会造成数据损失。
    """
    vault = get_settings().vault_path.resolve()
    result: list[tuple[str, str, str, datetime, datetime]] = []
    if not vault.exists():
        return result
    for path in sorted(vault.rglob("*.md")):
        resolved = path.resolve()
        if not resolved.is_relative_to(vault):
            continue
        rel = path.relative_to(vault).as_posix()
        folder = path.relative_to(vault).parent.as_posix()
        if folder == ".":
            folder = ""
        stat = resolved.stat()
        created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        result.append((rel, folder, resolved.read_text(encoding="utf-8"), created, updated))
    return result


async def rebuild(request: IndexRebuildRequest) -> IndexJob:
    global _active_job_id, _last_completed_at, _last_error
    if _active_job_id is not None:
        raise ApiError(409, "INDEX_BUSY", "索引正在后台计算，请稍后重试。")
    job_id = "job_" + uuid4().hex[:12]
    # 增量重建（scope != all 或指定 note_ids）尚未实现，明确拒绝而非静默全量重建
    if request.scope != "all" or request.note_ids:
        raise ApiError(
            400,
            "UNSUPPORTED_SCOPE",
            "only full rebuild (scope='all' with empty note_ids) is supported",
            {"scope": request.scope, "note_ids": request.note_ids},
        )

    docs = _scan_vault()
    saved_records = {key: repository.get_note_record(key) for key in _pending_notes()}
    saved_paths = {record.file_path: record for record in saved_records.values() if record is not None}

    _active_job_id = job_id
    _last_error = None
    _remember_job(IndexJob(
        job_id=job_id, status="running", scope=request.scope,
        created_at=datetime.now(timezone.utc),
    ))
    try:
        prepared_notes = []
        semantic_spaces = {}
        for rel, folder, markdown, created, updated in docs:
            parsed = parse_note(
                markdown=markdown, file_path=rel, folder=folder, tags=None,
                created_at=created, updated_at=updated,
            )
            if saved := saved_paths.get(rel):
                parsed = parse_note(markdown=markdown, file_path=rel, folder=folder, tags=saved.tags,
                    created_at=saved.created_at, updated_at=saved.updated_at, note_id=saved.note_id)
                parsed.title = saved.title
            prepared = await prepare_note_index(parsed, strict=True) if isinstance(note_service.embedding, LocalEmbedding) else await prepare_note_index(parsed)
            if isinstance(note_service.embedding, LocalEmbedding) and parsed.blocks:
                batch = prepared[1]
                if batch is None:
                    raise ApiError(503, "EMBEDDING_UNAVAILABLE", "Embedding 未生成向量，重建已停止，原索引已保留。")
                space = (batch.space_id, batch.dimensions)
                policy = parsed.embedding_local_only
                if policy in semantic_spaces and semantic_spaces[policy] != space:
                    raise ApiError(409, "EMBEDDING_SPACE_CHANGED", "重建期间 Embedding 模型发生切换，原索引已保留，请待模型服务稳定后重试。")
                semantic_spaces[policy] = space
            prepared_notes.append((parsed, prepared))
        # All network/model awaits precede the transaction. The concrete SQLite
        # methods below complete synchronously despite their async interfaces.
        async with _vault_mutation_lock:
            if _scan_vault() != docs or saved_records != {key: repository.get_note_record(key) for key in _pending_notes()}:
                raise ApiError(409, "INDEX_SNAPSHOT_CHANGED", "笔记在计算期间发生变化，稍后重新计算。")
            conn = connect()
            try:
                with transaction(conn):
                    task_note_links = dict(conn.execute(
                        "SELECT task_id, note_id FROM tasks WHERE note_id IS NOT NULL"
                    ).fetchall())
                    media_links = conn.execute("SELECT job_id,revision,options_hash,note_id FROM media_notes").fetchall()
                    repository.clear_all(conn=conn)
                    await vector_store.clear(conn=conn)
                    for parsed, prepared in prepared_notes:
                        await index_note(parsed, prepared=prepared, conn=conn)
                    for policy, space in semantic_spaces.items():
                        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='routed_block_vectors'").fetchone()
                        missing = not exists or conn.execute(
                            "SELECT 1 FROM blocks b LEFT JOIN routed_block_vectors r "
                            "ON r.block_id=b.block_id AND r.space_id=? AND r.dimensions=? "
                            "WHERE b.embedding_local_only=? AND r.block_id IS NULL LIMIT 1", (*space, int(policy)),
                        ).fetchone()
                        if missing:
                            raise ApiError(500, "SEMANTIC_INDEX_WRITE_FAILED", "向量索引写入失败，原索引已保留，请检查数据库和磁盘状态。")
                    for task_id, note_id in task_note_links.items():
                        conn.execute(
                            "UPDATE tasks SET note_id = ? WHERE task_id = ? "
                            "AND EXISTS (SELECT 1 FROM notes WHERE note_id = ?)",
                            (note_id, task_id, note_id),
                        )
                    for link in media_links:
                        conn.execute("INSERT OR IGNORE INTO media_notes SELECT ?,?,?,? WHERE EXISTS (SELECT 1 FROM notes WHERE note_id=?)",
                                     (*link, link["note_id"]))
                    repository.set_index_meta({"workspace_vectors_pending": "0"}, conn=conn)
            finally:
                conn.close()
    except BaseException as exc:
        _remember_job(IndexJob(
            job_id=job_id, status="failed", scope=request.scope,
            created_at=datetime.now(timezone.utc),
        ))
        _last_error = str(exc)
        raise
    finally:
        _active_job_id = None

    job = IndexJob(job_id=job_id, status="completed", scope=request.scope, created_at=datetime.now(timezone.utc))
    _remember_job(job)
    _last_completed_at = job.created_at
    if _pending_notes():
        schedule_workspace_rebuild()
    return job


def get_status() -> IndexStatus:
    counts = repository.stats()
    vector_refresh_required = repository.get_index_meta().get('workspace_vectors_pending') == '1' or bool(_pending_notes())
    if _active_job_id is not None:
        return IndexStatus(status="running", pending_jobs=0, active_job_id=_active_job_id, vector_refresh_required=vector_refresh_required,
                           total_notes=counts["notes"], total_blocks=counts["blocks"])
    return IndexStatus(
        vector_refresh_required=vector_refresh_required,
        total_notes=counts["notes"], total_blocks=counts["blocks"],
        status="failed" if _last_error else "idle",
        pending_jobs=0,
        last_completed_at=_last_completed_at,
        error_message=_last_error,
    )


def get_job(job_id: str) -> IndexJob | None:
    return _jobs.get(job_id)


def schedule_workspace_rebuild() -> None:
    """单进程去重；任务失败保留待重建标记，重新打开 Vault 可重试。"""
    global _background_task
    if _background_task is not None and not _background_task.done():
        return
    if _active_job_id is not None:
        return
    async def run():
        while True:
            try:
                if repository.get_index_meta().get('workspace_vectors_pending') == '1':
                    await rebuild(IndexRebuildRequest())
                elif pending := _pending_notes():
                    await _refresh_saved_note(pending[0])
                else:
                    return
            except ApiError as exc:
                if exc.code == 'INDEX_SNAPSHOT_CHANGED':
                    await asyncio.sleep(1)
                    continue
                _logger.warning('Background index failed: %s', exc.code)
                return
            except Exception:
                _logger.exception('Background index failed')
                return
    _background_task = asyncio.create_task(run(), name='workspace-vector-index')


async def shutdown() -> None:
    global _background_task
    if _background_task is not None:
        _background_task.cancel()
        await asyncio.gather(_background_task, return_exceptions=True)
        _background_task = None


def _pending_notes() -> list[str]:
    return [key.split(':', 1)[1] for key, value in repository.get_index_meta().items()
            if key.startswith('note_vectors_pending:') and value == '1']


async def _refresh_saved_note(note_id: str) -> None:
    global _active_job_id, _last_error, _last_completed_at
    record = repository.get_note_record(note_id)
    key = f'note_vectors_pending:{note_id}'
    if record is None:
        repository.set_index_meta({key: '0'})
        return
    markdown = note_service._read_markdown(record.file_path)
    parsed = parse_note(markdown=markdown, file_path=record.file_path, folder=record.folder,
                        tags=record.tags, created_at=record.created_at,
                        updated_at=record.updated_at, note_id=note_id)
    parsed.title = record.title
    job_id = 'job_' + uuid4().hex[:12]
    _active_job_id = job_id
    _last_error = None
    _remember_job(IndexJob(job_id=job_id, status='running', scope='all', created_at=datetime.now(timezone.utc)))
    try:
        prepared = await prepare_note_index(parsed, strict=True)
        if isinstance(note_service.embedding, LocalEmbedding) and parsed.blocks and prepared[1] is None:
            raise ApiError(503, "EMBEDDING_UNAVAILABLE", "笔记已保存，后台向量计算未完成。")
        async with _vault_mutation_lock:
            current = repository.get_note_record(note_id)
            if current != record or note_service._read_markdown(record.file_path) != markdown:
                # Another save or rename won the race; leave the durable queue entry intact.
                return
            conn = connect()
            try:
                with transaction(conn):
                    # Write only vectors: metadata and FTS already represent the saved revision.
                    vectors, remote = prepared
                    from app.retrieval.vectorstore import VectorRecord
                    from app.retrieval import routed_vectors
                    await vector_store.upsert([VectorRecord(id=b.block_id, vector=v)
                        for b, v in zip(parsed.blocks, vectors)], conn=conn)
                    routed_vectors.store_remote(conn, [b.block_id for b in parsed.blocks], remote)
                    repository.set_index_meta({key: '0'}, conn=conn)
            finally:
                conn.close()
        _last_completed_at = datetime.now(timezone.utc)
        _remember_job(IndexJob(job_id=job_id, status='completed', scope='all', created_at=_last_completed_at))
    except BaseException as exc:
        _last_error = str(exc) or '后台向量计算已中断，笔记已保存。'
        _remember_job(IndexJob(job_id=job_id, status='failed', scope='all', created_at=datetime.now(timezone.utc)))
        raise
    finally:
        _active_job_id = None
