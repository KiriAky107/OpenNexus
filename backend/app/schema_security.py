"""共享 JSON Schema 安全约束。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

_SCHEMA_BASE_URI = "https://notesagent.invalid/local-schema"


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
    """只允许可解析的文档内 Fragment，并按 JSON Schema Resource 作用域解析。"""

    root = DRAFT202012.create_resource(schema)
    root_uri = urljoin(_SCHEMA_BASE_URI, root.id() or "")
    registry = Registry().with_resource(_SCHEMA_BASE_URI, root).crawl()
    resolver = registry.resolver(root_uri)
    _validate_resource_references(root, resolver)


def _validate_resource_references(resource, resolver: Any) -> None:
    contents = resource.contents
    if isinstance(contents, dict):
        for keyword in ("$ref", "$dynamicRef"):
            if keyword not in contents:
                continue
            reference = contents[keyword]
            if not isinstance(reference, str) or not reference.startswith("#"):
                raise ExternalSchemaReferenceError(keyword, reference)
            try:
                resolver.lookup(reference)
            except Unresolvable as exc:
                raise UnresolvableLocalSchemaReferenceError(reference) from exc

    for subresource in resource.subresources():
        _validate_resource_references(
            subresource,
            resolver.in_subresource(subresource),
        )
