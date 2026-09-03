"""混合检索引擎：编排 FTS5 / Vector / RRF / Reranker / Metadata Filter / Citation。

对调用方（搜索页、RAG Engine、Agent Tool）暴露统一的 search(request) -> SearchResponse。
引擎只依赖 VectorStore / EmbeddingProvider / RerankerProvider 抽象与 Repository，
不直接拼接 vec0 内部 SQL，也不向前端输出聊天文本。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import repository
from app.contracts import (
    Citation,
    PageMeta,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from app.repository import BlockHit
from app.retrieval.embedding import EmbeddingProvider, HashEmbeddingProvider
from app.retrieval.hybrid import normalize_scores, rrf_fuse
from app.retrieval.reranker import LexicalReranker, RankedCandidate, RerankerProvider
from app.retrieval import routed_vectors
from app.retrieval.vectorstore import SqliteVecStore, VectorStore
from app.textutils import make_snippet, match_query

# 每个通道的候选池大小；真实规模上来后按 Retrieval Config 调整
CANDIDATE_POOL = 50
# 分页窗口上限：候选池至少覆盖 offset+limit，但设上限防止超大 offset 撑爆内存
MAX_CANDIDATE_POOL = 200
# 带 metadata 过滤时放大召回倍数，缓解「先截断候选池再过滤」造成的漏召回
OVERSCAN_FACTOR = 4


class RetrievalEngine:
    def __init__(
        self,
        embedding: EmbeddingProvider,
        reranker: RerankerProvider,
        vector_store: VectorStore,
        *,
        route_embeddings: bool = False,
    ) -> None:
        self.embedding = embedding
        self.reranker = reranker
        self.vector_store = vector_store
        # Only the production instance opts in. Replaced test dependencies must
        # remain authoritative, including monkeypatches on the singleton.
        self._routed_defaults = (embedding, vector_store) if route_embeddings else None

    async def search(self, request: SearchRequest) -> SearchResponse:
        if request.mode == SearchMode.fts:
            return self._search_fts(request)

        has_filters = bool(
            request.folders or request.note_ids or request.tags
            or request.created_from or request.created_to
            or request.updated_from or request.updated_to
        )
        # 候选池至少覆盖本次请求的 offset+limit，保证分页能取到目标页；设上限防内存失控
        window = min(request.offset + request.limit, MAX_CANDIDATE_POOL)
        pool_size = max(CANDIDATE_POOL, window)
        # 带过滤时放大召回；FTS 则一次性取全量命中（≤FTS_FETCH_LIMIT）避免截断漏召回
        recall = min(pool_size * OVERSCAN_FACTOR, MAX_CANDIDATE_POOL) if has_filters else pool_size

        # 1. 按模式收集候选（FTS 与 Vector 各产出「按相关性降序」的 block_id 列表）
        fts_ranked: list[str] = []
        vec_ranked: list[str] = []
        fts_scores: dict[str, float] = {}
        vec_scores: dict[str, float] = {}

        if request.mode in (SearchMode.fts, SearchMode.hybrid):
            match = match_query(request.query)
            if match:
                fts_hits = repository.fts_search(match, recall)
                fts_ranked = [h.block_id for h in fts_hits]
                # bm25 越小越相关，取反后统一为「越大越相关」
                fts_scores = {h.block_id: -h.bm25 for h in fts_hits}

        if request.mode in (SearchMode.vector, SearchMode.hybrid):
            vec_hits = None
            if (
                self._routed_defaults is not None
                and self.embedding is self._routed_defaults[0]
                and self.vector_store is self._routed_defaults[1]
            ):
                vec_hits = await routed_vectors.search_remote(request.query, top_k=recall)
            if vec_hits is None:
                query_vec = await self.embedding.embed_query(request.query)
                vec_hits = await self.vector_store.search(query_vec, top_k=recall)
            vec_ranked = [v.id for v in vec_hits]
            vec_scores = {v.id: v.score for v in vec_hits}

        if request.mode == SearchMode.fts:
            candidate_scores = fts_scores
        elif request.mode == SearchMode.vector:
            candidate_scores = vec_scores
        else:  # hybrid：RRF 融合
            candidate_scores = rrf_fuse([fts_ranked, vec_ranked])

        if not candidate_scores:
            return self._empty(request)

        # 2. 取完整 Block 上下文（用于过滤、摘要与 Citation 定位）
        hits = {h.block_id: h for h in repository.get_block_hits(list(candidate_scores.keys()))}

        # 3. Metadata Filter
        filtered = [h for h in hits.values() if self._matches(h, request)]
        if not filtered:
            return self._empty(request)

        # 4. 排序 / 精排
        if request.mode == SearchMode.hybrid:
            candidates = [
                RankedCandidate(block_id=h.block_id, score=candidate_scores[h.block_id], text=h.content)
                for h in filtered
            ]
            ranked = await self.reranker.rerank(request.query, candidates)
            ordered = [(c.block_id, c.score) for c in ranked]
        else:
            ordered = sorted(
                ((h.block_id, candidate_scores[h.block_id]) for h in filtered),
                key=lambda item: -item[1],
            )

        ordered = normalize_scores(ordered)

        # 5. 分页：total = 过滤后候选集大小。fts 已取全量（≤FTS_FETCH_LIMIT）故为真实命中数；
        #    vector/hybrid 为 KNN 候选集，无全局 total。
        total = len(ordered)
        page = ordered[request.offset : request.offset + request.limit]
        items = [self._build_result(hits[block_id], request, score) for block_id, score in page]
        return SearchResponse(
            query=request.query,
            mode=request.mode,
            items=items,
            page=PageMeta(total=total, limit=request.limit, offset=request.offset),
        )

    def _search_fts(self, request: SearchRequest) -> SearchResponse:
        """FTS 专用路径：过滤、COUNT 与分页全部在 SQLite 中完成。"""
        match = match_query(request.query)
        if not match:
            return self._empty(request)

        fts_hits, total = repository.fts_search_page(
            match=match,
            limit=request.limit,
            offset=request.offset,
            folders=request.folders,
            note_ids=request.note_ids,
            tags=request.tags,
            created_from=request.created_from,
            created_to=request.created_to,
            updated_from=request.updated_from,
            updated_to=request.updated_to,
        )
        if not fts_hits:
            return SearchResponse(
                query=request.query,
                mode=request.mode,
                page=PageMeta(total=total, limit=request.limit, offset=request.offset),
            )

        hits = {h.block_id: h for h in repository.get_block_hits([hit.block_id for hit in fts_hits])}
        ordered = normalize_scores(
            [(hit.block_id, -hit.bm25) for hit in fts_hits if hit.block_id in hits]
        )
        items = [self._build_result(hits[block_id], request, score) for block_id, score in ordered]
        return SearchResponse(
            query=request.query,
            mode=request.mode,
            items=items,
            page=PageMeta(total=total, limit=request.limit, offset=request.offset),
        )

    def _matches(self, hit: BlockHit, request: SearchRequest) -> bool:
        if request.folders and hit.folder not in request.folders:
            return False
        if request.note_ids and hit.note_id not in request.note_ids:
            return False
        if request.tags and not (set(hit.tags) & set(request.tags)):
            return False
        if request.created_from and _utc(hit.created_at) < _utc(request.created_from):
            return False
        if request.created_to and _utc(hit.created_at) > _utc(request.created_to):
            return False
        if request.updated_from and _utc(hit.updated_at) < _utc(request.updated_from):
            return False
        if request.updated_to and _utc(hit.updated_at) > _utc(request.updated_to):
            return False
        return True

    def _build_result(self, hit: BlockHit, request: SearchRequest, score: float) -> SearchResult:
        citation = Citation(
            citation_id=f"cit_{hit.block_id}",
            note_id=hit.note_id,
            block_id=hit.block_id,
            file_path=hit.file_path,
            heading_path=hit.heading_path,
            start_offset=hit.start_offset,
            end_offset=hit.end_offset,
        )
        snippet = make_snippet(hit.content, request.query) if request.include_snippet else None
        return SearchResult(
            note_id=hit.note_id,
            block_id=hit.block_id,
            title=hit.title,
            file_path=hit.file_path,
            heading_path=hit.heading_path,
            snippet=snippet,
            score=score,
            citation=citation,
        )

    def _empty(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            query=request.query,
            mode=request.mode,
            page=PageMeta(total=0, limit=request.limit, offset=request.offset),
        )


def _utc(dt: datetime) -> datetime:
    """把时间统一到 naive UTC 再比较，避免 aware/naive 混用报错。"""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


# 默认引擎实例：轻量实现跑通链路，后续可替换真实模型实现
engine = RetrievalEngine(
    HashEmbeddingProvider(), LexicalReranker(), SqliteVecStore(), route_embeddings=True,
)
