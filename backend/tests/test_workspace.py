import asyncio
import hashlib

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


@pytest.mark.parametrize('operation', ['rename','move','delete'])
def test_reviewed_note_operations_reject_external_edits(operation):
    from app.services import note_service
    note = asyncio.run(note_service.create_note(title='Before', markdown='original', folder='course', tags=[]))
    expected = hashlib.sha256(note.markdown.encode()).hexdigest()
    path = get_settings().vault_path / note.file_path
    path.write_text('human edit',encoding='utf-8')
    async def run():
        if operation == 'rename': return await note_service.rename_note(note.note_id,file_name='After.md',expected_content_hash=expected)
        if operation == 'move': return await note_service.move_note(note.note_id,folder='elsewhere',expected_content_hash=expected)
        return await note_service.delete_note(note.note_id,expected_content_hash=expected)
    with pytest.raises(ApiError) as error: asyncio.run(run())
    assert error.value.code == 'NOTE_CONTENT_CONFLICT'
    assert path.read_text(encoding='utf-8') == 'human edit'


def test_folder_review_checks_all_entries_and_tree_exposes_real_revisions():
    from app.services import workspace_service
    note = asyncio.run(create_note(NoteCreateRequest(title='Before',markdown='original',folder='course')))
    tree = workspace_service.get_workspace_tree()
    child = tree[0].children[0]
    assert child.content_hash == hashlib.sha256(b'original').hexdigest()
    assert child.updated_at is not None
    expected = {'Before.md':child.content_hash}
    source = get_settings().vault_path / 'course'
    (source/'unexpected.txt').write_text('new',encoding='utf-8')
    with pytest.raises(ApiError) as error: asyncio.run(workspace_service.rename_folder('/course','renamed',expected))
    assert error.value.code == 'FOLDER_CONTENT_CONFLICT'
    with pytest.raises(ApiError): asyncio.run(workspace_service.delete_folder('/course',expected))
    assert (source/'unexpected.txt').exists()


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
        "/api/workspace/assets",
        "/api/workspace/assets/content",
        "/api/notes/{note_id}/rename",
    } <= paths.keys()

def test_external_files_are_registered_and_removed_without_vector_wait(monkeypatch) -> None:
    from app.services import index_service
    scheduled = []
    monkeypatch.setattr(index_service, 'schedule_workspace_rebuild', lambda: scheduled.append(True))
    vault = get_settings().vault_path
    vault.mkdir(parents=True, exist_ok=True)
    external = vault / 'external.md'
    external.write_text('# External\n', encoding='utf-8')
    tree = asyncio.run(get_workspace_tree())
    assert tree[0].note_id is not None
    external.rename(vault / 'renamed.md')
    tree = asyncio.run(get_workspace_tree())
    assert [item.name for item in tree] == ['renamed.md']
    (vault / 'renamed.md').unlink()
    assert asyncio.run(get_workspace_tree()) == []
    assert len(scheduled) == 3


def test_save_rejects_external_content_change() -> None:
    import hashlib
    from app.contracts import NoteUpdateRequest
    from app.routes import update_note
    original = '# Original\n'
    note = asyncio.run(create_note(NoteCreateRequest(title='Conflict', markdown=original)))
    disk = get_settings().vault_path / note.file_path
    disk.write_text('# External\n', encoding='utf-8')
    with pytest.raises(ApiError) as error:
        asyncio.run(update_note(note.note_id, NoteUpdateRequest(markdown='# Editor\n', expected_content_hash=hashlib.sha256(original.encode()).hexdigest())))
    assert error.value.code == 'NOTE_CONTENT_CONFLICT'
    assert disk.read_text(encoding='utf-8') == '# External\n'
