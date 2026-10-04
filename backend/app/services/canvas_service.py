"""Validated JSON Canvas documents stored beside Markdown in the current Vault."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.config import get_settings
from app.errors import ApiError
from app.services.coordination import serialized_vault_mutation

MAX_CANVAS_BYTES = 4 * 1024 * 1024
MAX_NODES = 2000
MAX_EDGES = 4000


class CanvasDocument(BaseModel):
    path: str
    content: str
    content_hash: str


class CanvasWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    expected_content_hash: str = Field(default="", max_length=64)
    content: str = Field(max_length=MAX_CANVAS_BYTES)


class CanvasMoveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)
    expected_content_hash: str = Field(min_length=64, max_length=64)


class CanvasDeleteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    expected_content_hash: str = Field(min_length=64, max_length=64)


def _path(value: str) -> tuple[str, Path]:
    relative = value.removeprefix("/")
    parts = relative.split("/")
    if (not relative.lower().endswith(".canvas") or len(relative) > 1024
            or "\\" in relative or ":" in relative or "\x00" in relative
            or any(not part or part in {".", ".."} or part.startswith(".") for part in parts)
            or any(part.casefold() in {"opennexus-records", ".ainote", ".git"} for part in parts)):
        raise ApiError(400, "CANVAS_PATH_INVALID", "画布路径必须位于当前知识库内。")
    root = get_settings().vault_path.resolve()
    lexical = root.joinpath(*parts)
    resolved = lexical.resolve()
    if resolved != lexical or not resolved.is_relative_to(root):
        raise ApiError(400, "CANVAS_PATH_INVALID", "画布路径不能通过链接跳转。")
    return relative, resolved


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate(content: bytes) -> None:
    if len(content) > MAX_CANVAS_BYTES:
        raise ApiError(413, "CANVAS_TOO_LARGE", "画布文件超过 4 MiB。")
    try:
        root = json.loads(content.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApiError(422, "CANVAS_INVALID", "画布不是有效的 JSON Canvas。") from exc
    if not isinstance(root, dict):
        raise ApiError(422, "CANVAS_INVALID", "画布根节点必须是对象。")
    nodes, edges = root.get("nodes", []), root.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ApiError(422, "CANVAS_INVALID", "节点和连线必须是数组。")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ApiError(413, "CANVAS_TOO_COMPLEX", "画布节点或连线数量超出上限。")
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ApiError(422, "CANVAS_INVALID", "画布节点无效。")
        node_id = node.get("id")
        if (not isinstance(node_id, str) or not node_id or len(node_id) > 128 or node_id in ids
                or any(not isinstance(node.get(key), (int, float)) or isinstance(node.get(key), bool)
                       or not math.isfinite(node[key]) or not lower <= node[key] <= upper
                       for key, lower, upper in (("x", -1_000_000, 1_000_000), ("y", -1_000_000, 1_000_000),
                                                 ("width", 1, 100_000), ("height", 1, 100_000)))):
            raise ApiError(422, "CANVAS_INVALID", "画布节点标识或尺寸无效。")
        ids.add(node_id)
        if (not _optional(node, "color", _color)
                or not _optional(node, "subpath", lambda value: isinstance(value, str) and value.startswith("#"))
                or not _optional(node, "label", lambda value: isinstance(value, str))
                or not _optional(node, "background", _safe_file)
                or not _optional(node, "backgroundStyle", lambda value: value in ("cover", "ratio", "repeat"))):
            raise ApiError(422, "CANVAS_INVALID", "画布节点的可选字段无效。")
        kind = node.get("type")
        if kind == "text" and isinstance(node.get("text"), str):
            continue
        if kind == "group":
            continue
        if kind == "link" and _safe_url(node.get("url")):
            continue
        if kind == "file" and _safe_file(node.get("file")):
            continue
        raise ApiError(422, "CANVAS_INVALID", "画布节点类型或引用无效。")
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ApiError(422, "CANVAS_INVALID", "画布连线无效。")
        edge_id = edge.get("id")
        from_node, to_node = edge.get("fromNode"), edge.get("toNode")
        if (not isinstance(edge_id, str) or not edge_id or len(edge_id) > 128 or edge_id in edge_ids
                or not isinstance(from_node, str) or not isinstance(to_node, str)
                or from_node not in ids or to_node not in ids):
            raise ApiError(422, "CANVAS_INVALID", "画布连线指向不存在的节点。")
        edge_ids.add(edge_id)
        if (not _optional(edge, "color", _color)
                or not _optional(edge, "label", lambda value: isinstance(value, str))
                or any(not _optional(edge, key, lambda value: value in ("left", "right", "top", "bottom")) for key in ("fromSide", "toSide"))
                or any(not _optional(edge, key, lambda value: value in ("none", "arrow")) for key in ("fromEnd", "toEnd"))):
            raise ApiError(422, "CANVAS_INVALID", "画布连线的可选字段无效。")


def _optional(item: dict, key: str, valid) -> bool:
    return key not in item or valid(item[key])


def _color(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[1-6]|#[\da-fA-F]{6})", value) is not None


def _safe_file(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and not any(char in value for char in ("\\", ":", "\x00", "?", "*", '"', "<", ">", "|"))
            and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            and all(part and part not in {".", ".."} and not part.startswith(".")
                    and not part.endswith((".", " "))
                    and not re.match(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", part, re.I)
                    and part.casefold() != "opennexus-records" for part in value.split("/")))


def _safe_url(value: object) -> bool:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password and parsed.port != 0)
    except ValueError:
        return False


def read(path: str) -> CanvasDocument:
    relative, target = _path(path)
    if not target.is_file():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "画布文件不存在。")
    if target.stat().st_size > MAX_CANVAS_BYTES:
        raise ApiError(413, "CANVAS_TOO_LARGE", "画布文件超过 4 MiB。")
    content = target.read_bytes()
    validate(content)
    return CanvasDocument(path=f"/{relative}", content=content.decode("utf-8"), content_hash=_hash(content))


@serialized_vault_mutation
async def write(request: CanvasWriteRequest) -> CanvasDocument:
    relative, target = _path(request.path)
    content = request.content.encode("utf-8")
    validate(content)
    if not target.parent.is_dir():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "画布的父文件夹不存在。")
    if target.exists() and not request.expected_content_hash:
        raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已存在。")
    old = target.read_bytes() if target.exists() else b""
    if (_hash(old) if old else "") != request.expected_content_hash:
        raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已被修改，请重新加载。")
    # A new file must be created exclusively; an existing target is replaced atomically.
    if not target.exists() and request.expected_content_hash:
        raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已被移动或删除。")
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".canvas-", delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        if target.exists() and _hash(target.read_bytes()) != request.expected_content_hash:
            raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布保存前再次发生变化。")
        if request.expected_content_hash:
            os.replace(temporary_path, target)
        else:
            try:
                os.link(temporary_path, target)
            except FileExistsError as exc:
                raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已被创建。") from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    return CanvasDocument(path=f"/{relative}", content=request.content, content_hash=_hash(content))


@serialized_vault_mutation
async def move(request: CanvasMoveRequest) -> CanvasDocument:
    _, source = _path(request.path)
    relative, target = _path(request.destination)
    if not source.is_file():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "画布文件不存在。")
    content = source.read_bytes()
    validate(content)
    if _hash(content) != request.expected_content_hash:
        raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已被修改。")
    if source == target:
        return CanvasDocument(path=f"/{relative}", content=content.decode("utf-8"), content_hash=_hash(content))
    if target.exists() or not target.parent.is_dir():
        raise ApiError(409, "RESOURCE_CONFLICT", "目标文件夹不存在或已有同名文件。")
    source.rename(target)
    return CanvasDocument(path=f"/{relative}", content=content.decode("utf-8"), content_hash=_hash(content))


@serialized_vault_mutation
async def delete(request: CanvasDeleteRequest) -> None:
    _, source = _path(request.path)
    if not source.is_file():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "画布文件不存在。")
    content = source.read_bytes()
    if _hash(content) != request.expected_content_hash:
        raise ApiError(409, "CANVAS_CONTENT_CONFLICT", "画布已被修改。")
    tombstone = source.with_name(f".{source.name}.{uuid4().hex}.deleting")
    source.rename(tombstone)
    tombstone.unlink()
