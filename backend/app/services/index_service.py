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
from app.knowledge.parser import parse_note
from app.services.note_service import index_note
from app.retrieval.vectorstore import SqliteVecStore

vector_store = SqliteVecStore()

_jobs: dict[str, IndexJob] = {}


def _scan_vault() -> list[tuple[str, str, str]]:
    """扫描 Vault 下所有 Markdown，返回 (rel_path, folder, markdown)。"""
    vault = get_settings().vault_path
    result: list[tuple[str, str, str]] = []
    if not vault.exists():
        return result
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        folder = path.relative_to(vault).parent.as_posix()
        if folder == ".":
            folder = ""
        result.append((rel, folder, path.read_text(encoding="utf-8")))
    return result


async def rebuild(request: IndexRebuildRequest) -> IndexJob:
    job_id = "job_" + uuid4().hex[:12]
    # MVP：scope（all/notes/vectors）与 note_ids 增量暂不区分，统一全量重建
    repository.clear_all()
    await vector_store.clear()

    vault = get_settings().vault_path
    for rel, folder, markdown in _scan_vault():
        path = vault / rel
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        parsed = parse_note(
            markdown=markdown, file_path=rel, folder=folder, tags=None,
            created_at=created, updated_at=updated,
        )
        await index_note(parsed)

    job = IndexJob(job_id=job_id, status="completed", scope=request.scope, created_at=datetime.now(timezone.utc))
    _jobs[job_id] = job
    return job


def get_status() -> IndexStatus:
    # 同步重建、无排队任务，因此状态恒为 idle；实际索引规模可由 GET /api/notes 与搜索反映
    return IndexStatus(status="idle", pending_jobs=0)


def get_job(job_id: str) -> IndexJob | None:
    return _jobs.get(job_id)
