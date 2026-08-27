"""Reranker 统一接口与轻量实现。

真实默认是 BGE reranker 类 Cross-Encoder，第一阶段先用词面重叠 + 原始分数加权的
确定性精排跑通链路；后续替换实现即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.textutils import tokens


@dataclass
class RankedCandidate:
    block_id: str
    score: float
    text: str = ""  # 块正文，供轻量精排计算词面重叠


@runtime_checkable
class RerankerProvider(Protocol):
    """统一 Reranker 接口：输入候选块，输出按相关性重排后的候选块。"""

    model_id: str

    async def rerank(self, query: str, candidates: list[RankedCandidate]) -> list[RankedCandidate]: ...


class LexicalReranker:
    """轻量精排：query 与块正文的词面重叠度，与归一化后的原始分数加权求和。"""

    model_id = "lexical-v1"

    def __init__(self, lexical_weight: float = 0.5) -> None:
        self.lexical_weight = lexical_weight

    async def rerank(self, query: str, candidates: list[RankedCandidate]) -> list[RankedCandidate]:
        if not candidates:
            return []

        # 把原始分数（RRF 等）归一化到 [0,1]，便于与重叠度同量纲加权
        scores = [c.score for c in candidates]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0

        query_tokens = set(tokens(query))
        ranked: list[RankedCandidate] = []
        for c in candidates:
            norm = (c.score - lo) / span
            if query_tokens:
                overlap = len(query_tokens & set(tokens(c.text))) / len(query_tokens)
            else:
                overlap = 0.0
            final = self.lexical_weight * overlap + (1 - self.lexical_weight) * norm
            ranked.append(RankedCandidate(block_id=c.block_id, score=final, text=c.text))

        ranked.sort(key=lambda c: c.score, reverse=True)
        return ranked
