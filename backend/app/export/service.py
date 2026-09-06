"""Export 服务：任务注册表、后台渲染、取消与产物生命周期。

与 Benchmark 一致采用「创建即返回 queued、后台 Task 异步执行」的内存模型：任务与产物
暂存内存与 exports 目录，不持久化到 SQLite。导出是单阶段渲染，无 SSE 事件流，取消主要
在渲染前/后让出执行权的边界生效；产物带 24h 过期时间，过期后不可下载。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.contracts import (
    ExportFile,
    ExportFormat,
    ExportJob,
    ExportOptions,
    ExportProgress,
    ExportRequest,
    ExportSource,
    ExportSourceType,
    ExportStatus,
)
from app.errors import ApiError
from app.export.document import Document, ExportResult
from app.export.exporters.html import HtmlExporter
from app.export.markdown import parse_document
from app.services import note_service

logger = logging.getLogger(__name__)

_jobs: dict[str, ExportJob] = {}
_tasks: dict[str, asyncio.Task] = {}
_cancel_flags: dict[str, asyncio.Event] = {}
MAX_JOBS = 100
# 输入源（note / markdown）统一大小上限，防止未保存预览或超长笔记塞爆内存/产物
MAX_MARKDOWN_CHARS = 200_000
# 最终导出产物大小上限，防止超大 HTML 耗尽内存/磁盘
MAX_EXPORT_BYTES = 20 * 1024 * 1024  # 20 MB
# 并发渲染上限：解析/渲染是 CPU 密集的同步工作，限制同时执行的任务数，
# 防止大量任务同时占满工作线程与内存
MAX_CONCURRENT_RENDERS = 2
_render_slots = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
# 产物有效期
FILE_TTL = timedelta(hours=24)

_INVALID_FILE_CHARS = re.compile(r'[\\/:*?"<>|]')


class ExportCancelled(Exception):
    """导出在渲染前被取消时抛出，用于标记 cancelled。"""


class ExportTooLarge(Exception):
    """导出产物超过大小上限时抛出，用于标记 failed 并携带专用错误码。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_download_name(title: str) -> str:
    """清洗标题得到安全的下载文件名；空标题回退到 export。"""
    name = _INVALID_FILE_CHARS.sub("_", title).strip() or "export"
    return name[:80]


def _export_path(job_id: str) -> Path:
    return get_settings().exports_path / f"{job_id}.html"


def _delete_file(job_id: str) -> None:
    """删除导出产物文件；文件不存在时忽略。"""
    try:
        _export_path(job_id).unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete export file: %s", job_id)


def cleanup_orphan_files() -> int:
    """清理 exports 目录下无对应内存任务的孤立产物（服务重启后调用）。"""
    exports_dir = get_settings().exports_path
    if not exports_dir.is_dir():
        return 0
    removed = 0
    for path in exports_dir.glob("*.html"):
        if path.stem not in _jobs:
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("Failed to delete orphan export file: %s", path)
    return removed


def _render_document(document: Document, options: ExportOptions) -> ExportResult:
    """同步渲染辅助，供 asyncio.to_thread 调用；每次新建实例避免跨线程复用。"""
    return HtmlExporter().render(document, options)


def _forget(job_id: str) -> None:
    _jobs.pop(job_id, None)
    _tasks.pop(job_id, None)
    _cancel_flags.pop(job_id, None)
    _delete_file(job_id)


def _evict_terminal() -> bool:
    """超过容量时淘汰最旧的终态任务；全为活动任务无法淘汰时返回 False。"""
    terminal = (ExportStatus.completed, ExportStatus.failed, ExportStatus.cancelled)
    while len(_jobs) >= MAX_JOBS:
        victim = next((jid for jid, job in _jobs.items() if job.status in terminal), None)
        if victim is None:
            return False
        _forget(victim)
    return True


async def _resolve_source(source: ExportSource) -> tuple[str, str, dict | None]:
    """把导出源解析为 (markdown, title, metadata)；metadata 仅 note 源提供。"""
    if source.type == ExportSourceType.note:
        note = await note_service.get_note(source.note_id)
        if note is None:
            raise ApiError(
                404,
                "EXPORT_SOURCE_NOT_FOUND",
                "note not found",
                {"note_id": source.note_id},
            )
        if len(note.markdown) > MAX_MARKDOWN_CHARS:
            raise ApiError(
                400,
                "EXPORT_OPTIONS_INVALID",
                f"note source exceeds {MAX_MARKDOWN_CHARS} characters",
                {"size": len(note.markdown), "limit": MAX_MARKDOWN_CHARS},
            )
        metadata = {
            "file_path": note.file_path,
            "tags": note.tags,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        }
        return note.markdown, note.title, metadata

    markdown = source.markdown or ""
    if not markdown.strip():
        raise ApiError(400, "EXPORT_OPTIONS_INVALID", "markdown source must not be empty")
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise ApiError(
            400,
            "EXPORT_OPTIONS_INVALID",
            f"markdown source exceeds {MAX_MARKDOWN_CHARS} characters",
            {"size": len(markdown), "limit": MAX_MARKDOWN_CHARS},
        )
    return markdown, "", None


async def create_export(request: ExportRequest) -> ExportJob:
    """创建导出任务，立即返回 queued 的 ExportJob，由后台 Task 渲染。"""
    if request.format != ExportFormat.html:
        raise ApiError(
            400,
            "EXPORT_FORMAT_UNSUPPORTED",
            "PDF/DOCX 暂未实现，当前仅支持 HTML",
            {"format": request.format.value},
        )
    markdown, title, metadata = await _resolve_source(request.source)

    if not _evict_terminal():
        raise ApiError(
            429,
            "EXPORT_CAPACITY_EXCEEDED",
            "Export capacity exceeded; wait for active jobs to finish.",
            {},
        )

    job_id = "export_" + uuid4().hex[:12]
    job = ExportJob(
        job_id=job_id,
        status=ExportStatus.queued,
        format=request.format,
        created_at=_now(),
    )
    _jobs[job_id] = job
    _cancel_flags[job_id] = asyncio.Event()
    _tasks[job_id] = asyncio.create_task(
        _execute(job_id, markdown, title, metadata, request.options)
    )
    return job


async def _execute(
    job_id: str,
    markdown: str,
    title: str,
    metadata: dict | None,
    options: ExportOptions,
) -> None:
    """后台渲染：解析 → 导出 → 写文件 → 挂载产物元信息。"""
    cancel_event = _cancel_flags[job_id]
    _jobs[job_id] = _jobs[job_id].model_copy(
        update={
            "status": ExportStatus.running,
            "started_at": _now(),
            "progress": ExportProgress(phase="rendering", current=0, total=1, percent=0.0),
        }
    )
    try:
        # 并发渲染限额：解析/渲染是 CPU 密集的同步工作，用信号量限制同时执行的任务数，
        # 超出限额的任务在此排队等待，避免大量任务同时占满工作线程与内存
        async with _render_slots:
            # 让出一次，使「创建后立即取消」的 queued 任务能及时进入 cancelled
            await asyncio.sleep(0)
            if cancel_event.is_set():
                raise ExportCancelled()

            # 解析与渲染都是 CPU 密集的同步工作，放入线程执行避免阻塞事件循环，
            # 使运行中的取消能在渲染边界生效；写文件前再次检查取消。
            document = await asyncio.to_thread(parse_document, markdown)
            document.attributes["title"] = title
            if metadata:
                document.attributes["metadata"] = metadata

            result = await asyncio.to_thread(_render_document, document, options)
            if cancel_event.is_set():
                raise ExportCancelled()
            if len(result.content) > MAX_EXPORT_BYTES:
                raise ExportTooLarge()

            out_dir = get_settings().exports_path
            out_dir.mkdir(parents=True, exist_ok=True)
            path = _export_path(job_id)
            path.write_bytes(result.content)

            completed_at = _now()
            _jobs[job_id] = _jobs[job_id].model_copy(
                update={
                    "status": ExportStatus.completed,
                    "progress": ExportProgress(
                        phase="completed", current=1, total=1, percent=1.0
                    ),
                    "file": ExportFile(
                        file_name=f"{_safe_download_name(title)}.html",
                        mime_type=result.mime_type,
                        size=len(result.content),
                        sha256=hashlib.sha256(result.content).hexdigest(),
                        expires_at=completed_at + FILE_TTL,
                    ),
                    "warnings": result.warnings,
                    "completed_at": completed_at,
                }
            )
    except ExportCancelled:
        _jobs[job_id] = _jobs[job_id].model_copy(
            update={
                "status": ExportStatus.cancelled,
                "completed_at": _now(),
            }
        )
    except ExportTooLarge:
        _jobs[job_id] = _jobs[job_id].model_copy(
            update={
                "status": ExportStatus.failed,
                "error": "Export output exceeds size limit.",
                "error_code": "EXPORT_OUTPUT_TOO_LARGE",
                "completed_at": _now(),
            }
        )
    except Exception as exc:  # 渲染失败不拖垮服务，只记日志与项目错误码
        logger.exception("Export failed: job_id=%s", job_id)
        _jobs[job_id] = _jobs[job_id].model_copy(
            update={
                "status": ExportStatus.failed,
                "error": "Export render failed.",
                "error_code": "EXPORT_RENDER_FAILED",
                "completed_at": _now(),
            }
        )
    finally:
        _cancel_flags.pop(job_id, None)


def list_exports(
    status: ExportStatus | None = None,
    format: ExportFormat | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ExportJob], int]:
    jobs = list(_jobs.values())
    if status is not None:
        jobs = [j for j in jobs if j.status == status]
    if format is not None:
        jobs = [j for j in jobs if j.format == format]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    total = len(jobs)
    return jobs[offset : offset + limit], total


def get_export(job_id: str) -> ExportJob | None:
    return _jobs.get(job_id)


def cancel_export(job_id: str) -> ExportJob | None:
    """取消导出：仅 queued/running 可取消，后台 Task 在让出边界标记 cancelled。"""
    job = _jobs.get(job_id)
    if job is None:
        return None
    if job.status in (ExportStatus.queued, ExportStatus.running):
        _cancel_flags[job_id].set()
    return job


def get_export_file(job_id: str) -> Path:
    """返回可下载产物的存储路径；未完成返回 404、过期返回 410。"""
    job = _jobs.get(job_id)
    if job is None:
        raise ApiError(404, "EXPORT_JOB_NOT_FOUND", "export job not found", {"job_id": job_id})
    if job.status != ExportStatus.completed or job.file is None:
        raise ApiError(
            404, "EXPORT_JOB_NOT_FOUND", "export file not ready", {"job_id": job_id}
        )
    if job.file.expires_at <= _now():
        _forget(job_id)  # 过期即清理内存记录与产物文件
        raise ApiError(410, "EXPORT_FILE_EXPIRED", "export file has expired", {"job_id": job_id})
    return _export_path(job_id)


async def wait_for_export(job_id: str) -> ExportJob | None:
    """等待后台任务结束（测试/轮询用）；无任务时直接返回当前状态。"""
    task = _tasks.get(job_id)
    if task is not None:
        await task
    return _jobs.get(job_id)
