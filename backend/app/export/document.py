"""Document AST：导出器的内部中间表示（Internal Protocol，不放入 contracts.py）。

契约 §10.3 规定节点用稳定判别字段 node_id / type / attributes / children / text，
类型专有信息统一放 attributes（如 heading 的 level、link 的 href、image 的 src）。
导出器据此递归渲染，对无法表示的节点记 warning，不静默丢弃。
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import ExportOptions


class DocumentNode(BaseModel):
    """递归文档节点；type 取契约 §10.3 首批 node type 之一。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    node_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    children: list["DocumentNode"] = Field(default_factory=list)
    text: str = ""


class Document(DocumentNode):
    """根节点，type 固定为 document。"""

    type: str = "document"


class DocumentExporter(Protocol):
    """导出器协议（契约 §10.3）：把 Document AST 渲染为指定格式的产物。"""

    async def export(self, document: Document, options: ExportOptions) -> "ExportResult": ...


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: bytes
    mime_type: str
    warnings: list[str] = Field(default_factory=list)
