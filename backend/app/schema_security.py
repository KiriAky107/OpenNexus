"""共享 JSON Schema 安全约束。"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote


class SchemaReferenceError(ValueError):
    """Schema 引用不符合宿主的离线、文档内解析约束。"""


class ExternalSchemaReferenceError(SchemaReferenceError):
    def __init__(self, keyword: str, reference: Any) -> None:
        super().__init__(f"External JSON Schema reference is not allowed: {reference!r}")
        self.keyword = keyword
        self.reference = reference


class UnresolvableLocalSchemaReferenceError(SchemaReferenceError):
    def __init__(self, reference: str) -> None:
        super().__init__(f"Local JSON Schema reference cannot be resolved: {reference!r}")
        self.reference = reference


def reject_external_schema_references(schema: Any) -> None:
    """只允许可解析的文档内 Fragment，禁止文件和网络检索。"""

    pending = [schema]
    local_references: list[str] = []
    anchors: set[str] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"}:
                    if not isinstance(child, str) or not child.startswith("#"):
                        raise ExternalSchemaReferenceError(key, child)
                    local_references.append(child)
                elif key in {"$anchor", "$dynamicAnchor"} and isinstance(child, str):
                    anchors.add(child)
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)

    for reference in local_references:
        if not _local_reference_exists(schema, reference, anchors):
            raise UnresolvableLocalSchemaReferenceError(reference)


def _local_reference_exists(schema: Any, reference: str, anchors: set[str]) -> bool:
    fragment = unquote(reference[1:])
    if not fragment:
        return True
    if not fragment.startswith("/"):
        return fragment in anchors

    current = schema
    for encoded_segment in fragment[1:].split("/"):
        segment = encoded_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdecimal():
            index = int(segment)
            if index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True
