"""Benchmark 服务的单元与端到端测试。

沿用 conftest 的隔离机制：APP_DATA_DIR / DB / Vault 都指向临时目录，benchmark
数据集也落在临时目录（settings.benchmark_datasets_path），不读写真实数据。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.benchmarks import datasets, metrics as m, service
from app.config import get_settings
from app.contracts import BenchmarkKind, RAGRunRequest, SearchMode
from app.errors import ApiError


def _write_dataset(dataset_id: str, cases: list[dict], *, kind: str = "rag") -> None:
    directory = get_settings().benchmark_datasets_path
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset_id": dataset_id,
        "kind": kind,
        "version": "1.0.0",
        "description": "test dataset",
        "cases": cases,
    }
    (directory / f"{dataset_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# 指标纯函数
# --------------------------------------------------------------------------- #
def test_hit_at_k_and_recall() -> None:
    retrieved = ["a", "b", "c"]
    expected = {"b", "z"}

    assert m.hit_at_k(retrieved, expected, 1) is False
    assert m.hit_at_k(retrieved, expected, 2) is True
    assert m.recall_at_k(retrieved, expected, 5) == 0.5  # 只召回 b


def test_reciprocal_rank_and_citation_hit() -> None:
    assert m.reciprocal_rank(["x", "a", "b"], {"b"}) == 1 / 3
    assert m.reciprocal_rank(["x"], {"b"}) == 0.0
    assert m.citation_hit(["blk_1"], {"blk_1"}) is True
    assert m.citation_hit(["blk_2"], {"blk_1"}) is False
    assert m.citation_hit([], {"blk_1"}) is False


def test_percentile() -> None:
    assert m.percentile([1.0, 2.0, 3.0, 4.0], 50.0) == 2.5
    assert m.percentile([], 50.0) == 0.0
    assert m.percentile([7.0], 95.0) == 7.0


# --------------------------------------------------------------------------- #
# Dataset 注册与校验
# --------------------------------------------------------------------------- #
def test_list_datasets_empty_by_default() -> None:
    assert datasets.list_datasets(BenchmarkKind.rag) == []


def test_load_missing_dataset_raises() -> None:
    with pytest.raises(ApiError) as exc:
        datasets.load_dataset("does-not-exist", BenchmarkKind.rag)
    assert exc.value.status_code == 404
    assert exc.value.code == "BENCHMARK_DATASET_NOT_FOUND"


def test_dataset_without_expected_ids_is_invalid() -> None:
    _write_dataset("bad-v1", [{"case_id": "x", "query": "q", "citation_required": False}])
    with pytest.raises(ApiError) as exc:
        datasets.load_dataset("bad-v1", BenchmarkKind.rag)
    assert exc.value.code == "BENCHMARK_DATASET_INVALID"


def test_dataset_kind_mismatch_is_invalid() -> None:
    _write_dataset("agent-v1", [{"case_id": "x", "query": "q", "expected_note_ids": ["n"]}], kind="agent")
    with pytest.raises(ApiError) as exc:
        datasets.load_dataset("agent-v1", BenchmarkKind.rag)
    assert exc.value.code == "BENCHMARK_DATASET_INVALID"


# --------------------------------------------------------------------------- #
# RAG Benchmark 端到端
# --------------------------------------------------------------------------- #
def _single_note_case() -> tuple[str, str, dict]:
    from app.services import note_service

    note = asyncio.run(
        note_service.create_note(
            title="向量库",
            markdown="向量数据库用于存储高维向量并支持近似最近邻检索。",
            folder="",
            tags=["向量"],
        )
    )
    case = {
        "case_id": "c1",
        "query": "向量数据库相似度检索",
        "expected_note_ids": [note.note_id],
        "expected_block_ids": [note.blocks[0].block_id],
        "citation_required": True,
        "tags": ["向量"],
    }
    return note.note_id, note.blocks[0].block_id, case


def test_rag_benchmark_end_to_end() -> None:
    _, _, case = _single_note_case()
    _write_dataset("e2e-v1", [case])

    run = asyncio.run(
        service.create_rag_run(RAGRunRequest(dataset_id="e2e-v1", modes=[SearchMode.fts]))
    )

    assert run.status.value == "completed"
    assert run.dataset_hash.startswith("sha256:")
    assert run.metrics is not None

    fts = run.metrics["fts"]
    assert fts["hit_at_1"] == 1.0
    assert fts["recall_at_k"] == 1.0
    assert fts["mrr"] == 1.0
    assert fts["citation_hit_rate"] == 1.0
    assert fts["p50_latency_ms"] >= 0.0
    assert fts["p95_latency_ms"] >= fts["p50_latency_ms"]


def test_rag_benchmark_all_modes_produce_metrics() -> None:
    _, _, case = _single_note_case()
    _write_dataset("e2e-modes-v1", [case])

    run = asyncio.run(service.create_rag_run(RAGRunRequest(dataset_id="e2e-modes-v1")))
    assert run.status.value == "completed"

    for mode in ("fts", "vector", "hybrid"):
        assert mode in run.metrics
        for key in ("hit_at_1", "hit_at_5", "recall_at_k", "mrr", "citation_hit_rate"):
            assert 0.0 <= run.metrics[mode][key] <= 1.0


def test_benchmark_report_and_events() -> None:
    _, _, case = _single_note_case()
    _write_dataset("report-v1", [case])

    run = asyncio.run(service.create_rag_run(RAGRunRequest(dataset_id="report-v1", modes=[SearchMode.fts])))
    report = service.get_report(run.run_id)
    events = service.get_events(run.run_id)

    assert report is not None
    assert report.run_id == run.run_id
    assert len(report.cases) == 1
    assert report.cases[0].case_id == "c1"
    assert report.cases[0].hit_at_1 is True

    assert events, "运行应产生事件"
    assert events[0].event.value == "RunStarted"
    assert events[-1].event.value == "RunCompleted"


def test_cancel_completed_run_keeps_status() -> None:
    _, _, case = _single_note_case()
    _write_dataset("cancel-v1", [case])

    run = asyncio.run(service.create_rag_run(RAGRunRequest(dataset_id="cancel-v1", modes=[SearchMode.fts])))
    cancelled = service.cancel_run(run.run_id)
    assert cancelled.status.value == "completed"  # 同步运行已结束，不再变 cancelled


# --------------------------------------------------------------------------- #
# 路由接入
# --------------------------------------------------------------------------- #
def test_benchmark_routes_wired() -> None:
    from app import routes

    _, _, case = _single_note_case()
    _write_dataset("route-v1", [case])

    listed = asyncio.run(routes.list_benchmark_datasets(BenchmarkKind.rag))
    assert any(item.dataset_id == "route-v1" for item in listed.items)

    run = asyncio.run(
        routes.create_rag_benchmark(RAGRunRequest(dataset_id="route-v1", modes=[SearchMode.fts]))
    )
    assert run.status.value == "completed"

    got = asyncio.run(routes.get_benchmark_run(run.run_id))
    assert got.run_id == run.run_id

    report = asyncio.run(routes.get_benchmark_report(run.run_id))
    assert report.cases[0].case_id == "c1"


def test_benchmark_run_not_found_raises() -> None:
    from app import routes

    with pytest.raises(ApiError) as exc:
        asyncio.run(routes.get_benchmark_run("benchmark_missing"))
    assert exc.value.code == "BENCHMARK_RUN_NOT_FOUND"
