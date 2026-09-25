"""工作区图片资产：原图归 Vault，SQLite 保存元数据与笔记引用。"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app import host_bridge
from app.config import get_settings
from app.database.db import connect_knowledge, transaction
from app.errors import ApiError
from app.services.vault_paths import resolve_in_vault

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _image_kind(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise ApiError(415, "WORKSPACE_IMAGE_UNSUPPORTED", "仅支持 PNG、JPEG、GIF 和 WebP 图片。")


def _desktop() -> bool:
    return get_settings().environment == "desktop"


def _vault_id() -> str:
    return host_bridge.vault_id.get() or "default"


def _validate_asset_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    parts = normalized.parts
    if normalized.is_absolute() or ".." in parts or len(parts) != 3 or parts[0] != "attachments":
        raise ApiError(400, "INVALID_PATH", "图片路径不属于工作区附件目录。")
    return normalized.as_posix()


def _validate_image_read_path(path: str) -> str:
    value = path.replace('\\', '/')
    parts = value.split('/')
    if (not value or value.startswith('/') or ':' in value or '\x00' in value
            or any(not part or part.startswith('.') or part.lower() == 'opennexus-records' for part in parts)
            or PurePosixPath(value).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}):
        raise ApiError(400, 'INVALID_PATH', '图片必须是知识库内的 PNG、JPEG、GIF 或 WebP 文件。')
    return value


def _write_web(path: str, data: bytes) -> None:
    target = resolve_in_vault(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ApiError(409, "RESOURCE_CONFLICT", "附件路径已有不同内容。")
        return
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_desktop(path: str, data: bytes) -> None:
    if host_bridge.active is None:
        raise ApiError(503, "HOST_UNAVAILABLE", "桌面 Host 不可用。")
    try:
        host_bridge.active.call(
            "workspace.assets.write", vault_id=_vault_id(), path=path,
            content_base64=base64.b64encode(data).decode("ascii"), operation_id=str(uuid4()),
        )
    except RuntimeError as error:
        raise ApiError(409 if str(error) == "REVISION_CONFLICT" else 503,
                       str(error), "写入工作区图片失败。") from None


def _record(*, digest: str, path: str, media_type: str, size: int, original_name: str,
            note_id: str, note_path: str, source: str) -> None:
    asset_id = f"asset_{digest}"
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_knowledge()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO workspace_assets(asset_id,path,content_hash,media_type,size,original_name,created_at) VALUES(?,?,?,?,?,?,?)",
                (asset_id, path, digest, media_type, size, Path(original_name).name[:255], now),
            )
            if note_path:
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_asset_links(asset_id,note_id,note_path,source,created_at) VALUES(?,?,?,?,?)",
                    (asset_id, note_id, note_path.replace("\\", "/").lstrip("/"), source, now),
                )
    finally:
        conn.close()


def store(data: bytes, *, original_name: str, note_id: str, note_path: str, source: str) -> dict:
    if not data:
        raise ApiError(400, "WORKSPACE_IMAGE_EMPTY", "图片内容为空。")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "WORKSPACE_IMAGE_TOO_LARGE", "工作区图片不能超过 5 MiB。")
    if source not in {"paste", "drop", "upload"}:
        raise ApiError(400, "WORKSPACE_IMAGE_SOURCE_INVALID", "图片来源无效。")
    extension, media_type = _image_kind(data)
    digest = hashlib.sha256(data).hexdigest()
    asset_id = f"asset_{digest}"
    path = f"attachments/{digest[:2]}/{digest}.{extension}"
    (_write_desktop if _desktop() else _write_web)(path, data)

    _record(digest=digest, path=path, media_type=media_type, size=len(data),
            original_name=Path(original_name).name or f"image.{extension}", note_id=note_id,
            note_path=note_path, source=source)
    return {"asset_id": asset_id, "path": path, "content_hash": digest,
            "media_type": media_type, "size": len(data), "original_name": Path(original_name).name}


def read(path: str, *, note_id: str = "", note_path: str = "") -> tuple[bytes, str]:
    path = _validate_image_read_path(path)
    if _desktop():
        if host_bridge.active is None:
            raise ApiError(503, "HOST_UNAVAILABLE", "桌面 Host 不可用。")
        try:
            result = host_bridge.active.call("workspace.assets.read", vault_id=_vault_id(), path=path)
            data = base64.b64decode(result["content_base64"], validate=True)
        except (RuntimeError, KeyError, ValueError):
            raise ApiError(404, "RESOURCE_NOT_FOUND", "工作区图片不存在。") from None
    else:
        original = get_settings().vault_path
        for part in path.split('/'):
            original = original / part
            if original.is_symlink() or getattr(original, 'is_junction', lambda: False)():
                raise ApiError(400, 'INVALID_PATH', '不读取链接图片。')
        target = resolve_in_vault(path)
        if not target.is_file() or target.is_symlink():
            raise ApiError(404, "RESOURCE_NOT_FOUND", "工作区图片不存在。")
        if target.stat().st_size > MAX_IMAGE_BYTES:
            raise ApiError(413, 'WORKSPACE_IMAGE_TOO_LARGE', '工作区图片超过读取上限。')
        with target.open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "WORKSPACE_IMAGE_TOO_LARGE", "工作区图片超过读取上限。")
    _, media_type = _image_kind(data)
    digest = hashlib.sha256(data).hexdigest()
    expected = PurePosixPath(path).stem
    managed = re.fullmatch(r'attachments/[0-9a-f]{2}/[0-9a-f]{64}\.(png|jpg|gif|webp)', path)
    if managed and digest != expected:
        raise ApiError(409, "WORKSPACE_IMAGE_HASH_MISMATCH", "工作区图片内容与路径哈希不一致。")
    # Only immutable, content-addressed attachments belong in the managed asset
    # registry. Ordinary files may be renamed or replaced outside the editor.
    if managed:
        _record(digest=digest, path=path, media_type=media_type, size=len(data),
                original_name=PurePosixPath(path).name, note_id=note_id, note_path=note_path,
                source="sync")
    return data, media_type
