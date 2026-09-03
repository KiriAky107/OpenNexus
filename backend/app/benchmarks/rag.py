"""RAG Benchmark Runner：调用检索引擎对数据集逐 Case 求值并聚合指标。

只读操作，直接复用 app.retrieval.engine 的 search()，不旁路检索链路。指标按
(mode, case, repeat) 逐样本计算，再按 mode 聚合；失败样本按零分计入质量指标分母，
避免把执行失败误判为检索质量（同时保留 total/successful/failed/failure_rate）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app import repository
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

logger = logging.getLogger(__name__)


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
            expected_notes = _expected_notes(case)
            for repeat in range(request.repeat):
                # 让出事件循环：使运行中取消、SSE 进度与并发 API 请求能及时得到调度
                await asyncio.sleep(0)
                if should_cancel is not None and should_cancel():
                    raise BenchmarkCancelled()
                result = await _evaluate_one(case, mode, request, repeat, expected_notes)
                results.append(result)
                done += 1
                if on_case is not None:
                    on_case(result, done, total)

    metrics_by_mode = {mode.value: _aggregate(results, mode) for mode in request.modes}
    return metrics_by_mode, results


def _expected_notes(case: RAGDatasetCase) -> set[str]:
    """返回笔记级期望 id；仅标注块 ID 时从块反查所属笔记，避免把标注缺失误判为检索失败。"""
    if case.expected_note_ids:
        return set(case.expected_note_ids)
    return {hit.note_id for hit in repository.get_block_hits(case.expected_block_ids)}


async def _evaluate_one(
    case: RAGDatasetCase,
    mode: SearchMode,
    request: RAGRunRequest,
    repeat: int,
    expected_notes: set[str],
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
        # 详细异常只进日志，公开响应只带项目错误码与安全消息，避免泄露路径/SQL 等敏感信息
        logger.warning(
            "RAG case evaluation failed: case=%s mode=%s", case.case_id, mode.value,
            exc_info=exc,
        )
        return RAGCaseResult(
            case_id=case.case_id,
            mode=mode,
            repeat=repeat,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            citation_applicable=case.citation_required,
            error="RAG case evaluation failed.",
            error_code="BENCHMARK_CASE_EVALUATION_FAILED",
        )

    retrieved_note_ids = [item.note_id for item in response.items]
    retrieved_block_ids = [item.block_id for item in response.items]
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
    total = len(samples)
    failed = sum(1 for c in samples if c.error is not None)
    successful = total - failed
    if total == 0:
        return RAGMetrics()

    # 延迟只统计成功样本；失败样本按零分计入质量指标分母，避免汇总虚高
    latencies = [c.latency_ms for c in samples if c.error is None]
    citation_samples = [c for c in samples if c.citation_applicable]
    return RAGMetrics(
        hit_at_1=m.mean([1.0 if (c.error is None and c.hit_at_1) else 0.0 for c in samples]),
        hit_at_5=m.mean([1.0 if (c.error is None and c.hit_at_5) else 0.0 for c in samples]),
        recall_at_k=m.mean([c.recall if c.error is None else 0.0 for c in samples]),
        mrr=m.mean([c.reciprocal_rank if c.error is None else 0.0 for c in samples]),
        citation_hit_rate=m.mean(
            [1.0 if (c.error is None and c.citation_hit) else 0.0 for c in citation_samples]
        ),
        p50_latency_ms=m.percentile(latencies, 50.0),
        p95_latency_ms=m.percentile(latencies, 95.0),
        total_cases=total,
        successful_cases=successful,
        failed_cases=failed,
        failure_rate=failed / total,
    )
