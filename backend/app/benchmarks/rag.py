"""RAG Benchmark Runner：调用检索引擎对数据集逐 Case 求值并聚合指标。

只读操作，直接复用 app.retrieval.engine 的 search()，不旁路检索链路。指标按
(mode, case, repeat) 逐样本计算，再按 mode 聚合；失败样本保留在报告中但不计入汇总，
避免异常样本污染指标。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.benchmarks import metrics as m
from app.benchmarks.datasets import RAGDataset
from app.contracts import (
    RAGCaseResult,
    RAGDatasetCase,
    RAGMetrics,
    RAGRunRequest,
    SearchMode,
    SearchRequest,
)
from app.retrieval.engine import engine


class BenchmarkCancelled(Exception):
    """运行在 Case 之间被取消时抛出，用于中断后台执行并标记 cancelled。"""


async def run_rag(
    dataset: RAGDataset,
    request: RAGRunRequest,
    on_case: Callable[[RAGCaseResult, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[dict[str, RAGMetrics], list[RAGCaseResult]]:
    """执行 RAG Benchmark，返回 (按 mode 聚合的指标, 全部逐样本结果)。

    on_case 在每个样本求值完成后回调 (result, done, total)，供上层更新进度与事件。
    should_cancel 在每个样本开始前被检查；返回 True 时抛出 BenchmarkCancelled 中断运行。
    """
    total = len(request.modes) * len(dataset.cases) * request.repeat
    done = 0
    results: list[RAGCaseResult] = []

    for mode in request.modes:
        for case in dataset.cases:
            for repeat in range(request.repeat):
                if should_cancel is not None and should_cancel():
                    raise BenchmarkCancelled()
                result = await _evaluate_one(case, mode, request, repeat)
                results.append(result)
                done += 1
                if on_case is not None:
                    on_case(result, done, total)

    metrics_by_mode = {mode.value: _aggregate(results, mode) for mode in request.modes}
    return metrics_by_mode, results


async def _evaluate_one(
    case: RAGDatasetCase, mode: SearchMode, request: RAGRunRequest, repeat: int
) -> RAGCaseResult:
    search_request = SearchRequest(
        query=case.query,
        mode=mode,
        limit=request.retrieval.top_k,
        include_snippet=False,
        rrf_k=request.retrieval.rrf_k,
        rerank=request.retrieval.rerank,
        rerank_candidates=request.retrieval.rerank_candidates,
        score_threshold=request.retrieval.score_threshold,
    )
    start = time.perf_counter()
    try:
        response = await engine.search(search_request)
        latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # 单个样本失败不中断整个 Benchmark
        return RAGCaseResult(
            case_id=case.case_id,
            mode=mode,
            repeat=repeat,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            error=str(exc),
        )

    retrieved_note_ids = [item.note_id for item in response.items]
    retrieved_block_ids = [item.block_id for item in response.items]
    expected_notes = set(case.expected_note_ids)
    expected_blocks = set(case.expected_block_ids)
    k = request.retrieval.top_k

    return RAGCaseResult(
        case_id=case.case_id,
        mode=mode,
        repeat=repeat,
        latency_ms=latency_ms,
        retrieved_note_ids=retrieved_note_ids,
        retrieved_block_ids=retrieved_block_ids,
        hit_at_1=m.hit_at_k(retrieved_note_ids, expected_notes, 1),
        hit_at_5=m.hit_at_k(retrieved_note_ids, expected_notes, 5),
        recall=m.recall_at_k(retrieved_note_ids, expected_notes, k),
        reciprocal_rank=m.reciprocal_rank(retrieved_note_ids, expected_notes),
        citation_hit=m.citation_hit(retrieved_block_ids, expected_blocks),
        citation_applicable=case.citation_required,
    )


def _aggregate(cases: list[RAGCaseResult], mode: SearchMode) -> RAGMetrics:
    samples = [c for c in cases if c.mode == mode]
    ok = [c for c in samples if c.error is None]
    if not ok:
        return RAGMetrics()

    latencies = [c.latency_ms for c in ok]
    # citation_hit_rate 只统计声明了 expected_block_ids 的样本
    citation_samples = [c for c in ok if c.citation_applicable]
    return RAGMetrics(
        hit_at_1=m.mean([1.0 if c.hit_at_1 else 0.0 for c in ok]),
        hit_at_5=m.mean([1.0 if c.hit_at_5 else 0.0 for c in ok]),
        recall_at_k=m.mean([c.recall for c in ok]),
        mrr=m.mean([c.reciprocal_rank for c in ok]),
        citation_hit_rate=m.mean([1.0 if c.citation_hit else 0.0 for c in citation_samples]),
        p50_latency_ms=m.percentile(latencies, 50.0),
        p95_latency_ms=m.percentile(latencies, 95.0),
    )
