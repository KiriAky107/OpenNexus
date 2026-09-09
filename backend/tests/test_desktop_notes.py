"""Host-only适配器约定使用虚假文档；流程覆盖位于 Rust 中。"""
from types import SimpleNamespace
import pytest
import asyncio
from app import host_bridge
from app.errors import ApiError
from app.services import desktop_notes, note_service


def test_desktop_note_read_never_falls_back_without_bound_vault(monkeypatch):
    monkeypatch.setattr('app.config.get_settings', lambda: SimpleNamespace(environment='desktop'))
    token = host_bridge.vault_id.set(None)
    try:
        with pytest.raises(ApiError, match='授权工作区') as error:
            asyncio.run(note_service.get_note('note-from-old-core-index'))
        assert error.value.code == 'WORKSPACE_NOT_OPEN'
    finally:
        host_bridge.vault_id.reset(token)


def test_update_preserves_tags_and_carries_explicit_cas_and_operation(monkeypatch):
    document = dict(file_id='stable-id', path='notes/a.md', hash='observed-hash',
                    content='---\ntags: [original]\n---\nold', created_at=0, updated_at=1)
    calls = []
    def call(method, **params):
        calls.append((method, params))
        return document if method == 'read' else {'state': 'committed'}
    monkeypatch.setattr(desktop_notes, 'call', call)
    token = host_bridge.operation_id.set('b9e1da18-c442-4c1c-a7d7-4ac83b58c849')
    try:
        asyncio.run(desktop_notes.mutate('update_note', 'stable-id', markdown='new body', expected_content_hash='caller-hash'))
    finally:
        host_bridge.operation_id.reset(token)
    write = next(params for method, params in calls if method == 'write')
    assert write['expected'] == 'caller-hash'
    assert write['operation_id'] == 'b9e1da18-c442-4c1c-a7d7-4ac83b58c849'
    assert 'original' in write['content'] and write['content'].endswith('new body')


def test_metadata_keeps_unrelated_frontmatter_and_rejects_non_mapping():
    result = desktop_notes.metadata('---\ncustom: keep\ntags: [old]\n---\nbody', None, [])
    assert 'custom: keep' in result and 'tags: []' in result and result.endswith('body')
    with pytest.raises(ApiError):
        desktop_notes.metadata('---\ntitle: [broken\n---\nbody', 'new', None)
