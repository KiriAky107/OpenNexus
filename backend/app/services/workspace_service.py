"""Web 联调 Workspace：把单一配置 Vault 映射为前端可用的真实文件树。"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import repository
from app.config import get_settings
from app.contracts import (
    OperationResponse,
    WorkspaceEntry,
    WorkspaceInfo,
    WorkspaceSnapshot,
)
from app.database.db import connect, transaction
from app.errors import ApiError
from app.retrieval.vectorstore import SqliteVecStore
from app.knowledge.parser import parse_note
from app.services import index_service
from app.services.coordination import serialized_vault_mutation
from app.services.vault_paths import normalize_entry_name, normalize_folder, resolve_in_vault

vector_store = SqliteVecStore()


def _entry_id(kind: str, path: str) -> str:
    digest = hashlib.sha256(f"{kind}:{path}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}_{digest}"


def _disk_markdown_paths() -> set[str]:
    root = get_settings().vault_path
    if not root.exists():
        return set()
    resolved_root = root.resolve()
    paths: set[str] = set()
    for path in root.rglob("*.md"):
        if path.is_symlink():
            continue
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(resolved_root):
            paths.add(resolved.relative_to(resolved_root).as_posix())
    return paths


def get_workspace_info() -> WorkspaceInfo:
    root = get_settings().vault_path.resolve()
    disk_paths = _disk_markdown_paths()
    indexed_paths = {item.file_path for item in repository.list_note_locations()}
    return WorkspaceInfo(
        name=root.name or "Vault",
        path=str(root),
        file_count=len(disk_paths),
        indexed_note_count=len(indexed_paths),
        requires_refresh=disk_paths != indexed_paths,
    )


def _tree(directory: Path, locations: dict[str, repository.NoteLocation]) -> list[WorkspaceEntry]:
    if not directory.exists():
        return []
    root = get_settings().vault_path.resolve()
    entries: list[WorkspaceEntry] = []
    children = sorted(
        directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
    )
    for child in children:
        if child.name.startswith(".") or child.is_symlink():
            continue
        resolved = child.resolve()
        if not resolved.is_relative_to(root):
            continue
        relative = resolved.relative_to(root).as_posix()
        public_path = f"/{relative}"
        if resolved.is_dir():
            entries.append(
                WorkspaceEntry(
                    entry_id=_entry_id("folder", relative),
                    name=child.name,
                    path=public_path,
                    type="folder",
                    children=_tree(resolved, locations),
                )
            )
        elif resolved.is_file() and child.suffix.lower() == ".md":
            location = locations.get(relative)
            entries.append(
                WorkspaceEntry(
                    entry_id=location.note_id if location else _entry_id("file", relative),
                    note_id=location.note_id if location else None,
                    name=child.name,
                    path=public_path,
                    type="file",
                )
            )
    return entries


def get_workspace_tree() -> list[WorkspaceEntry]:
    locations = {item.file_path: item for item in repository.list_note_locations()}
    return _tree(get_settings().vault_path.resolve(), locations)


async def refresh_workspace_tree() -> list[WorkspaceEntry]:
    """Observe external creates/deletes without waiting for vector inference."""
    if get_workspace_info().requires_refresh:
        await _register_workspace_files()
        index_service.schedule_workspace_rebuild()
    return get_workspace_tree()


async def open_workspace(requested_path: str | None) -> WorkspaceSnapshot:
    """打开只登记文件与全文索引，不让 Embedding 或厂商网络阻塞工作区。"""

    root = get_settings().vault_path.resolve()
    if requested_path and Path(requested_path).resolve() != root:
        raise ApiError(
            409,
            "WORKSPACE_PATH_MISMATCH",
            "Web development mode can only open the backend configured Vault.",
            {"configured_path": str(root)},
        )
    root.mkdir(parents=True, exist_ok=True)
    info = get_workspace_info()
    if info.requires_refresh:
        await _register_workspace_files()
        info = get_workspace_info()
    if index_service.get_status().vector_refresh_required:
        index_service.schedule_workspace_rebuild()
    return WorkspaceSnapshot(workspace=info, items=get_workspace_tree())


@serialized_vault_mutation
async def _register_workspace_files() -> None:
    root = get_settings().vault_path.resolve()
    paths = _disk_markdown_paths()
    existing = {item.file_path: item for item in repository.list_note_locations()}
    prepared = []
    for relative in sorted(paths - existing.keys()):
        path = resolve_in_vault(relative)
        stat = path.stat()
        prepared.append(parse_note(
            markdown=path.read_text(encoding='utf-8'), file_path=relative,
            folder='' if path.parent == root else path.parent.relative_to(root).as_posix(),
            tags=None, created_at=datetime.fromtimestamp(stat.st_ctime, timezone.utc),
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        ))
    conn = connect()
    try:
        with transaction(conn):
            for relative in existing.keys() - paths:
                block_ids = repository.delete_note(existing[relative].note_id, conn=conn)
                await vector_store.delete(block_ids, conn=conn)
            for parsed in prepared:
                repository.replace_note_metadata(conn=conn, note_id=parsed.note_id, title=parsed.title,
                    file_path=parsed.file_path, folder=parsed.folder, tags=parsed.tags,
                    created_at=parsed.created_at, updated_at=parsed.updated_at, blocks=parsed.blocks)
                conn.execute('UPDATE blocks SET embedding_local_only=? WHERE note_id=?', (int(parsed.embedding_local_only), parsed.note_id))
            if prepared:
                repository.set_index_meta({'workspace_vectors_pending': '1'}, conn=conn)
    finally:
        conn.close()


@serialized_vault_mutation
async def create_folder(parent: str, name: str) -> WorkspaceEntry:
    clean_parent = normalize_folder(parent)
    clean_name = normalize_entry_name(name)
    relative = f"{clean_parent}/{clean_name}" if clean_parent else clean_name
    target = resolve_in_vault(relative)
    if not clean_parent:
        get_settings().vault_path.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ApiError(
            409, "RESOURCE_CONFLICT", "folder already exists", {"path": relative}
        )
    if not target.parent.is_dir():
        raise ApiError(
            404,
            "RESOURCE_NOT_FOUND",
            "parent folder not found",
            {"parent": clean_parent},
        )
    target.mkdir(parents=False)
    return WorkspaceEntry(
        entry_id=_entry_id("folder", relative),
        name=clean_name,
        path=f"/{relative}",
        type="folder",
    )


@serialized_vault_mutation
async def rename_folder(path: str, new_name: str) -> WorkspaceEntry:
    old_folder = normalize_folder(path)
    if not old_folder:
        raise ApiError(400, "INVALID_PATH", "the Vault root cannot be renamed")
    clean_name = normalize_entry_name(new_name)
    parent = Path(old_folder).parent.as_posix()
    parent = "" if parent == "." else parent
    new_folder = f"{parent}/{clean_name}" if parent else clean_name
    source = resolve_in_vault(old_folder)
    target = resolve_in_vault(new_folder)
    if not source.is_dir() or source.is_symlink():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "folder not found", {"path": path})
    if target.exists():
        raise ApiError(
            409, "RESOURCE_CONFLICT", "target folder already exists", {"path": new_folder}
        )

    affected = [
        item
        for item in repository.list_note_locations()
        if item.folder == old_folder or item.folder.startswith(f"{old_folder}/")
    ]
    source.replace(target)
    conn = connect()
    now = datetime.now(timezone.utc)
    try:
        with transaction(conn):
            for item in affected:
                file_suffix = item.file_path[len(old_folder) :].lstrip("/")
                folder_suffix = item.folder[len(old_folder) :].lstrip("/")
                repository.update_note_location(
                    conn=conn,
                    note_id=item.note_id,
                    title=item.title,
                    file_path=f"{new_folder}/{file_suffix}",
                    folder=(
                        f"{new_folder}/{folder_suffix}" if folder_suffix else new_folder
                    ),
                    updated_at=now,
                )
    except BaseException:
        target.replace(source)
        raise
    finally:
        conn.close()

    return WorkspaceEntry(
        entry_id=_entry_id("folder", new_folder),
        name=clean_name,
        path=f"/{new_folder}",
        type="folder",
        children=_tree(target, {item.file_path: item for item in repository.list_note_locations()}),
    )


@serialized_vault_mutation
async def delete_folder(path: str) -> OperationResponse:
    folder = normalize_folder(path)
    if not folder:
        raise ApiError(400, "INVALID_PATH", "the Vault root cannot be deleted")
    source = resolve_in_vault(folder)
    if not source.is_dir() or source.is_symlink():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "folder not found", {"path": path})

    affected = [
        item
        for item in repository.list_note_locations()
        if item.folder == folder or item.folder.startswith(f"{folder}/")
    ]
    tombstone = source.with_name(f".{source.name}.{uuid4().hex}.deleting")
    source.replace(tombstone)
    conn = connect()
    try:
        with transaction(conn):
            block_ids: list[str] = []
            for item in affected:
                block_ids.extend(repository.delete_note(item.note_id, conn=conn))
            await vector_store.delete(block_ids, conn=conn)
    except BaseException:
        tombstone.replace(source)
        raise
    finally:
        conn.close()

    try:
        shutil.rmtree(tombstone)
    except OSError:
        # 已提交的删除不回滚；隐藏 tombstone 可由后续维护任务清理。
        pass
    return OperationResponse(
        status="completed",
        resource_id=_entry_id("folder", folder),
        message=f"deleted folder and {len(affected)} indexed notes",
    )
