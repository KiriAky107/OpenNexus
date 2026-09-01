"""Benchmark 服务：运行注册表、配置快照与报告组装。

MVP 阶段运行是同步的（与 index_service 一致）：POST 创建后立即执行完并返回
completed 的 BenchmarkRun。运行记录、事件与报告暂存内存（_runs/_events/_reports），
不持久化到 SQLite；后续接入异步任务队列时再落库。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from uuid import uuid4

from app.benchmarks import datasets
from app.benchmarks.datasets import RAGDataset
from app.benchmarks.rag import run_rag
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
        "embedding": {"model_id": engine.embedding.model_id, "dim": engine.embedding.dim},
        "reranker": {"model_id": engine.reranker.model_id},
        "app": {"version": settings.version, "environment": settings.environment},
        "python": sys.version.split()[0],
        "metadata": request.metadata,
    }


async def create_rag_run(request: RAGRunRequest) -> BenchmarkRun:
    """创建并同步执行一次 RAG Benchmark，返回 completed 的 BenchmarkRun。"""
    dataset = datasets.load_dataset(request.dataset_id, BenchmarkKind.rag)
    run_id = "benchmark_" + uuid4().hex[:12]
    snapshot = _config_snapshot(request, dataset)

    run = BenchmarkRun(
        run_id=run_id,
        kind=BenchmarkKind.rag,
        dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash,
        status=BenchmarkStatus.running,
        progress=0.0,
        config_snapshot=snapshot,
        created_at=_now(),
        started_at=_now(),
    )
    _remember(run)
    _events[run_id] = []

    def emit(event_type: BenchmarkEventType, data: dict) -> None:
        sequence = len(_events[run_id])
        _events[run_id].append(
            BenchmarkEvent(
                event=event_type, run_id=run_id, sequence=sequence,
                data=data, timestamp=_now(),
            )
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
        metrics_by_mode, results = await run_rag(dataset, request, on_case=on_case)
    except Exception as exc:
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
        raise ApiError(500, "BENCHMARK_RUN_FAILED", str(exc), {"run_id": run_id}) from exc

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
    return _runs[run_id]


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
    """取消运行：同步 MVP 下运行通常已结束，仅对仍在排队/运行的记录置为 cancelled。"""
    run = _runs.get(run_id)
    if run is None:
        return None
    if run.status in (BenchmarkStatus.queued, BenchmarkStatus.running):
        run = run.model_copy(
            update={"status": BenchmarkStatus.cancelled, "completed_at": _now()}
        )
        _runs[run_id] = run
    return run
