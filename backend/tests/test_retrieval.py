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
