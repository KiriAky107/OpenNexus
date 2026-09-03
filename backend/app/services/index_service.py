"""索引服务：扫描 Vault、全量重建索引、查询索引状态。

MVP 阶段重建是同步的（数据量小），完成后直接返回 completed 的 IndexJob。
索引任务暂存内存（_jobs），不持久化到 SQLite；后续接入异步任务队列时再落到 index_jobs 表。
"""

from __future__ import annotations

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
from app.services.coordination import serialized_vault_mutation
from app.retrieval.vectorstore import SqliteVecStore

vector_store = SqliteVecStore()

_jobs: dict[str, IndexJob] = {}
_active_job_id: str | None = None
_last_completed_at: datetime | None = None
_last_error: str | None = None
MAX_JOBS = 100


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


@serialized_vault_mutation
async def rebuild(request: IndexRebuildRequest) -> IndexJob:
    global _active_job_id, _last_completed_at, _last_error
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

    _active_job_id = job_id
    _last_error = None
    _remember_job(IndexJob(
        job_id=job_id, status="running", scope=request.scope,
        created_at=datetime.now(timezone.utc),
    ))
    try:
        prepared_notes = []
        for rel, folder, markdown, created, updated in docs:
            parsed = parse_note(
                markdown=markdown, file_path=rel, folder=folder, tags=None,
                created_at=created, updated_at=updated,
            )
            prepared_notes.append((parsed, await prepare_note_index(parsed)))
        # All network/model awaits precede the transaction. The concrete SQLite
        # methods below complete synchronously despite their async interfaces.
        conn = connect()
        try:
            with transaction(conn):
                task_note_links = dict(conn.execute(
                    "SELECT task_id, note_id FROM tasks WHERE note_id IS NOT NULL"
                ).fetchall())
                repository.clear_all(conn=conn)
                await vector_store.clear(conn=conn)
                for parsed, prepared in prepared_notes:
                    await index_note(parsed, prepared=prepared, conn=conn)
                for task_id, note_id in task_note_links.items():
                    conn.execute(
                        "UPDATE tasks SET note_id = ? WHERE task_id = ? "
                        "AND EXISTS (SELECT 1 FROM notes WHERE note_id = ?)",
                        (note_id, task_id, note_id),
                    )
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
    return job


def get_status() -> IndexStatus:
    if _active_job_id is not None:
        return IndexStatus(status="running", pending_jobs=0, active_job_id=_active_job_id)
    return IndexStatus(
        status="failed" if _last_error else "idle",
        pending_jobs=0,
        last_completed_at=_last_completed_at,
        error_message=_last_error,
    )


def get_job(job_id: str) -> IndexJob | None:
    return _jobs.get(job_id)
