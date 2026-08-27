"""RRF 排名融合与分数归一化。"""


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：对多个「按相关性降序」的 block_id 列表做排名融合。

    每个 block 的融合分 = Σ 1/(k + rank)，rank 从 1 开始。返回 block_id -> 融合分。
    """
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, block_id in enumerate(ids, start=1):
            scores[block_id] = scores.get(block_id, 0.0) + 1.0 / (k + rank)
    return scores


def normalize_scores(items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """把 (block_id, score) 列表 min-max 归一化到 [0,1]，score 越大越相关。"""
    if not items:
        return []
    values = [score for _, score in items]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return [(block_id, 1.0) for block_id, _ in items]
    return [(block_id, round((score - lo) / span, 6)) for block_id, score in items]
