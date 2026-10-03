"""Benchmark 服务：运行注册表、配置快照与报告组装。

RAG Benchmark 采用「创建即返回 queued、后台 Task 异步执行」的模式（与 index_service
的 rebuild 一致）：POST 创建后立即返回 202 queued 的 BenchmarkRun，由受管 asyncio.Task
在后台逐 Case 求值，进度与事件实时写入内存注册表，供 SSE 订阅。运行记录、事件与报告
活动任务暂存内存；运行快照、逐例事件与报告持久化到当前知识库的 SQLite。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

from app import repository
from app.benchmarks import datasets
from app.benchmarks import storage
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
    RAGRunRequest,
    SearchMode,
)
from app.errors import ApiError
from app.retrieval.engine import engine

logger = logging.getLogger(__name__)

_runs: dict[str, BenchmarkRun] = {}
_events: dict[str, list[BenchmarkEvent]] = {}
_reports: dict[str, BenchmarkReport] = {}
_tasks: dict[str, asyncio.Task] = {}
_subscribers: dict[str, list[asyncio.Queue[BenchmarkEvent | None]]] = {}
_cancel_flags: dict[str, asyncio.Event] = {}
MAX_RUNS = 100


async def _persist(run_id, event=None, *, run=None, report=None):
    """One awaited worker write per run: ordered, capacity-one backpressure.

    to_thread carries the creating task's Vault ContextVars. Shield the write so
    cancellation cannot race an unfinished transaction with the terminal event.
    Only committed state is visible to polling and SSE subscribers.
    """
    run = run if run is not None else _runs[run_id]
    write = asyncio.create_task(asyncio.to_thread(storage.save, run, report, event))
    cancelled = False
    while not write.done():
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            cancelled = True
    write.result()
    _runs[run_id] = run
    if report is not None:
        _reports[run_id] = report
    if event is not None:
        _events[run_id].append(event)
        for queue in _subscribers.get(run_id, []):
            queue.put_nowait(event)
    if cancelled:
        raise asyncio.CancelledError


async def _emit(run_id, kind, data, *, run=None, report=None):
    event = BenchmarkEvent(event=kind, run_id=run_id, sequence=len(_events[run_id]), data=data, timestamp=_now())
    await _persist(run_id, event, run=run, report=report)


async def _finish(run_id, status, metrics=None, cases=None, error_code=None):
    """Commit the report and its terminal event together, then close live state."""
    if _runs[run_id].status in (BenchmarkStatus.completed, BenchmarkStatus.cancelled, BenchmarkStatus.failed):
        return  # Cancellation arrived after a terminal transaction committed.
    run = _runs[run_id].model_copy(update={
        'status': status, 'progress': 1.0, 'metrics': metrics or {}, 'completed_at': _now(),
        'error_code': error_code, 'error': 'Benchmark run failed.' if error_code else None,
    })
    report = BenchmarkReport(run_id=run_id, kind=run.kind, dataset_id=run.dataset_id,
        dataset_hash=run.dataset_hash, status=status, config_snapshot=run.config_snapshot,
        metrics=run.metrics, error_code=run.error_code, error=run.error,
        cases=cases if cases is not None else [event.data for event in _events[run_id]
            if event.event == BenchmarkEventType.case_completed])
    kind = {BenchmarkStatus.completed: BenchmarkEventType.run_completed,
        BenchmarkStatus.cancelled: BenchmarkEventType.run_cancelled,
        BenchmarkStatus.failed: BenchmarkEventType.run_failed}[status]
    try:
        await _emit(run_id, kind, {'metrics': run.metrics, 'error_code': run.error_code, 'error': run.error},
            run=run, report=report)
    except asyncio.CancelledError:
        pass  # _persist already waited for and published this terminal commit.
    except Exception:
        logger.exception('Benchmark terminal persistence failed: run_id=%s', run_id)
        # Do not advertise a result that never reached disk. The last committed
        # evidence remains recoverable through the existing interrupted-run path.
        _forget(run_id)
    finally:
        _close_subscribers(run_id)
        _cancel_flags.pop(run_id, None)


def _saved(run_id):
    _recover(run_id)
    return storage.get(run_id)


def _recover(run_id):
    # Live asyncio tasks/queues stay on their owning loop. Database recovery only
    # touches orphaned records and serializes its status transition in SQLite.
    if run_id not in _tasks and run_id not in _runs:
        run = storage.get_summary(run_id)
        if run and run.status in (BenchmarkStatus.queued, BenchmarkStatus.running):
            storage.recover_interrupted(run_id, _now())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _forget(run_id: str) -> None:
    """移除一条 run 的全部内存态；仅在 run 处于终态时调用，避免打断活动任务。"""
    _runs.pop(run_id, None)
    _events.pop(run_id, None)
    _reports.pop(run_id, None)
    _tasks.pop(run_id, None)
    _close_subscribers(run_id)
    _cancel_flags.pop(run_id, None)


def _close_subscribers(run_id):
    # Closing a failed writer must not manufacture an uncommitted SSE event, or
    # leave the stream waiting indefinitely for an event that can never arrive.
    for queue in _subscribers.pop(run_id, []):
        queue.put_nowait(None)


def _evict_terminal() -> bool:
    """超过容量时淘汰最旧的终态 run；全部为活动 run 无法淘汰时返回 False。

    绝不能删除仍在运行（queued/running）的 run：那会连带移除其 _cancel_flags 与
    _subscribers，使后台 Task 访问时抛出 KeyError。
    """
    terminal = (BenchmarkStatus.completed, BenchmarkStatus.failed, BenchmarkStatus.cancelled)
    while len(_runs) >= MAX_RUNS:
        victim = next(
            (rid for rid, run in _runs.items() if run.status in terminal), None
        )
        if victim is None:
            return False
        _forget(victim)
    return True


def _config_snapshot(request: RAGRunRequest, dataset: RAGDataset) -> dict:
    """记录运行时的模型 / 索引 / 环境信息，保证报告可解释、可复现。"""
    settings = get_settings()
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_hash": dataset.content_hash,
        "dataset_version": dataset.version,
        "dataset_scope": dataset.scope,
        "vault_scope": datasets.current_scope(),
        "dataset_cases": [case.model_dump(mode='json') for case in dataset.cases],
        "modes": [m.value for m in request.modes],
        "retrieval": request.retrieval.model_dump(),
        "repeat": request.repeat,
        "embedding": {"policy": "per_case", "details": "cases[].embedding"},
        "local_embedding": {
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


async def _validate_index_compatibility(request: RAGRunRequest) -> None:
    """创建 RAG Run 前校验索引已建立且与当前 Embedding 模型/维度兼容。

    空索引或不兼容索引会让所有模式得到全 0 指标，把环境/索引错误误判为检索质量差，
    故在创建时即拒绝，返回 BENCHMARK_INDEX_INCOMPATIBLE。
    """
    stats = repository.stats()
    meta = repository.get_index_meta()
    needs_vector = any(m in (SearchMode.vector, SearchMode.hybrid) for m in request.modes)

    reasons: list[str] = []
    if stats["blocks"] == 0:
        reasons.append("index is empty (no indexed blocks; run /api/index/rebuild first)")
    from app.local_models.runtime import LocalEmbedding
    if needs_vector and isinstance(engine.embedding, LocalEmbedding):
        from app.retrieval import routed_vectors
        if await routed_vectors.search_remote("索引可用性检查", top_k=1, accept_local=True) is None:
            reasons.append("current semantic model space has no complete index")
    elif needs_vector:
        if meta.get("embedding_model") != engine.embedding.model_id:
            reasons.append(
                f"embedding model mismatch: index={meta.get('embedding_model')!r}, "
                f"engine={engine.embedding.model_id!r}"
            )
        if meta.get("embedding_dim") != str(engine.embedding.dim):
            reasons.append(
                f"embedding dimension mismatch: index={meta.get('embedding_dim')!r}, "
                f"engine={engine.embedding.dim}"
            )
        if await engine.vector_store.count() == 0:
            reasons.append("vector index is empty")
    if reasons:
        raise ApiError(
            409,
            "BENCHMARK_INDEX_INCOMPATIBLE",
            "Benchmark index is not built or is incompatible with the current retrieval engine.",
            {"reasons": reasons},
        )


async def create_rag_run(request: RAGRunRequest) -> BenchmarkRun:
    """创建一次 RAG Benchmark，立即返回 queued 的 BenchmarkRun，由后台 Task 执行。"""
    datasets.check_scope(request.expected_vault_id)
    dataset = datasets.load_dataset(request.dataset_id, BenchmarkKind.rag)
    if get_settings().environment == 'desktop':
        from app.services import desktop_projection
        await desktop_projection.refresh()
    datasets.resolve_note_paths(dataset)
    await _validate_index_compatibility(request)

    # 容量检查：先淘汰终态 run 腾空间；满容量且全为活动 run 时拒绝创建
    if not _evict_terminal():
        raise ApiError(
            429,
            "BENCHMARK_CAPACITY_EXCEEDED",
            "Benchmark run capacity exceeded; wait for active runs to finish.",
            {},
        )

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
    _runs[run_id] = run
    _events[run_id] = []
    _subscribers[run_id] = []
    _cancel_flags[run_id] = asyncio.Event()
    try:
        await _persist(run_id)
    except BaseException:
        _forget(run_id)
        raise
    _tasks[run_id] = asyncio.create_task(_execute_rag(run_id, request, dataset))
    return run


async def _execute_rag(
    run_id: str, request: RAGRunRequest, dataset: RAGDataset
) -> None:
    """后台执行 RAG Benchmark，实时更新进度/事件，结束后写入报告并关闭订阅。"""
    cancel_event = _cancel_flags[run_id]

    total = len(request.modes) * len(dataset.cases) * request.repeat

    async def on_case(result: RAGCaseResult, done: int, _total: int) -> None:
        progress = done / total if total else 1.0
        run = _runs[run_id].model_copy(update={"progress": progress})
        await _emit(run_id, BenchmarkEventType.case_completed, result.model_dump(mode="json"), run=run)

    try:
        await _emit(run_id, BenchmarkEventType.run_started,
            {"dataset_id": dataset.dataset_id, "modes": [m.value for m in request.modes]},
            run=_runs[run_id].model_copy(update={"status": BenchmarkStatus.running, "started_at": _now()}))
        metrics_by_mode, results = await run_rag(
            dataset,
            request,
            on_case=on_case,
            should_cancel=cancel_event.is_set,
        )
    except (BenchmarkCancelled, asyncio.CancelledError):
        await _finish(run_id, BenchmarkStatus.cancelled)
        return
    except Exception:  # 单次运行失败不拖垮服务，记录错误后结束
        # 详细异常只进日志，公开响应仅带项目错误码与安全消息，避免泄露路径/SQL 等敏感信息
        logger.exception("Benchmark run failed: run_id=%s", run_id)
        await _finish(run_id, BenchmarkStatus.failed, error_code='BENCHMARK_RUN_FAILED')
        return

    metrics = {mode: m.model_dump() for mode, m in metrics_by_mode.items()}
    await _finish(run_id, BenchmarkStatus.completed, metrics, results)


def list_runs(
    kind: BenchmarkKind | None = None,
    status: BenchmarkStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BenchmarkRun], int]:
    scope = datasets.current_scope()
    for run_id in storage.unfinished_ids():
        _recover(run_id)
    # Recover interrupted records before filtering by state; terminal historical
    # rows stay on disk instead of growing the live task cache.
    live = [run for run in list(_runs.values()) if run.config_snapshot.get('vault_scope', scope) == scope]
    existing = storage.existing_ids([run.run_id for run in live])
    extra = [storage.summary(run) for run in live if run.run_id not in existing
             and (kind is None or run.kind == kind) and (status is None or run.status == status)]
    if not extra:
        return storage.list_runs(kind, status, limit, offset)
    # Compatibility for legacy process-local fixtures: paginate the merged order,
    # rather than appending the same entries to every persisted page.
    runs, total = storage.list_runs(kind, status, limit + offset, 0)
    runs += extra
    runs.sort(key=lambda run: (run.created_at, run.run_id), reverse=True)
    return runs[offset:offset+limit], total + len(extra)


def get_run(run_id: str) -> BenchmarkRun | None:
    run = _runs.get(run_id)
    if run is None:
        _recover(run_id)
        run = storage.get_summary(run_id)
    scope = datasets.current_scope()
    return storage.summary(run) if run and run.config_snapshot.get('vault_scope', scope) == scope else None


def get_report(run_id: str) -> BenchmarkReport | None:
    return (_reports.get(run_id) or _saved(run_id)[1]) if get_run(run_id) else None


def get_events(run_id: str) -> list[BenchmarkEvent]:
    return (_events.get(run_id) or storage.events(run_id)) if get_run(run_id) else []


def cancel_run(run_id: str) -> BenchmarkRun | None:
    """取消运行：对 queued/running 设置取消标志，后台 Task 在 Case 边界检查后置为 cancelled。"""
    run = get_run(run_id)
    if run is None:
        return None
    if run.status in (BenchmarkStatus.queued, BenchmarkStatus.running):
        _cancel_flags[run_id].set()
    return run


def subscribe(run_id: str) -> asyncio.Queue[BenchmarkEvent | None] | None:
    """订阅运行事件流；运行已结束（completed/failed/cancelled）时返回 None。"""
    run = get_run(run_id)
    if run is None or run.status in (
        BenchmarkStatus.completed,
        BenchmarkStatus.failed,
        BenchmarkStatus.cancelled,
    ):
        return None
    queue: asyncio.Queue[BenchmarkEvent | None] = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue[BenchmarkEvent | None]) -> None:
    subscribers = _subscribers.get(run_id)
    if subscribers and queue in subscribers:
        subscribers.remove(queue)


async def wait_for_run(run_id: str) -> BenchmarkRun:
    """等待后台任务结束（测试/轮询用）；无任务时直接返回当前状态。"""
    task = _tasks.get(run_id)
    if task is not None:
        await task
    return _runs.get(run_id)


async def shutdown():
    loop = asyncio.get_running_loop()
    active = {rid: task for rid, task in _tasks.items() if not task.done() and task.get_loop() is loop}
    for rid in active:
        flag = _cancel_flags.get(rid)
        if flag: flag.set()
    await asyncio.gather(*active.values(), return_exceptions=True)
