"""Vault 相对路径校验；所有文件操作必须先经过本模块。"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings
from app.errors import ApiError

_INVALID_FILE_CHARS = re.compile(r'[\\/:*?"<>|]')


def normalize_folder(folder: str | None) -> str:
    """返回使用 `/` 的安全相对目录；根目录表示为空字符串。"""

    if not folder or folder in {"/", "\\"}:
        return ""
    if "\x00" in folder:
        raise ApiError(400, "INVALID_PATH", "folder must not contain NUL bytes")
    segments: list[str] = []
    for part in re.split(r"[\\/]+", folder):
        if not part:
            continue
        if part in {".", ".."} or ":" in part:
            raise ApiError(
                400,
                "INVALID_PATH",
                "folder must be a relative path without '.' or '..' segments",
                {"folder": folder},
            )
        segments.append(part)
    return "/".join(segments)


def normalize_entry_name(name: str, *, markdown: bool = False) -> str:
    """校验单个目录项名称；不静默接受路径分隔符或保留段。"""

    value = name.strip()
    if not value or value in {".", ".."} or "\x00" in value:
        raise ApiError(400, "INVALID_PATH", "entry name is invalid", {"name": name})
    if _INVALID_FILE_CHARS.search(value):
        raise ApiError(
            400,
            "INVALID_PATH",
            "entry name contains unsupported characters",
            {"name": name},
        )
    if markdown and not value.lower().endswith(".md"):
        value += ".md"
    return value


def safe_note_filename(title: str) -> str:
    """为创建笔记保留原有的宽松清洗行为。"""

    value = _INVALID_FILE_CHARS.sub("_", title).strip() or "untitled"
    return value if value.lower().endswith(".md") else f"{value}.md"


def resolve_in_vault(relative_path: str) -> Path:
    """把相对路径解析到当前 Vault，并拒绝符号链接/`..` 导致的越界。"""

    if not relative_path or "\x00" in relative_path:
        raise ApiError(
            400, "INVALID_PATH", "invalid Vault-relative path", {"path": relative_path}
        )
    root = get_settings().vault_path.resolve()
    candidate = (root / relative_path.replace("\\", "/").lstrip("/")).resolve()
    if not candidate.is_relative_to(root):
        raise ApiError(
            400, "INVALID_PATH", "path escapes Vault", {"path": relative_path}
        )
    return candidate


def relative_to_vault(path: Path) -> str:
    return path.resolve().relative_to(get_settings().vault_path.resolve()).as_posix()
