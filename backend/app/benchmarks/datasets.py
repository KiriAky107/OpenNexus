"""Benchmark Dataset 注册：从受控目录加载 JSON 数据集并校验。

Dataset 只能来自配置目录（settings.benchmark_datasets_path），API 不接受调用方提交
任意文件路径。目录不存在或为空时按「无数据集」处理，不报错。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from uuid import UUID
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.contracts import (
    BenchmarkDatasetInfo,
    BenchmarkKind,
    RAGDatasetCase, AgentDatasetCase,
)
from app.errors import ApiError


@dataclass
class RAGDataset:
    """内存中的 RAG / Agent 数据集：元信息 + 已校验的 Case 列表 + 内容哈希。"""

    dataset_id: str
    kind: BenchmarkKind
    version: str
    description: str
    cases: list[RAGDatasetCase | AgentDatasetCase] = field(default_factory=list)
    content_hash: str = ""
    scope: str = 'shared'


class _DatasetMeta(BaseModel):
    """Dataset 元数据的最小校验模型。

    list_datasets 用它逐文件校验元信息字段结构，把「合法 JSON 但字段类型错误」
    （如 cases: 42）这类损坏文件隔离掉，而不是让 len() 抛 TypeError 拖垮整个列表。
    """

    dataset_id: str = Field(min_length=1)
    kind: str = ""
    version: str = ""
    description: str = ""
    cases: list = Field(default_factory=list)


def _datasets_dir() -> Path:
    return get_settings().benchmark_datasets_path


def _dataset_files() -> list[Path]:
    directory = _datasets_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def current_scope() -> str | None:
    settings = get_settings()
    if settings.environment == 'desktop':
        from app.host_bridge import vault_id
        try:
            return str(UUID(vault_id.get() or ''))
        except ValueError:
            return None
    # Web mode has exactly one configured workspace. Never expose its absolute path.
    return hashlib.sha256(os.path.normcase(str(settings.vault_path.resolve())).encode()).hexdigest()


def _vault_dir(kind: BenchmarkKind) -> Path:
    scope = current_scope()
    if scope is None:
        raise ApiError(409, 'WORKSPACE_NOT_OPEN', '请先打开知识库，再管理专属评测数据集。')
    return _datasets_dir() / 'vaults' / scope / kind.value


def check_scope(expected: str | None) -> None:
    if expected is not None and expected != current_scope():
        raise ApiError(409, 'BENCHMARK_VAULT_CHANGED', '知识库已切换，请重新选择数据集后操作。')


def _visible_files(kind: BenchmarkKind) -> list[tuple[Path, str]]:
    local = sorted(_vault_dir(kind).glob('*.json')) if current_scope() else []
    # Current-vault definitions take precedence over legacy shared datasets.
    identifiers = {path.stem for path in local}
    return [(path, 'vault') for path in local] + [(path, 'shared') for path in _dataset_files() if path.stem not in identifiers]


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

    cases: list[RAGDatasetCase | AgentDatasetCase] = []
    for index, case in enumerate(raw_cases):
        try:
            parsed = (AgentDatasetCase if kind == BenchmarkKind.agent else RAGDatasetCase).model_validate(case)
        except ValidationError as exc:
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                f"Dataset case #{index} is invalid.",
                {"dataset_id": dataset_id, "case_index": index, "errors": exc.errors()},
            ) from exc
        if kind == BenchmarkKind.agent:
            if not (parsed.members or parsed.expected_tools or parsed.output_contains or parsed.citation_required or parsed.tasks_created is not None):
                raise ApiError(422, 'BENCHMARK_DATASET_INVALID', 'Agent case requires objective expectations.')
            if any(tool.name not in parsed.allowed_tools for tool in parsed.expected_tools):
                raise ApiError(422, 'BENCHMARK_DATASET_INVALID', 'Expected tools must be allowed.')
            cases.append(parsed)
            continue
        # 每个 Case 至少要声明一个期望 id，否则无法计算命中/召回
        if not parsed.expected_note_ids and not parsed.expected_block_ids and not parsed.expected_note_paths:
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                f"Dataset case '{parsed.case_id}' must declare expected_note_paths, expected_note_ids or expected_block_ids.",
                {"dataset_id": dataset_id, "case_id": parsed.case_id},
            )
        # citation_required=true 时必须声明 expected_block_ids，否则无法计算 Citation Hit Rate
        if parsed.citation_required and not parsed.expected_block_ids:
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                f"Dataset case '{parsed.case_id}' requires expected_block_ids when citation_required is true.",
                {"dataset_id": dataset_id, "case_id": parsed.case_id},
            )
        cases.append(parsed)

    if len(cases) > 100 or len({c.case_id for c in cases}) != len(cases):
        raise ApiError(422, 'BENCHMARK_DATASET_INVALID', 'Dataset case IDs must be unique; maximum 100 cases.')
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

    逐文件用 _DatasetMeta 校验元信息字段结构，单个损坏文件隔离跳过而非整体失败，
    保证列表接口健壮；损坏细节由 load_dataset 抛出。
    """
    infos: list[BenchmarkDatasetInfo] = []
    for path, scope in _visible_files(kind):
        try:
            raw, raw_bytes = _read_json(path)
            meta = _DatasetMeta.model_validate(raw)
        except (ApiError, ValidationError):
            continue
        if meta.kind not in ("", kind.value):
            continue
        infos.append(
            BenchmarkDatasetInfo(
                dataset_id=meta.dataset_id,
                kind=kind,
                version=meta.version,
                description=meta.description,
                case_count=len(meta.cases),
                content_hash=_content_hash(raw_bytes),
                scope=scope,
            )
        )
    return infos


def load_dataset(dataset_id: str, kind: BenchmarkKind) -> RAGDataset:
    """按文件名加载并校验数据集；找不到抛 BENCHMARK_DATASET_NOT_FOUND。

    只读取与请求 dataset_id 同名的文件（{dataset_id}.json），无关文件的损坏（JSON 语法
    错误、UTF-8 解码错误、顶层非对象）不会阻断目标数据集加载；只有目标文件本身损坏
    才抛 BENCHMARK_DATASET_INVALID。按现有文件 stem 精确匹配，不拼接调用方传入的路径。
    """
    for path, scope in _visible_files(kind):
        if path.stem != dataset_id:
            continue
        raw, raw_bytes = _read_json(path)
        if not isinstance(raw, dict):
            raise ApiError(
                422,
                "BENCHMARK_DATASET_INVALID",
                "Dataset top-level must be a JSON object.",
                {"dataset_id": dataset_id, "path": path.name},
            )
        dataset = _dataset_from_raw(raw, raw_bytes, kind)
        dataset.scope = scope
        return dataset
    raise ApiError(
        404,
        "BENCHMARK_DATASET_NOT_FOUND",
        f"Benchmark dataset does not exist: {dataset_id}",
        {"dataset_id": dataset_id, "kind": kind.value},
    )


def import_dataset(content: str) -> BenchmarkDatasetInfo:
    """Import validated JSON into the authenticated current vault, without overwriting."""
    if len(content.encode('utf-8')) > 1048576:
        raise ApiError(413, 'BENCHMARK_DATASET_TOO_LARGE', '数据集不能超过 1 MiB。')
    try:
        raw = json.loads(content.lstrip('\ufeff'))
        if not isinstance(raw, dict):
            raise ValueError()
        kind = BenchmarkKind(raw.get('kind'))
        meta = _DatasetMeta.model_validate(raw)
    except (ValueError, TypeError, ValidationError):
        raise ApiError(422, 'BENCHMARK_DATASET_INVALID', '请提供有效的 RAG 或 Agent 数据集 JSON。') from None
    identifier = meta.dataset_id
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', identifier) or identifier.upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        raise ApiError(422, 'BENCHMARK_DATASET_INVALID', '数据集 ID 需为 1–80 位字母、数字、下划线或连字符，不能使用系统保留名称。')
    try:
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeEncodeError):
        raise ApiError(422, 'BENCHMARK_DATASET_INVALID', '数据集必须是有效 UTF-8 JSON，不能包含 NaN 或 Infinity。') from None
    if len(encoded) > 1048576:
        raise ApiError(413, 'BENCHMARK_DATASET_TOO_LARGE', '格式化后的数据集不能超过 1 MiB。')
    parsed = _dataset_from_raw(raw, encoded, kind)
    directory = _vault_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{identifier}.json'
    # Publish only a fully written file; exclusive hard-link creation prevents races.
    fd, temporary = tempfile.mkstemp(prefix='.import-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != encoded:
                raise ApiError(409, 'BENCHMARK_DATASET_EXISTS', '当前知识库已有同名数据集，请更换 dataset_id；不会覆盖原数据。') from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return BenchmarkDatasetInfo(dataset_id=identifier, kind=kind, version=parsed.version,
        description=parsed.description, case_count=len(parsed.cases), content_hash=parsed.content_hash, scope='vault')


def export_dataset(identifier: str, kind: BenchmarkKind) -> dict:
    load_dataset(identifier, kind)
    for path, _ in _visible_files(kind):
        if path.stem == identifier:
            return _read_json(path)[0]
    raise ApiError(404, 'BENCHMARK_DATASET_NOT_FOUND', '数据集不存在。')


def resolve_note_paths(dataset: RAGDataset) -> None:
    """Resolve user-authored relative note paths using only the current vault index."""
    from app.database.db import connect_knowledge
    with closing(connect_knowledge()) as conn:
        paths = {}
        for row in conn.execute('SELECT note_id, file_path FROM notes'):
            paths.setdefault(row['file_path'].replace('\\', '/').lstrip('/'), []).append(row['note_id'])
    for case in dataset.cases:
        if not isinstance(case, RAGDatasetCase):
            continue
        resolved = []
        for path in case.expected_note_paths:
            ids = paths.get(path.replace('\\', '/').lstrip('/'), [])
            if len(ids) != 1:
                raise ApiError(409, 'BENCHMARK_NOTE_NOT_INDEXED', '预期笔记未在当前知识库唯一匹配，请检查相对路径并重建索引。', {'case_id': case.case_id, 'note_path': path})
            resolved.extend(ids)
        case.expected_note_ids = list(dict.fromkeys([*case.expected_note_ids, *resolved]))
