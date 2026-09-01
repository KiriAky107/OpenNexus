"""Benchmark 指标纯函数。

所有指标只依赖「按相关性降序的 retrieved id 列表」和「期望 id 集合」，不接触任何
外部状态，便于单元测试与未来 Agent Benchmark 复用。retrieved 顺序越靠前越相关。
"""

from __future__ import annotations


def hit_at_k(retrieved: list[str], expected: set[str], k: int) -> bool:
    """前 k 个结果里是否命中任意期望 id（用于 Hit@1 / Hit@5）。"""
    return any(item in expected for item in retrieved[:k])


def recall_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    """前 k 个结果召回的期望 id 占比；期望为空时视为 0。"""
    if not expected:
        return 0.0
    hits = sum(1 for item in retrieved[:k] if item in expected)
    return hits / len(expected)


def reciprocal_rank(retrieved: list[str], expected: set[str]) -> float:
    """首个命中的倒数排名；未命中返回 0。rank 从 1 开始。"""
    for rank, item in enumerate(retrieved, start=1):
        if item in expected:
            return 1.0 / rank
    return 0.0


def citation_hit(retrieved_block_ids: list[str], expected: set[str]) -> bool:
    """首条结果的 block_id 是否为期望引用块（Citation Hit Rate 的逐 Case 判据）。"""
    if not retrieved_block_ids or not expected:
        return False
    return retrieved_block_ids[0] in expected


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], p: float) -> float:
    """线性插值分位数（p ∈ [0, 100]），用于 P50 / P95 延迟。空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lo = int(rank)
    hi = lo + 1
    if hi >= len(ordered):
        return ordered[-1]
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac
