"""Knowledge / Retrieval Core 的单元与端到端测试。

端到端用例通过 monkeypatch 将 APP_DATA_DIR / APP_DB_PATH / APP_VAULT_PATH 指到临时目录，
并清理 get_settings 缓存，保证不读写 backend/data 下的真实索引，也不污染其他测试。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.contracts import IndexRebuildRequest, SearchMode, SearchRequest
from app.knowledge.parser import note_id_for_path, parse_note
from app.retrieval.embedding import HashEmbeddingProvider
from app.retrieval.hybrid import rrf_fuse
from app.textutils import match_query, tokens

MD = """---
title: 测试标题
tags: python, 检索
---

# 一级标题

这是第一段正文。

## 二级标题

第二段正文内容。
"""


def _dt() -> datetime:
    return datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def vault():
    """返回 conftest 全局隔离后的临时 Vault 目录，用于写入示例笔记。"""
    return get_settings().vault_path


# --------------------------------------------------------------------------- #
# 单元测试
# --------------------------------------------------------------------------- #
def test_parse_note_extracts_frontmatter_and_blocks() -> None:
    parsed = parse_note(
        markdown=MD, file_path="编程/测试.md", folder="编程",
        tags=None, created_at=_dt(), updated_at=_dt(),
    )

    assert parsed.title == "测试标题"
    assert parsed.tags == ["python", "检索"]
    assert parsed.note_id == note_id_for_path("编程/测试.md")

    paths = [tuple(b.heading_path) for b in parsed.blocks]
    assert ("一级标题",) in paths
    assert ("一级标题", "二级标题") in paths

    # 每个 Block 的偏移合法且内容非空
    for b in parsed.blocks:
        assert 0 <= b.start_offset <= b.end_offset
        assert b.content.strip()


def test_block_ids_are_stable() -> None:
    p1 = parse_note(markdown=MD, file_path="编程/测试.md", folder="编程",
                    tags=None, created_at=_dt(), updated_at=_dt())
    p2 = parse_note(markdown=MD, file_path="编程/测试.md", folder="编程",
                    tags=None, created_at=_dt(), updated_at=_dt())

    assert [b.block_id for b in p1.blocks] == [b.block_id for b in p2.blocks]
    # block_id 前缀符合团队约定
    assert all(b.block_id.startswith("blk_") for b in p1.blocks)


def test_tokens_split_cjk_bigrams_and_match_query() -> None:
    toks = tokens("向量检索")
    assert "向" in toks and "量" in toks
    assert "向量" in toks and "检索" in toks

    q = match_query("python 向量")
    assert '"python"' in q and '"向量"' in q


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    emb = HashEmbeddingProvider()
    v1 = asyncio.run(emb.embed_query("向量检索"))
    v2 = asyncio.run(emb.embed_query("向量检索"))

    assert v1 == v2
    assert len(v1) == emb.dim == 128
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_rrf_fuse_merges_ranked_lists() -> None:
    scores = rrf_fuse([["a", "b"], ["b", "a"]])

    assert set(scores) == {"a", "b"}
    assert scores["a"] > 0 and scores["b"] > 0


# --------------------------------------------------------------------------- #
# 端到端测试（隔离环境）
# --------------------------------------------------------------------------- #
SAMPLE_NOTES = {
    "编程/向量.md": (
        "---\ntitle: 向量数据库\ntags: 向量, 检索\n---\n\n"
        "# 向量数据库\n\n向量数据库用于存储高维向量并支持近似最近邻检索。\n"
    ),
    "编程/Python.md": (
        "---\ntitle: Python 基础\ntags: python\n---\n\n"
        "# 变量\n\nPython 是动态类型语言。\n"
    ),
    "产品/RAG.md": (
        "---\ntitle: RAG 概述\ntags: RAG\n---\n\n"
        "# RAG\n\n检索增强生成先检索相关文档块。\n"
    ),
}


def _write_vault(vault, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_rebuild_indexes_vault_and_lists_notes(vault) -> None:
    from app.services import index_service, note_service

    _write_vault(vault, SAMPLE_NOTES)

    job = asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))
    assert job.status == "completed"

    items, total = note_service.list_notes(limit=50, offset=0, folder=None, tag=None)
    assert total == 3
    assert {i.file_path for i in items} == set(SAMPLE_NOTES)


def test_search_returns_citations_for_each_mode(vault) -> None:
    from app.retrieval.engine import engine
    from app.services import index_service

    _write_vault(vault, SAMPLE_NOTES)
    asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))

    # FTS：中文词组召回，且返回可定位的 Citation
    fts = asyncio.run(engine.search(SearchRequest(query="向量数据库", mode=SearchMode.fts)))
    assert fts.page.total >= 1
    top = fts.items[0]
    assert top.citation.citation_id.startswith("cit_")
    assert top.citation.file_path == "编程/向量.md"
    assert top.citation.block_id == top.block_id

    # Vector：向量召回
    vec = asyncio.run(engine.search(SearchRequest(query="向量数据库", mode=SearchMode.vector)))
    assert vec.page.total >= 1

    # Hybrid：RRF + Reranker 融合后仍有结果
    hyb = asyncio.run(engine.search(SearchRequest(query="向量数据库", mode=SearchMode.hybrid)))
    assert hyb.page.total >= 1
    assert all(0.0 <= r.score <= 1.0 for r in hyb.items)


def test_search_metadata_filters(vault) -> None:
    from app.retrieval.engine import engine
    from app.services import index_service

    _write_vault(vault, SAMPLE_NOTES)
    asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))

    by_folder = asyncio.run(
        engine.search(SearchRequest(query="检索", mode=SearchMode.hybrid, folders=["产品"]))
    )
    assert by_folder.page.total >= 1
    assert all(r.file_path.startswith("产品/") for r in by_folder.items)

    by_tag = asyncio.run(
        engine.search(SearchRequest(query="向量", mode=SearchMode.hybrid, tags=["向量"]))
    )
    assert by_tag.page.total >= 1
    assert all("向量" in r.citation.heading_path or "向量" in r.title for r in by_tag.items)


def test_route_handlers_wired_to_services(vault) -> None:
    """验证 routes.py 里 notes/search/index 端点已接入真实服务（而非 501 壳子）。"""
    from app import routes
    from app.contracts import NoteCreateRequest

    _write_vault(vault, SAMPLE_NOTES)
    job = asyncio.run(routes.rebuild_index(IndexRebuildRequest(scope="all")))
    assert job.status == "completed"

    notes = asyncio.run(routes.list_notes(limit=50, offset=0, folder=None, tag=None))
    assert notes.page.total == 3

    result = asyncio.run(routes.search_notes(SearchRequest(query="向量数据库", mode=SearchMode.hybrid)))
    assert result.page.total >= 1
    assert result.items[0].citation.citation_id.startswith("cit_")

    created = asyncio.run(
        routes.create_note(NoteCreateRequest(title="接口测试", markdown="# 接口\n\n正文。"))
    )
    assert created.title == "接口测试"
    assert asyncio.run(routes.get_note(created.note_id)).note_id == created.note_id


def test_get_missing_note_raises_404(vault) -> None:
    from app import routes
    from app.errors import ApiError

    with pytest.raises(ApiError):
        asyncio.run(routes.get_note("note_missing"))


def test_note_crud_roundtrip(vault) -> None:
    from app.retrieval.engine import engine
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(title="新建笔记", markdown="# 标题\n\n内容。", folder="测试", tags=["测试"])
    )
    assert note.note_id.startswith("note_")
    assert note.blocks

    got = asyncio.run(note_service.get_note(note.note_id))
    assert got is not None and got.title == "新建笔记"

    updated = asyncio.run(
        note_service.update_note(note.note_id, title="改名", markdown="# 新标题\n\n检索内容。")
    )
    assert updated.title == "改名"
    assert updated.note_id == note.note_id  # 更新不改变 ID

    # 更新后可检索到新内容
    resp = asyncio.run(engine.search(SearchRequest(query="检索内容", mode=SearchMode.fts)))
    assert any(r.note_id == note.note_id for r in resp.items)

    assert asyncio.run(note_service.delete_note(note.note_id)) is True
    assert asyncio.run(note_service.get_note(note.note_id)) is None


# --------------------------------------------------------------------------- #
# 审阅回归：路径逃逸 / 部分提交回滚 / 失效向量 / 搜索分页
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("folder", ["../../outside", "..", "..\\..\\etc", "C:\\Windows", "a/../b"])
def test_create_note_rejects_path_traversal(vault, folder) -> None:
    from app.errors import ApiError
    from app.services import note_service

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            note_service.create_note(title="逃逸", markdown="# 逃逸", folder=folder, tags=[])
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_PATH"


def test_update_note_rolls_back_file_on_index_error(vault, monkeypatch) -> None:
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(title="回滚", markdown="# 原文\n\n旧内容。", folder="", tags=[])
    )
    path = vault / note.file_path
    before = path.read_text(encoding="utf-8")

    async def _boom(_contents):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(note_service.embedding, "embed_documents", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(note_service.update_note(note.note_id, markdown="# 新文\n\n新内容。"))

    assert path.read_text(encoding="utf-8") == before  # 文件已回滚，无部分提交


def test_update_note_rolls_back_index_when_index_meta_fails(vault, monkeypatch) -> None:
    """索引元信息失败时，Markdown 与完整索引都保持旧版本。"""
    from app import repository
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(title="原子更新", markdown="旧正文", folder="", tags=[])
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("index meta failed")

    monkeypatch.setattr(repository, "set_index_meta", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(note_service.update_note(note.note_id, markdown="新正文"))

    got = asyncio.run(note_service.get_note(note.note_id))
    record = repository.get_note_record(note.note_id)
    assert got is not None and got.markdown == "旧正文"
    assert record is not None
    assert [block.content for block in record.blocks] == ["旧正文"]


def test_create_note_rejects_existing_path_without_overwrite(vault) -> None:
    """POST 同目录同标题返回 409，且不改动已有 Markdown 和索引。"""
    from app.errors import ApiError
    from app.services import note_service

    original = asyncio.run(
        note_service.create_note(title="不能覆盖", markdown="原始正文", folder="测试", tags=[])
    )

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            note_service.create_note(title="不能覆盖", markdown="替换正文", folder="测试", tags=[])
        )

    assert exc.value.status_code == 409
    assert exc.value.code == "RESOURCE_CONFLICT"
    got = asyncio.run(note_service.get_note(original.note_id))
    assert got is not None and got.markdown == "原始正文"


def test_delete_note_rolls_back_when_vector_delete_fails(vault, monkeypatch) -> None:
    """向量删除失败时，笔记数据库记录和 Markdown 都恢复到删除前。"""
    from app.database.db import connect
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(title="删除回滚", markdown="待保留正文", folder="测试", tags=[])
    )
    path = vault / note.file_path

    async def _boom(_ids, **_kwargs):
        raise RuntimeError("vector delete failed")

    monkeypatch.setattr(note_service.vector_store, "delete", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(note_service.delete_note(note.note_id))

    got = asyncio.run(note_service.get_note(note.note_id))
    assert got is not None and got.markdown == "待保留正文"
    assert path.exists()
    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM vec_blocks WHERE block_id = ?", (note.blocks[0].block_id,)
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_update_removes_stale_vectors(vault) -> None:
    from app.database.db import connect
    from app.services import note_service

    def vec_count() -> int:
        conn = connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM vec_blocks").fetchone()[0]
        finally:
            conn.close()

    note = asyncio.run(
        note_service.create_note(
            title="向量清理", markdown="# 标题\n\n段落一。\n\n段落二。", folder="", tags=[]
        )
    )
    assert vec_count() == 3  # 标题 + 段落一 + 段落二

    asyncio.run(note_service.update_note(note.note_id, markdown="# 标题\n\n段落一。"))
    assert vec_count() == 2  # 段落二的旧向量被清理，不再残留


def test_search_pagination_total_reflects_all_matches(vault) -> None:
    from app.retrieval.engine import engine
    from app.services import index_service

    body = "\n\n".join(f"第{i}段 内容。" for i in range(60))
    _write_vault(vault, {"多段.md": f"# 大量段落\n\n{body}"})
    asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))

    page1 = asyncio.run(
        engine.search(SearchRequest(query="段", mode=SearchMode.fts, limit=10, offset=0))
    )
    assert page1.page.total >= 60  # total 反映真实命中数，而非候选池上限 50
    assert len(page1.items) == 10

    page2 = asyncio.run(
        engine.search(SearchRequest(query="段", mode=SearchMode.fts, limit=10, offset=55))
    )
    assert page2.items  # 跨过旧候选池边界仍能取到结果


def test_fts_pagination_is_not_truncated_at_one_thousand(vault) -> None:
    """FTS total 与分页由数据库计算，不在第 1000 个候选处截断。"""
    from app.retrieval.engine import engine
    from app.services import note_service

    markdown = "\n\n".join(f"共同词 p{i}" for i in range(1010))
    asyncio.run(
        note_service.create_note(title="千条分页", markdown=markdown, folder="", tags=[])
    )

    response = asyncio.run(
        engine.search(
            SearchRequest(query="共同词", mode=SearchMode.fts, limit=10, offset=1000)
        )
    )
    assert response.page.total == 1010
    assert len(response.items) == 10


# --------------------------------------------------------------------------- #
# 审阅回归：PATCH tags 语义 / 向量-块一致性 / 过滤漏召回 / rebuild 语义与回滚
# --------------------------------------------------------------------------- #
def test_patch_tags_semantics(vault) -> None:
    """PATCH 省略 tags 保留、tags=[] 清空、非空替换（审阅 #5）。"""
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(title="标签语义", markdown="# 标题\n\n正文。", folder="", tags=["a"])
    )
    assert note.tags == ["a"]

    updated = asyncio.run(note_service.update_note(note.note_id, title="改名"))  # tags=None
    assert updated.tags == ["a"]  # 省略 tags 保留原标签

    updated = asyncio.run(note_service.update_note(note.note_id, tags=["b"]))
    assert updated.tags == ["b"]  # 非空列表替换

    updated = asyncio.run(note_service.update_note(note.note_id, tags=[]))
    assert updated.tags == []  # 空列表清空


def test_patch_partial_content_no_orphan_vectors(vault) -> None:
    """修改正文只删部分 block 后，vec_blocks 与 blocks 的 ID 集合一致（审阅 #2/#3）。"""
    from app.database.db import connect
    from app.services import note_service

    def ids(table: str) -> set[str]:
        conn = connect()
        try:
            return {row[0] for row in conn.execute(f"SELECT block_id FROM {table}")}
        finally:
            conn.close()

    note = asyncio.run(
        note_service.create_note(
            title="部分修改", markdown="# 标题\n\n段落一。\n\n段落二。", folder="", tags=[]
        )
    )
    assert ids("vec_blocks") == ids("blocks")

    asyncio.run(
        note_service.update_note(note.note_id, markdown="# 标题\n\n段落一改了。\n\n新增段落。")
    )
    # 更新后不变量：向量集合与块集合一一对应，无残留、无缺失
    assert ids("vec_blocks") == ids("blocks")


def test_fts_metadata_filter_recalls_beyond_candidate_pool(vault) -> None:
    """metadata 过滤不能受候选池截断影响：目标块排在 50 名之外也应被召回（审阅 #4）。"""
    from app.retrieval.engine import engine
    from app.services import index_service

    files: dict[str, str] = {}
    # 60 篇短填充笔记：bm25 高，占据 FTS 前 60 位
    for i in range(60):
        files[f"批量/填充{i}.md"] = f"---\ntitle: 填充{i}\ntags: 填充\n---\n\n检索\n"
    # 目标笔记：长正文使 bm25 变低，排在候选池（50）之外
    long_body = "检索 " + "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄宇宙洪荒日月盈昃"
    files["批量/目标.md"] = f"---\ntitle: 目标\ntags: 目标\n---\n\n{long_body}\n"

    _write_vault(vault, files)
    asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))

    resp = asyncio.run(
        engine.search(SearchRequest(query="检索", mode=SearchMode.fts, tags=["目标"]))
    )
    assert resp.page.total == 1
    assert resp.items[0].title == "目标"


def test_rebuild_rejects_unsupported_scope_and_note_ids(vault) -> None:
    """增量 scope / note_ids 未实现时明确拒绝，而非静默全量重建（审阅 #6）。"""
    from app.errors import ApiError
    from app.services import index_service

    with pytest.raises(ApiError) as exc:
        asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="notes")))
    assert exc.value.status_code == 400
    assert exc.value.code == "UNSUPPORTED_SCOPE"

    with pytest.raises(ApiError) as exc:
        asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all", note_ids=["note_x"])))
    assert exc.value.code == "UNSUPPORTED_SCOPE"


def test_rebuild_failure_restores_old_index(vault, monkeypatch) -> None:
    """重建中途失败应恢复旧索引，不留下半成品（审阅 #6）。"""
    from app import repository
    from app.services import index_service
    from app.services import note_service as ns

    _write_vault(vault, SAMPLE_NOTES)
    asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))
    before = repository.stats()

    real_embed = ns.embedding.embed_documents
    call = {"n": 0}

    async def _flaky(contents):
        call["n"] += 1
        if call["n"] > 1:
            raise RuntimeError("embed down")
        return await real_embed(contents)

    monkeypatch.setattr(ns.embedding, "embed_documents", _flaky)
    with pytest.raises(RuntimeError):
        asyncio.run(index_service.rebuild(IndexRebuildRequest(scope="all")))

    assert repository.stats() == before  # 旧索引已恢复，无半成品
