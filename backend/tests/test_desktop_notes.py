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
        import hashlib
        return document if method == 'read' else {'state': 'committed', 'result': {'file_id': document['file_id'], 'path': params['path'], 'hash': hashlib.sha256(params['content'].encode()).hexdigest()}}
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


def test_note_listing_skips_canvas_images_and_folders_across_host_pages(monkeypatch):
    document = dict(file_id='note', path='lesson/evidence.MD', hash='hash',
                    content='# Evidence\nActual note', created_at=0, updated_at=1)
    pages = [dict(path='map.canvas', file_id='canvas'), dict(path='image.png', file_id='image'),
             dict(path='lesson', file_id='folder', is_folder=True), document]
    reads = []
    def call(method, **params):
        if method == 'list':
            return {'items': pages[params['offset']:params['offset'] + 2], 'total': len(pages)}
        reads.append(params['file_id'])
        assert params['file_id'] == 'note'
        return document
    monkeypatch.setattr(desktop_notes, 'call', call)
    notes, total = desktop_notes.list_notes(limit=100, offset=0, folder=None, tag=None)
    assert total == 1 and notes[0].note_id == 'note'
    assert reads == ['note']
    from app.services import desktop_projection
    assert desktop_projection.entries() == [document]
