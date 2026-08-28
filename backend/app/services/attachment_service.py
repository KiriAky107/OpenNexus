from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings
from app.errors import ApiError

_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
MAX_ATTACHMENT_BYTES = 1024 * 1024


def attachment_path(attachment_id: str) -> Path:
    if not _ATTACHMENT_ID.fullmatch(attachment_id):
        raise ApiError(
            400, "INVALID_ATTACHMENT_ID", "attachment_id is invalid",
            {"attachment_id": attachment_id},
        )
    root = get_settings().attachments_path.resolve()
    candidate = (root / attachment_id).resolve()
    if not candidate.is_relative_to(root):
        raise ApiError(400, "INVALID_ATTACHMENT_ID", "attachment path escapes storage")
    return candidate


def read_attachment(attachment_id: str, *, max_chars: int = 100_000) -> dict[str, object]:
    path = attachment_path(attachment_id)
    if not path.is_file():
        raise ApiError(
            404, "ATTACHMENT_NOT_FOUND", "attachment not found",
            {"attachment_id": attachment_id},
        )
    size = path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ApiError(
            413, "ATTACHMENT_TOO_LARGE", "attachment exceeds the Tool read limit",
            {"attachment_id": attachment_id, "size": size},
        )
    text = path.read_text(encoding="utf-8")
    truncated = len(text) > max_chars
    return {
        "attachment_id": attachment_id,
        "content": text[:max_chars],
        "size": size,
        "truncated": truncated,
    }
