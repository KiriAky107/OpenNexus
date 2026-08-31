import asyncio

import pytest

from app.config import get_settings
from app.contracts import (
    FolderCreateRequest,
    FolderDeleteRequest,
    FolderRenameRequest,
    NoteCreateRequest,
    NoteRenameRequest,
    WorkspaceOpenRequest,
)
from app.errors import ApiError
from app.routes import (
    create_note,
    create_workspace_folder,
    delete_workspace_folder,
    get_note,
    get_workspace_tree,
    open_workspace,
    rename_note,
    rename_workspace_folder,
)


def test_open_workspace_indexes_real_markdown_and_returns_tree() -> None:
    vault = get_settings().vault_path
    note_path = vault / "课程" / "操作系统.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("# 操作系统\n\n进程调度。\n", encoding="utf-8")

    snapshot = asyncio.run(open_workspace(WorkspaceOpenRequest()))

    assert snapshot.workspace.path == str(vault.resolve())
    assert snapshot.workspace.requires_refresh is False
    assert snapshot.workspace.file_count == snapshot.workspace.indexed_note_count == 1
    folder = snapshot.items[0]
    assert folder.path == "/课程"
    assert folder.children[0].path == "/课程/操作系统.md"
    assert folder.children[0].note_id is not None


def test_open_workspace_rejects_unconfigured_path() -> None:
    with pytest.raises(ApiError) as error:
        asyncio.run(open_workspace(WorkspaceOpenRequest(path="C:/another-vault")))

    assert error.value.code == "WORKSPACE_PATH_MISMATCH"


def test_note_rename_preserves_identity_and_content() -> None:
    created = asyncio.run(
        create_note(
            NoteCreateRequest(
                title="旧名称", markdown="# 标题不变\n\n真实正文。\n", folder="课程"
            )
        )
    )

    renamed = asyncio.run(
        rename_note(created.note_id, NoteRenameRequest(file_name="新名称.md"))
    )

    assert renamed.note_id == created.note_id
    assert renamed.file_path == "课程/新名称.md"
    assert renamed.title == "新名称"
    assert renamed.markdown == "# 标题不变\n\n真实正文。\n"
    assert not (get_settings().vault_path / "课程" / "旧名称.md").exists()


def test_folder_lifecycle_updates_database_and_vectors() -> None:
    folder = asyncio.run(
        create_workspace_folder(FolderCreateRequest(parent="/", name="课程"))
    )
    created = asyncio.run(
        create_note(
            NoteCreateRequest(title="网络", markdown="# 网络\n\nTCP。\n", folder="课程")
        )
    )

    renamed_folder = asyncio.run(
        rename_workspace_folder(
            FolderRenameRequest(path=folder.path, new_name="计算机课程")
        )
    )
    moved_note = asyncio.run(get_note(created.note_id))

    assert renamed_folder.path == "/计算机课程"
    assert moved_note.note_id == created.note_id
    assert moved_note.file_path == "计算机课程/网络.md"
    assert asyncio.run(get_workspace_tree())[0].children[0].note_id == created.note_id

    response = asyncio.run(
        delete_workspace_folder(FolderDeleteRequest(path=renamed_folder.path))
    )

    assert response.status == "completed"
    with pytest.raises(ApiError) as error:
        asyncio.run(get_note(created.note_id))
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert asyncio.run(get_workspace_tree()) == []


def test_workspace_openapi_paths_are_published() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    assert {
        "/api/workspace",
        "/api/workspace/open",
        "/api/workspace/tree",
        "/api/workspace/folders",
        "/api/workspace/folders/rename",
        "/api/workspace/folders/delete",
        "/api/notes/{note_id}/rename",
    } <= paths.keys()
