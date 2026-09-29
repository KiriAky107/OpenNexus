import asyncio
from types import SimpleNamespace

import pytest

from app.database.db import connect_knowledge
from app import host_bridge
from app.errors import ApiError
from app.services import note_changes


def test_ai_write_receipt_and_restore_are_revision_checked(monkeypatch):
    current = {'content': 'before'}

    async def get_note(note_id):
        return SimpleNamespace(note_id=note_id, file_path='lessons/a.md', markdown=current['content'])

    async def update_note(note_id, *, markdown, tags, expected_content_hash, defer_vectors):
        assert defer_vectors
        assert tags == []
        if note_changes.content_hash(current['content']) != expected_content_hash:
            raise ApiError(409, 'NOTE_CONTENT_CONFLICT', 'changed')
        current['content'] = markdown
        return await get_note(note_id)

    monkeypatch.setattr(note_changes.note_service, 'get_note', get_note)
    monkeypatch.setattr(note_changes.note_service, 'update_note', update_note)

    async def write(expected):
        assert expected == note_changes.content_hash('before')
        current['content'] = 'after'
        return await get_note('stable-id')

    asyncio.run(note_changes.record_ai_write(
        origin='agent:run-1:notes.update', requested_target='stable-id',
        note_id='stable-id', write=write,
    ))
    history = note_changes.list_changes('stable-id')
    assert len(history) == 1
    assert history[0]['before_hash'] == note_changes.content_hash('before')
    assert history[0]['after_hash'] == note_changes.content_hash('after')

    current['content'] = 'human edit'
    with pytest.raises(ApiError) as conflict:
        asyncio.run(note_changes.restore_change(history[0]['change_id']))
    assert conflict.value.code == 'CHANGE_CONTENT_CONFLICT'
    assert current['content'] == 'human edit'

    current['content'] = 'after'
    asyncio.run(note_changes.restore_change(history[0]['change_id']))
    assert current['content'] == 'before'
    assert len(note_changes.list_changes('stable-id')) == 2


def test_failed_write_does_not_appear_as_applied(monkeypatch):
    async def get_note(note_id):
        return SimpleNamespace(note_id=note_id, file_path='a.md', markdown='original')

    async def failed(_):
        raise ApiError(409, 'NOTE_CONTENT_CONFLICT', 'changed')

    monkeypatch.setattr(note_changes.note_service, 'get_note', get_note)
    with pytest.raises(ApiError):
        asyncio.run(note_changes.record_ai_write(
            origin='agent:run-2:notes.update', requested_target='a.md',
            note_id='stable-id', write=failed,
        ))
    assert note_changes.list_changes('stable-id') == []
    conn = connect_knowledge()
    try:
        assert conn.execute("SELECT status FROM note_changes").fetchone()['status'] == 'failed'
    finally:
        conn.close()


def test_duplicate_tool_operation_does_not_replay_write(monkeypatch):
    current = {'content': 'old', 'calls': 0}

    async def get_note(note_id):
        return SimpleNamespace(note_id=note_id, file_path='a.md', markdown=current['content'])

    async def write(expected):
        current['calls'] += 1
        assert expected == note_changes.content_hash('old')
        current['content'] = 'new'
        return await get_note('stable-id')

    monkeypatch.setattr(note_changes.note_service, 'get_note', get_note)
    token = host_bridge.operation_id.set('same-tool-operation')
    try:
        for _ in range(2):
            asyncio.run(note_changes.record_ai_write(
                origin='agent:run:notes.update', requested_target='a.md',
                note_id='stable-id', write=write))
    finally:
        host_bridge.operation_id.reset(token)
    assert current['calls'] == 1
    assert len(note_changes.list_changes('stable-id')) == 1
