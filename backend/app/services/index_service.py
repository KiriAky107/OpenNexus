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
from app.retrieval.vectorstore import SqliteVecStore

vector_store = SqliteVecStore()

_jobs: dict[str, IndexJob] = {}


def _scan_vault() -> list[tuple[str, str, str, datetime, datetime]]:
    """扫描 Vault 下所有 Markdown，返回 (rel_path, folder, markdown, created, updated)。

    先读入内存：若文件读取失败，rebuild 尚未清空旧索引，不会造成数据损失。
    """
    vault = get_settings().vault_path
    result: list[tuple[str, str, str, datetime, datetime]] = []
    if not vault.exists():
        return result
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        folder = path.relative_to(vault).parent.as_posix()
        if folder == ".":
            folder = ""
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        result.append((rel, folder, path.read_text(encoding="utf-8"), created, updated))
    return result


async def rebuild(request: IndexRebuildRequest) -> IndexJob:
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
    backup_path = settings.db_path.with_suffix(".db.bak") if settings.db_path.exists() else None
    if backup_path is not None:
        shutil.copy2(settings.db_path, backup_path)

    try:
        repository.clear_all()
        await vector_store.clear()
        for rel, folder, markdown, created, updated in docs:
            parsed = parse_note(
                markdown=markdown, file_path=rel, folder=folder, tags=None,
                created_at=created, updated_at=updated,
            )
            await index_note(parsed)
    except BaseException:
        # 重建失败：恢复旧索引，避免留下半成品；记录 failed 任务后向上抛
        if backup_path is not None and backup_path.exists():
            shutil.copy2(backup_path, settings.db_path)
        _jobs[job_id] = IndexJob(
            job_id=job_id, status="failed", scope=request.scope,
            created_at=datetime.now(timezone.utc),
        )
        raise
    finally:
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)

    job = IndexJob(job_id=job_id, status="completed", scope=request.scope, created_at=datetime.now(timezone.utc))
    _jobs[job_id] = job
    return job


def get_status() -> IndexStatus:
    # 同步重建、无排队任务，因此状态恒为 idle；实际索引规模可由 GET /api/notes 与搜索反映
    return IndexStatus(status="idle", pending_jobs=0)


def get_job(job_id: str) -> IndexJob | None:
    return _jobs.get(job_id)
