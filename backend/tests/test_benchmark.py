"""Benchmark 服务的单元与端到端测试。

沿用 conftest 的隔离机制：APP_DATA_DIR / DB / Vault 都指向临时目录，benchmark
数据集也落在临时目录（settings.benchmark_datasets_path），不读写真实数据。

运行采用「创建即 queued + 后台 Task 执行」的异步模型，测试通过 _run 在同一事件循环内
创建并等待后台任务结束，得到终态 BenchmarkRun 后再断言。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.benchmarks import datasets, metrics as m, service
from app.config import get_settings
from app.contracts import (
    BenchmarkKind,
    BenchmarkRun,
    BenchmarkStatus,
    RAGRunRequest,
    SearchMode,
)
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


def _write_raw(dataset_id: str, raw: dict) -> None:
    directory = get_settings().benchmark_datasets_path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{dataset_id}.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )


def _run(request: RAGRunRequest):
    """创建运行并在同一事件循环内等待后台任务结束，返回终态 BenchmarkRun。"""
    from app.contracts import BenchmarkRun

    async def _execute() -> BenchmarkRun:
        run = await service.create_rag_run(request)
        return await service.wait_for_run(run.run_id)

    return asyncio.run(_execute())


# --------------------------------------------------------------------------- #
# 指标纯函数
# --------------------------------------------------------------------------- #
def test_hit_at_k_and_recall() -> None:
    retrieved = ["a", "b", "c"]
    expected = {"b", "z"}

    assert m.hit_at_k(retrieved, expected, 1) is False
    assert m.hit_at_k(retrieved, expected, 2) is True
    assert m.recall_at_k(retrieved, expected, 5) == 0.5  # 只召回 b


def test_recall_at_k_dedups_duplicate_notes() -> None:
    # 同一 Note 经多个 Block 重复出现，去重后 Recall 不应超过 1
    assert m.recall_at_k(["note-a", "note-a"], {"note-a"}, 2) == 1.0
    assert m.recall_at_k(["note-a", "note-a", "note-b"], {"note-a"}, 3) == 1.0


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


def test_citation_required_requires_expected_block_ids() -> None:
    # citation_required=true 却没有 expected_block_ids，无法计算 Citation Hit Rate，应拒绝
    _write_dataset(
        "cit-req-v1",
        [{"case_id": "x", "query": "q", "expected_note_ids": ["n"], "citation_required": True}],
    )
    with pytest.raises(ApiError) as exc:
        datasets.load_dataset("cit-req-v1", BenchmarkKind.rag)
    assert exc.value.code == "BENCHMARK_DATASET_INVALID"


def test_list_datasets_skips_corrupted_structure() -> None:
    # 合法 JSON 但字段结构错误（cases: 42），列表接口应隔离该文件而非整体 500
    _write_raw("bad-structure", {"dataset_id": "bad-structure", "kind": "rag", "cases": 42})
    _write_dataset("good-v1", [{"case_id": "x", "query": "q", "expected_note_ids": ["n"]}])

    infos = datasets.list_datasets(BenchmarkKind.rag)
    ids = {info.dataset_id for info in infos}
    assert "good-v1" in ids
    assert "bad-structure" not in ids


# --------------------------------------------------------------------------- #
# 请求校验（空 / 重复 modes）
# --------------------------------------------------------------------------- #
def test_empty_modes_rejected() -> None:
    with pytest.raises(ValidationError):
        RAGRunRequest(dataset_id="x", modes=[])


def test_duplicate_modes_rejected() -> None:
    with pytest.raises(ValidationError):
        RAGRunRequest(dataset_id="x", modes=[SearchMode.fts, SearchMode.fts])


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

    run = _run(RAGRunRequest(dataset_id="e2e-v1", modes=[SearchMode.fts]))

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

    run = _run(RAGRunRequest(dataset_id="e2e-modes-v1"))
    assert run.status.value == "completed"

    for mode in ("fts", "vector", "hybrid"):
        assert mode in run.metrics
        for key in ("hit_at_1", "hit_at_5", "recall_at_k", "mrr", "citation_hit_rate"):
            assert 0.0 <= run.metrics[mode][key] <= 1.0


def test_config_snapshot_records_index_and_models() -> None:
    _, _, case = _single_note_case()
    _write_dataset("snapshot-v1", [case])

    run = _run(RAGRunRequest(dataset_id="snapshot-v1", modes=[SearchMode.fts]))

    snapshot = run.config_snapshot
    assert snapshot["index_meta"] is not None
    assert snapshot["embedding"]["version"]
    assert snapshot["embedding"]["dim"]
    assert snapshot["reranker"]["version"]
    assert snapshot["retrieval"]["rrf_k"] == 60


def test_benchmark_report_and_events() -> None:
    _, _, case = _single_note_case()
    _write_dataset("report-v1", [case])

    run = _run(RAGRunRequest(dataset_id="report-v1", modes=[SearchMode.fts]))
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

    run = _run(RAGRunRequest(dataset_id="cancel-v1", modes=[SearchMode.fts]))
    assert run.status.value == "completed"

    cancelled = service.cancel_run(run.run_id)
    assert cancelled.status.value == "completed"  # 已结束，不再变 cancelled


def test_cancel_queued_run_marks_cancelled() -> None:
    _, _, case = _single_note_case()
    _write_dataset("cancel-queued-v1", [case])

    async def _scenario():
        run = await service.create_rag_run(
            RAGRunRequest(dataset_id="cancel-queued-v1", modes=[SearchMode.fts])
        )
        service.cancel_run(run.run_id)
        return await service.wait_for_run(run.run_id)

    run = asyncio.run(_scenario())
    assert run.status.value == "cancelled"


# --------------------------------------------------------------------------- #
# 指标聚合：Citation Hit Rate 只统计 citation_required 样本
# --------------------------------------------------------------------------- #
def test_citation_hit_rate_only_counts_citation_required() -> None:
    from app.benchmarks import rag as rag_module
    from app.contracts import RAGCaseResult

    cases = [
        RAGCaseResult(
            case_id="a", mode=SearchMode.fts, repeat=0, latency_ms=1.0,
            citation_hit=True, citation_applicable=True,
        ),
        RAGCaseResult(
            case_id="b", mode=SearchMode.fts, repeat=0, latency_ms=1.0,
            citation_hit=False, citation_applicable=False,
        ),
    ]
    metrics = rag_module._aggregate(cases, SearchMode.fts)
    # 只有 citation_applicable（citation_required=true）的样本计入分母
    assert metrics.citation_hit_rate == 1.0


# --------------------------------------------------------------------------- #
# 路由接入
# --------------------------------------------------------------------------- #
def test_benchmark_routes_wired() -> None:
    from app import routes

    _, _, case = _single_note_case()
    _write_dataset("route-v1", [case])

    async def _scenario():
        listed = await routes.list_benchmark_datasets(BenchmarkKind.rag)
        assert any(item.dataset_id == "route-v1" for item in listed.items)

        run = await routes.create_rag_benchmark(
            RAGRunRequest(dataset_id="route-v1", modes=[SearchMode.fts])
        )
        assert run.status.value == "queued"
        return await service.wait_for_run(run.run_id)

    run = asyncio.run(_scenario())
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


# --------------------------------------------------------------------------- #
# 审阅回归：索引兼容 / 容量 / 失败样本 / 取消事件 / 数据集隔离
# --------------------------------------------------------------------------- #
def test_create_rag_run_requires_built_index() -> None:
    # 空索引（无已索引 block）会让所有模式得到全 0 指标，应在创建时拒绝而非跑出误导结果
    _write_dataset("empty-index-v1", [{"case_id": "x", "query": "q", "expected_note_ids": ["n"]}])
    with pytest.raises(ApiError) as exc:
        asyncio.run(
            service.create_rag_run(
                RAGRunRequest(dataset_id="empty-index-v1", modes=[SearchMode.fts])
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "BENCHMARK_INDEX_INCOMPATIBLE"


def test_capacity_exceeded_when_all_runs_active(monkeypatch) -> None:
    # 满容量且全为活动（非终态）run 时，无法淘汰，应拒绝创建而非删掉正在运行的 run
    _, _, case = _single_note_case()
    _write_dataset("capacity-v1", [case])

    monkeypatch.setattr(service, "MAX_RUNS", 1)
    fake_id = "benchmark_fake_active"
    service._runs[fake_id] = BenchmarkRun(
        run_id=fake_id,
        kind=BenchmarkKind.rag,
        dataset_id="capacity-v1",
        dataset_hash="sha256:fake",
        status=BenchmarkStatus.queued,
        created_at=service._now(),
    )
    try:
        with pytest.raises(ApiError) as exc:
            asyncio.run(
                service.create_rag_run(
                    RAGRunRequest(dataset_id="capacity-v1", modes=[SearchMode.fts])
                )
            )
        assert exc.value.status_code == 429
        assert exc.value.code == "BENCHMARK_CAPACITY_EXCEEDED"
    finally:
        service._runs.pop(fake_id, None)


def test_failed_samples_counted_as_zero_in_aggregate() -> None:
    from app.benchmarks import rag as rag_module
    from app.contracts import RAGCaseResult

    cases = [
        RAGCaseResult(
            case_id="ok", mode=SearchMode.fts, repeat=0, latency_ms=10.0,
            hit_at_1=True, recall=1.0, reciprocal_rank=1.0,
            citation_hit=True, citation_applicable=True,
        ),
        RAGCaseResult(
            case_id="boom", mode=SearchMode.fts, repeat=0, latency_ms=0.0,
            error="RAG case evaluation failed.",
            error_code="BENCHMARK_CASE_EVALUATION_FAILED",
        ),
    ]
    metrics = rag_module._aggregate(cases, SearchMode.fts)

    assert metrics.total_cases == 2
    assert metrics.successful_cases == 1
    assert metrics.failed_cases == 1
    assert metrics.failure_rate == 0.5
    # 失败样本按零分计入质量指标分母，汇总不虚高
    assert metrics.hit_at_1 == 0.5
    assert metrics.recall_at_k == 0.5
    # 延迟只统计成功样本
    assert metrics.p50_latency_ms == 10.0


def test_cancel_emits_run_cancelled_event() -> None:
    _, _, case = _single_note_case()
    _write_dataset("cancel-event-v1", [case])

    async def _scenario():
        run = await service.create_rag_run(
            RAGRunRequest(dataset_id="cancel-event-v1", modes=[SearchMode.fts])
        )
        service.cancel_run(run.run_id)
        return await service.wait_for_run(run.run_id)

    run = asyncio.run(_scenario())
    assert run.status.value == "cancelled"
    events = service.get_events(run.run_id)
    assert events[-1].event.value == "RunCancelled"


def test_load_dataset_ignores_corrupted_unrelated_files() -> None:
    # 无关文件损坏（非法 JSON / 顶层非对象）不应阻断目标数据集加载
    directory = get_settings().benchmark_datasets_path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "broken.json").write_text("{ not valid json", encoding="utf-8")
    (directory / "array.json").write_text('["a", "b"]', encoding="utf-8")
    _write_dataset("ok-v1", [{"case_id": "x", "query": "q", "expected_note_ids": ["n"]}])

    dataset = datasets.load_dataset("ok-v1", BenchmarkKind.rag)
    assert dataset.dataset_id == "ok-v1"
    assert len(dataset.cases) == 1


def test_load_dataset_top_level_must_be_object() -> None:
    _write_raw("array-top", ["a", "b"])
    with pytest.raises(ApiError) as exc:
        datasets.load_dataset("array-top", BenchmarkKind.rag)
    assert exc.value.code == "BENCHMARK_DATASET_INVALID"
