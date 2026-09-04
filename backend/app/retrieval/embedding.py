"""Embedding 统一接口与轻量实现。

生产环境使用 local_models 的真实模型。特征哈希实现仅供测试显式注入。
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from app.constants import EMBEDDING_DIM
from app.textutils import tokens


@runtime_checkable
class EmbeddingProvider(Protocol):
    """统一 Embedding 接口（与文档一致）。"""

    model_id: str
    version: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, query: str) -> list[float]: ...


class HashEmbeddingProvider:
    """轻量确定性向量：特征哈希 + 符号 + L2 归一化。

    同一文本永远得到相同向量，可离线复现、无外部依赖。向量维度为 EMBEDDING_DIM，
    与 vec_blocks 建表维度一致。
    """

    model_id = "hash-v1"
    version = "1"
    dim = EMBEDDING_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, query: str) -> list[float]:
        return self._embed(query)

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokens(text):
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
