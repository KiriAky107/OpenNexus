"""混合检索引擎：编排 FTS5 / Vector / RRF / Reranker / Metadata Filter / Citation。

对调用方（搜索页、RAG Engine、Agent Tool）暴露统一的 search(request) -> SearchResponse。
引擎只依赖 VectorStore / EmbeddingProvider / RerankerProvider 抽象与 Repository，
不直接拼接 vec0 内部 SQL，也不向前端输出聊天文本。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import repository
from app.retrieval.activity import track_search
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
from app.local_models.runtime import LocalEmbedding
from app.retrieval.hybrid import normalize_scores, rrf_fuse
from app.retrieval.reranker import LexicalReranker, RankedCandidate, RerankerProvider
from app.retrieval import routed_vectors
from app.retrieval.provenance import record_embedding
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

    @track_search
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
        # 带过滤时放大召回，缓解「先截断候选池再过滤」造成的漏召回
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
            record_embedding(source="unavailable")
            vec_hits = None
            if (
                self._routed_defaults is not None
                and self.embedding is self._routed_defaults[0]
                and self.vector_store is self._routed_defaults[1]
            ):
                vec_hits = await routed_vectors.search_remote(
                    request.query, top_k=recall,
                    accept_local=isinstance(self.embedding, LocalEmbedding),
                    strict=isinstance(self.embedding, LocalEmbedding) and request.mode == SearchMode.vector,
                )
            if vec_hits is None:
                if isinstance(self.embedding, LocalEmbedding):
                    if request.mode == SearchMode.hybrid:
                        return self._search_fts(request)
                    from app.errors import ApiError
                    raise ApiError(503, "EMBEDDING_UNAVAILABLE", "Embedding 服务未就绪，请检查模型路由和本地运行环境。")
                query_vec = await self.embedding.embed_query(request.query)
                vec_hits = await self.vector_store.search(query_vec, top_k=recall)
                record_embedding(source="local", model_id=self.embedding.model_id,
                                 dimensions=self.embedding.dim, version=self.embedding.version)
            vec_ranked = [v.id for v in vec_hits]
            vec_scores = {v.id: v.score for v in vec_hits}

        if request.mode == SearchMode.fts:
            candidate_scores = fts_scores
        elif request.mode == SearchMode.vector:
            candidate_scores = vec_scores
        else:  # hybrid：RRF 融合
            if request.fusion == 'weighted':
                # 两路原始分值量纲不同，先各自归一化再等权融合，避免任一路分值范围支配结果。
                fts_normal = dict(normalize_scores(list(fts_scores.items())))
                vec_normal = dict(normalize_scores(list(vec_scores.items())))
                candidate_scores = {bid: .5 * fts_normal.get(bid, 0) + .5 * vec_normal.get(bid, 0)
                    for bid in dict.fromkeys(fts_ranked + vec_ranked)}
            else:
                candidate_scores = rrf_fuse([fts_ranked, vec_ranked], k=request.rrf_k)

        if not candidate_scores:
            return self._empty(request)

        # 2. 取完整 Block 上下文（用于过滤、摘要与 Citation 定位）
        hits = {h.block_id: h for h in repository.get_block_hits(list(candidate_scores.keys()))}

        # 3. Metadata Filter
        filtered = [h for h in hits.values() if self._matches(h, request)]
        if not filtered:
            return self._empty(request)

        # 4. 排序 / 精排：hybrid 先按融合分预排序，再对前 rerank_candidates 个候选做精排，
        #    剩余候选按融合分排在精排结果之后；rerank=False 时跳过精排直接按融合分排序。
        if request.mode == SearchMode.hybrid:
            pre_sorted = sorted(filtered, key=lambda h: -candidate_scores[h.block_id])
            if request.rerank:
                limit = request.rerank_candidates
                pool = pre_sorted if limit is None else pre_sorted[:limit]
                rest = [] if limit is None else pre_sorted[limit:]
                candidates = [
                    RankedCandidate(block_id=h.block_id, score=candidate_scores[h.block_id], text=h.content)
                    for h in pool
                ]
                ranked = await self.reranker.rerank(request.query, candidates)
                ordered = [(c.block_id, c.score) for c in ranked]
                ordered += [(h.block_id, candidate_scores[h.block_id]) for h in rest]
            else:
                ordered = [(h.block_id, candidate_scores[h.block_id]) for h in pre_sorted]
        else:
            ordered = sorted(
                ((h.block_id, candidate_scores[h.block_id]) for h in filtered),
                key=lambda item: -item[1],
            )

        ordered = normalize_scores(ordered)
        # score_threshold：归一化后过滤低分结果（默认 0 不过滤）
        ordered = [(bid, score) for bid, score in ordered if score >= request.score_threshold]

        # 5. 分页：total = 过滤后候选集大小。fts 走数据库精确分页，total 为真实命中数；
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
        """FTS 专用路径：在数据库侧完成过滤、计数与分页，不取全量后再截断。

        阈值过滤时，min-max 归一化是 bm25 的线性函数，据此把 score_threshold 换算为
        bm25 截止值（bm25_max），使过滤、计数与分页口径一致；无阈值时走数据库原生分页，
        total 始终为过滤后的真实命中数，不再受固定截断影响。
        """
        match = match_query(request.query)
        if not match:
            return self._empty(request)

        bounds = repository.fts_score_bounds(
            match=match,
            folders=request.folders,
            note_ids=request.note_ids,
            tags=request.tags,
            created_from=request.created_from,
            created_to=request.created_to,
            updated_from=request.updated_from,
            updated_to=request.updated_to,
        )
        if bounds is None:
            return self._empty(request)

        lo, hi = bounds
        span = hi - lo
        bm25_max: float | None = None
        if request.score_threshold > 0:
            if span == 0:
                # 全部命中 bm25 相同，归一化后皆为 1.0；阈值超过 1.0 时无命中
                if request.score_threshold > 1.0:
                    return self._empty(request)
            else:
                # norm = (hi - bm25) / span；norm >= threshold ⟺ bm25 <= hi - threshold * span
                bm25_max = hi - request.score_threshold * span

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
            bm25_max=bm25_max,
        )
        if not fts_hits:
            # 本页无结果：offset 越过末页时 total 仍为真实命中数（>0），需保留而非归零
            return SearchResponse(
                query=request.query,
                mode=request.mode,
                items=[],
                page=PageMeta(total=total, limit=request.limit, offset=request.offset),
            )

        # 分数按全局 bm25 上下界归一化（与取全量后 normalize_scores 等价），保证跨页一致
        span = hi - lo
        if span == 0:
            ordered = [(hit.block_id, 1.0) for hit in fts_hits]
        else:
            ordered = [(hit.block_id, round((hi - hit.bm25) / span, 6)) for hit in fts_hits]
        hits = {h.block_id: h for h in repository.get_block_hits([bid for bid, _ in ordered])}
        items = [
            self._build_result(hits[block_id], request, score)
            for block_id, score in ordered
            if block_id in hits
        ]
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
    LocalEmbedding(), LexicalReranker(), SqliteVecStore(), route_embeddings=True,
)
