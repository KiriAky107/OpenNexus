"""索引服务：扫描 Vault、全量重建索引、查询索引状态。

MVP 阶段重建是同步的（数据量小），完成后直接返回 completed 的 IndexJob。
索引任务暂存内存（_jobs），不持久化到 SQLite；后续接入异步任务队列时再落到 index_jobs 表。
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import repository
from app.config import get_settings
from app.contracts import IndexJob, IndexRebuildRequest, IndexStatus
from app.errors import ApiError
from app.knowledge.parser import parse_note
from app.services.note_service import index_note
from app.services import task_service
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

    # 先扫描到内存（失败不会清旧索引），再快照旧库用于失败回滚
    docs = _scan_vault()
    settings = get_settings()
    database_existed = settings.db_path.exists()
    task_note_links = task_service.note_links() if database_existed else {}
    backup_path = (
        settings.db_path.with_name(f"{settings.db_path.name}.{job_id}.bak")
        if database_existed
        else None
    )
    if backup_path is not None:
        shutil.copy2(settings.db_path, backup_path)

    _active_job_id = job_id
    _last_error = None
    _remember_job(IndexJob(
        job_id=job_id, status="running", scope=request.scope,
        created_at=datetime.now(timezone.utc),
    ))
    try:
        # Deleting blocks also cascades every space in routed_block_vectors;
        # index_note repopulates only the currently successful API space.
        repository.clear_all()
        await vector_store.clear()
        for rel, folder, markdown, created, updated in docs:
            parsed = parse_note(
                markdown=markdown, file_path=rel, folder=folder, tags=None,
                created_at=created, updated_at=updated,
            )
            await index_note(parsed)
        task_service.restore_note_links(task_note_links)
    except BaseException as exc:
        # 重建失败：恢复旧索引，避免留下半成品；记录 failed 任务后向上抛
        if backup_path is not None and backup_path.exists():
            shutil.copy2(backup_path, settings.db_path)
        elif not database_existed:
            settings.db_path.unlink(missing_ok=True)
        _remember_job(IndexJob(
            job_id=job_id, status="failed", scope=request.scope,
            created_at=datetime.now(timezone.utc),
        ))
        _last_error = str(exc)
        raise
    finally:
        _active_job_id = None
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)

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
