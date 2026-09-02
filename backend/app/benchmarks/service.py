"""Benchmark 服务：运行注册表、配置快照与报告组装。

RAG Benchmark 采用「创建即返回 queued、后台 Task 异步执行」的模式（与 index_service
的 rebuild 一致）：POST 创建后立即返回 202 queued 的 BenchmarkRun，由受管 asyncio.Task
在后台逐 Case 求值，进度与事件实时写入内存注册表，供 SSE 订阅。运行记录、事件与报告
暂存内存（_runs/_events/_reports），不持久化到 SQLite；后续接入异步任务队列时再落库。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from uuid import uuid4

from app import repository
from app.benchmarks import datasets
from app.benchmarks.datasets import RAGDataset
from app.benchmarks.rag import BenchmarkCancelled, run_rag
from app.config import get_settings
from app.contracts import (
    BenchmarkEvent,
    BenchmarkEventType,
    BenchmarkKind,
    BenchmarkReport,
    BenchmarkRun,
    BenchmarkStatus,
    RAGCaseResult,
    RAGMetrics,
    RAGRunRequest,
)
from app.errors import ApiError
from app.retrieval.engine import engine

_runs: dict[str, BenchmarkRun] = {}
_events: dict[str, list[BenchmarkEvent]] = {}
_reports: dict[str, BenchmarkReport] = {}
_tasks: dict[str, asyncio.Task] = {}
_subscribers: dict[str, list[asyncio.Queue[BenchmarkEvent]]] = {}
_cancel_flags: dict[str, asyncio.Event] = {}
MAX_RUNS = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _remember(run: BenchmarkRun) -> None:
    _runs[run.run_id] = run
    while len(_runs) > MAX_RUNS:
        oldest = next(iter(_runs))
        _runs.pop(oldest, None)
        _events.pop(oldest, None)
        _reports.pop(oldest, None)
        _tasks.pop(oldest, None)
        _subscribers.pop(oldest, None)
        _cancel_flags.pop(oldest, None)


def _config_snapshot(request: RAGRunRequest, dataset: RAGDataset) -> dict:
    """记录运行时的模型 / 索引 / 环境信息，保证报告可解释、可复现。"""
    settings = get_settings()
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_hash": dataset.content_hash,
        "dataset_version": dataset.version,
        "modes": [m.value for m in request.modes],
        "retrieval": request.retrieval.model_dump(),
        "repeat": request.repeat,
        "embedding": {
            "model_id": engine.embedding.model_id,
            "version": engine.embedding.version,
            "dim": engine.embedding.dim,
        },
        "reranker": {
            "model_id": engine.reranker.model_id,
            "version": engine.reranker.version,
        },
        "index_meta": repository.get_index_meta(),
        "app": {"version": settings.version, "environment": settings.environment},
        "python": sys.version.split()[0],
        "metadata": request.metadata,
    }


async def create_rag_run(request: RAGRunRequest) -> BenchmarkRun:
    """创建一次 RAG Benchmark，立即返回 queued 的 BenchmarkRun，由后台 Task 执行。"""
    dataset = datasets.load_dataset(request.dataset_id, BenchmarkKind.rag)
    run_id = "benchmark_" + uuid4().hex[:12]
    snapshot = _config_snapshot(request, dataset)

    run = BenchmarkRun(
        run_id=run_id,
        kind=BenchmarkKind.rag,
        dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash,
        status=BenchmarkStatus.queued,
        progress=0.0,
        config_snapshot=snapshot,
        created_at=_now(),
    )
    _remember(run)
    _events[run_id] = []
    _subscribers[run_id] = []
    _cancel_flags[run_id] = asyncio.Event()
    _tasks[run_id] = asyncio.create_task(_execute_rag(run_id, request, dataset, snapshot))
    return run


async def _execute_rag(
    run_id: str, request: RAGRunRequest, dataset: RAGDataset, snapshot: dict
) -> None:
    """后台执行 RAG Benchmark，实时更新进度/事件，结束后写入报告并关闭订阅。"""
    cancel_event = _cancel_flags[run_id]

    def emit(event_type: BenchmarkEventType, data: dict) -> None:
        sequence = len(_events[run_id])
        event = BenchmarkEvent(
            event=event_type, run_id=run_id, sequence=sequence, data=data, timestamp=_now()
        )
        _events[run_id].append(event)
        for queue in _subscribers.get(run_id, []):
            queue.put_nowait(event)

    def finish() -> None:
        _subscribers.pop(run_id, None)
        _cancel_flags.pop(run_id, None)

    _runs[run_id] = _runs[run_id].model_copy(
        update={"status": BenchmarkStatus.running, "started_at": _now()}
    )
    emit(
        BenchmarkEventType.run_started,
        {"dataset_id": dataset.dataset_id, "modes": [m.value for m in request.modes]},
    )
    total = len(request.modes) * len(dataset.cases) * request.repeat

    def on_case(result: RAGCaseResult, done: int, _total: int) -> None:
        progress = done / total if total else 1.0
        _runs[run_id] = _runs[run_id].model_copy(update={"progress": progress})
        emit(BenchmarkEventType.case_completed, result.model_dump(mode="json"))

    try:
        metrics_by_mode, results = await run_rag(
            dataset,
            request,
            on_case=on_case,
            should_cancel=cancel_event.is_set,
        )
    except BenchmarkCancelled:
        _runs[run_id] = _runs[run_id].model_copy(
            update={
                "status": BenchmarkStatus.cancelled,
                "progress": 1.0,
                "completed_at": _now(),
            }
        )
        _reports[run_id] = BenchmarkReport(
            run_id=run_id,
            kind=BenchmarkKind.rag,
            dataset_id=dataset.dataset_id,
            dataset_hash=dataset.content_hash,
            status=BenchmarkStatus.cancelled,
            config_snapshot=snapshot,
        )
        finish()
        return
    except Exception as exc:  # 单次运行失败不拖垮服务，记录错误后结束
        _runs[run_id] = _runs[run_id].model_copy(
            update={
                "status": BenchmarkStatus.failed,
                "progress": 1.0,
                "error": str(exc),
                "completed_at": _now(),
            }
        )
        emit(BenchmarkEventType.run_failed, {"error": str(exc)})
        _reports[run_id] = BenchmarkReport(
            run_id=run_id,
            kind=BenchmarkKind.rag,
            dataset_id=dataset.dataset_id,
            dataset_hash=dataset.content_hash,
            status=BenchmarkStatus.failed,
            config_snapshot=snapshot,
            error=str(exc),
        )
        finish()
        return

    metrics = {mode: m.model_dump() for mode, m in metrics_by_mode.items()}
    _runs[run_id] = _runs[run_id].model_copy(
        update={
            "status": BenchmarkStatus.completed,
            "progress": 1.0,
            "metrics": metrics,
            "completed_at": _now(),
        }
    )
    emit(BenchmarkEventType.run_completed, {"metrics": metrics})
    _reports[run_id] = BenchmarkReport(
        run_id=run_id,
        kind=BenchmarkKind.rag,
        dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash,
        status=BenchmarkStatus.completed,
        config_snapshot=snapshot,
        metrics=metrics,
        cases=results,
    )
    finish()


def list_runs(
    kind: BenchmarkKind | None = None,
    status: BenchmarkStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BenchmarkRun], int]:
    runs = list(_runs.values())
    if kind is not None:
        runs = [r for r in runs if r.kind == kind]
    if status is not None:
        runs = [r for r in runs if r.status == status]
    runs.sort(key=lambda r: r.created_at, reverse=True)
    total = len(runs)
    return runs[offset : offset + limit], total


def get_run(run_id: str) -> BenchmarkRun | None:
    return _runs.get(run_id)


def get_report(run_id: str) -> BenchmarkReport | None:
    return _reports.get(run_id)


def get_events(run_id: str) -> list[BenchmarkEvent]:
    return _events.get(run_id, [])


def cancel_run(run_id: str) -> BenchmarkRun | None:
    """取消运行：对 queued/running 设置取消标志，后台 Task 在 Case 边界检查后置为 cancelled。"""
    run = _runs.get(run_id)
    if run is None:
        return None
    if run.status in (BenchmarkStatus.queued, BenchmarkStatus.running):
        _cancel_flags[run_id].set()
    return run


def subscribe(run_id: str) -> asyncio.Queue[BenchmarkEvent] | None:
    """订阅运行事件流；运行已结束（completed/failed/cancelled）时返回 None。"""
    run = _runs.get(run_id)
    if run is None or run.status in (
        BenchmarkStatus.completed,
        BenchmarkStatus.failed,
        BenchmarkStatus.cancelled,
    ):
        return None
    queue: asyncio.Queue[BenchmarkEvent] = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue[BenchmarkEvent]) -> None:
    subscribers = _subscribers.get(run_id)
    if subscribers and queue in subscribers:
        subscribers.remove(queue)


async def wait_for_run(run_id: str) -> BenchmarkRun:
    """等待后台任务结束（测试/轮询用）；无任务时直接返回当前状态。"""
    task = _tasks.get(run_id)
    if task is not None:
        await task
    return _runs.get(run_id)
