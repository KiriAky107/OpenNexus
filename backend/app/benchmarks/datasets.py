"""Benchmark Dataset 注册：从受控目录加载 JSON 数据集并校验。

Dataset 只能来自配置目录（settings.benchmark_datasets_path），API 不接受调用方提交
任意文件路径。目录不存在或为空时按「无数据集」处理，不报错。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings
from app.contracts import (
    BenchmarkDatasetInfo,
    BenchmarkKind,
    RAGDatasetCase,
)
from app.errors import ApiError


@dataclass
class RAGDataset:
    """内存中的 RAG 数据集：元信息 + 已校验的 Case 列表 + 内容哈希。"""

    dataset_id: str
    kind: BenchmarkKind
    version: str
    description: str
    cases: list[RAGDatasetCase] = field(default_factory=list)
    content_hash: str = ""


def _datasets_dir() -> Path:
    return get_settings().benchmark_datasets_path


def _dataset_files() -> list[Path]:
    directory = _datasets_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def _content_hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> tuple[dict, bytes]:
    """读取并解析 JSON 文件，返回 (dict, 原始字节)；非法 JSON 抛 BENCHMARK_DATASET_INVALID。"""
    try:
        raw_bytes = path.read_bytes()
        return json.loads(raw_bytes.decode("utf-8")), raw_bytes
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ApiError(
            422,
            "BENCHMARK_DATASET_INVALID",
            f"Dataset file is not valid JSON: {path.name}",
            {"path": str(path)},
        ) from exc


def _dataset_from_raw(raw: dict, raw_bytes: bytes, kind: BenchmarkKind) -> RAGDataset:
    """把单个数据集 JSON 解析为 RAGDataset，非法结构抛 BENCHMARK_DATASET_INVALID。"""
    dataset_id = raw.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ApiError(
            422,
            "BENCHMARK_DATASET_INVALID",
            "Dataset must declare a non-empty string 'dataset_id'.",
            {},
        )
    file_kind = raw.get("kind", kind.value)
    if file_kind != kind.value:
        raise ApiError(
            422,
            "BENCHMARK_DATASET_INVALID",
            f"Dataset kind mismatch: expected '{kind.value}', got '{file_kind}'.",
            {"dataset_id": dataset_id},
        )
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ApiError(
            422,
            "BENCHMARK_DATASET_INVALID",
            "Dataset 'cases' must be a non-empty list.",
            {"dataset_id": dataset_id},
        )

    cases: list[RAGDatasetCase] = []
    for index, case in enumerate(raw_cases):
        try:
            parsed = RAGDatasetCase.model_validate(case)
        except ValidationError as exc:
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                f"Dataset case #{index} is invalid.",
                {"dataset_id": dataset_id, "case_index": index, "errors": exc.errors()},
            ) from exc
        # 每个 Case 至少要声明一个期望 id，否则无法计算命中/召回
        if not parsed.expected_note_ids and not parsed.expected_block_ids:
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                f"Dataset case '{parsed.case_id}' must declare expected_note_ids or expected_block_ids.",
                {"dataset_id": dataset_id, "case_id": parsed.case_id},
            )
        cases.append(parsed)

    return RAGDataset(
        dataset_id=dataset_id,
        kind=kind,
        version=str(raw.get("version", "")),
        description=str(raw.get("description", "")),
        cases=cases,
        content_hash=_content_hash(raw_bytes),
    )


def list_datasets(kind: BenchmarkKind) -> list[BenchmarkDatasetInfo]:
    """枚举受控目录下指定 kind 的数据集元信息（不含 Case 内容）。

    个别文件损坏时跳过而非整体失败，保证列表接口健壮；损坏细节由 load_dataset 抛出。
    """
    infos: list[BenchmarkDatasetInfo] = []
    for path in _dataset_files():
        try:
            raw, raw_bytes = _read_json(path)
        except ApiError:
            continue
        if raw.get("kind", kind.value) != kind.value:
            continue
        infos.append(
            BenchmarkDatasetInfo(
                dataset_id=raw.get("dataset_id", path.stem),
                kind=kind,
                version=str(raw.get("version", "")),
                description=str(raw.get("description", "")),
                case_count=len(raw.get("cases", [])),
                content_hash=_content_hash(raw_bytes),
            )
        )
    return infos


def load_dataset(dataset_id: str, kind: BenchmarkKind) -> RAGDataset:
    """按 id 加载并校验数据集；找不到抛 BENCHMARK_DATASET_NOT_FOUND。"""
    for path in _dataset_files():
        raw, raw_bytes = _read_json(path)
        if raw.get("dataset_id") != dataset_id:
            continue
        return _dataset_from_raw(raw, raw_bytes, kind)
    raise ApiError(
        404,
        "BENCHMARK_DATASET_NOT_FOUND",
        f"Benchmark dataset does not exist: {dataset_id}",
        {"dataset_id": dataset_id, "kind": kind.value},
    )
