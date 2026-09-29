import asyncio
import json
from uuid import uuid4

import pytest

from app import host_bridge
from app.agent.builtin_tools import NoteUpdateArguments, update_note
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import AgentEventType, AgentRunCreateRequest, ToolCall
from app.errors import ApiError
from app.services import note_changes, note_preview, note_service


def call(name, **arguments):
    return ToolCall(tool_call_id='tool_' + uuid4().hex, name=name, arguments=arguments)


def test_preview_matches_writes_and_history_restores_are_reversible():
    async def scenario():
        container = build_container()
        create = call('notes.create', title='Lesson', markdown='# Lesson\nBefore\n', tags=['original'])
        preview = await note_preview.preview_write(create)
        assert preview['operation'] == 'create'
        result = await container.tools.execute(create, ToolExecutionContext('run', reviewed_write=preview))
        assert result.success
        note_id = result.output['note_id']
        for tool in [call('notes.update', note_id=note_id, markdown='# Lesson\nBefore\nAfter\n'),
                     call('notes.patch_markdown', note_id=note_id, expected_content_hash=note_changes.content_hash('# Lesson\nBefore\nAfter\n'), old_text='After', new_text='Revised')]:
            preview = await note_preview.preview_write(tool)
            before = await note_service.get_note(note_id)
            result = await container.tools.execute(tool, ToolExecutionContext('run', reviewed_write=preview))
            assert result.success, result
            after = await note_service.get_note(note_id)
            assert preview['diff'] == note_preview.content_diff(before.markdown, after.markdown)
        history = note_changes.list_changes(note_id)
        assert len(history) == 3
        detail = await note_changes.change_detail(note_id, history[0]['change_id'])
        assert detail['can_restore']
        await note_changes.restore_change(history[0]['change_id'], note_id=note_id, expected_hash=history[0]['after_hash'])
        assert (await note_service.get_note(note_id)).markdown.endswith('After\n')
        undo = note_changes.list_changes(note_id)[0]
        assert undo['origin'].startswith('restore:')
        await note_changes.restore_change(undo['change_id'])
        assert (await note_service.get_note(note_id)).markdown.endswith('Revised\n')
        assert len(note_changes.list_changes(note_id)) == 5
        with pytest.raises(ApiError, match='不存在'):
            await note_changes.change_detail('other-note', history[0]['change_id'])
        creation = await note_changes.change_detail(note_id, history[-1]['change_id'])
        assert creation['restore_reason'] == 'created'
    asyncio.run(scenario())


def test_permission_rejects_missing_or_stale_preview_and_accepts_fresh_review():
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        container = build_container()
        created = await container.agent.create_run(AgentRunCreateRequest(
            input='/tool notes.update ' + json.dumps({'note_id': note.note_id, 'markdown': 'after'}),
            provider_id='mock', model='mock-1', allowed_tools=['notes.update']))
        async with asyncio.timeout(3):
            async for event in container.agent.events(created.run_id):
                if event.event == AgentEventType.permission_required:
                    request_id = event.data['request_id']
                    break
        with pytest.raises(ApiError) as missing:
            await container.agent.resolve_permission(created.run_id, request_id, 'allow_once')
        assert missing.value.code == 'NOTE_PREVIEW_REQUIRED'
        preview = await container.agent.permission_preview(created.run_id, request_id)
        await note_service.update_note(note.note_id, markdown='human edit')
        with pytest.raises(ApiError) as stale:
            await container.agent.resolve_permission(created.run_id, request_id, 'allow_once', preview['token'])
        assert stale.value.code == 'NOTE_PREVIEW_STALE'
        assert not note_changes.list_changes(note.note_id)
        fresh = await container.agent.permission_preview(created.run_id, request_id)
        assert fresh['token'] != preview['token']
        assert await container.agent.resolve_permission(created.run_id, request_id, 'allow_once', fresh['token'])
        completed = await container.agent.wait(created.run_id)
        assert completed.tool_results[0].success
        assert (await note_service.get_note(note.note_id)).markdown == 'after'
    asyncio.run(scenario())


def test_two_approved_writers_cannot_share_a_stale_revision():
    async def scenario():
        container = build_container()
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        calls = [call('notes.update', note_id=note.note_id, markdown=content) for content in ['first', 'second']]
        previews = [await note_preview.preview_write(tool) for tool in calls]
        results = await asyncio.gather(*(container.tools.execute(tool, ToolExecutionContext(f'run-{i}', reviewed_write=previews[i])) for i, tool in enumerate(calls)))
        assert sum(result.success for result in results) == 1
        assert len(note_changes.list_changes(note.note_id)) == 1
        assert (await note_service.get_note(note.note_id)).markdown in {'first', 'second'}
    asyncio.run(scenario())


def test_restore_checks_again_after_receipt_read(monkeypatch):
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        await update_note(NoteUpdateArguments(note_id=note.note_id, markdown='after'), ToolExecutionContext('run'))
        change = note_changes.list_changes(note.note_id)[0]
        original = note_changes.record_ai_write
        async def racing(**kwargs):
            await note_service.update_note(note.note_id, markdown='human')
            return await original(**kwargs)
        monkeypatch.setattr(note_changes, 'record_ai_write', racing)
        with pytest.raises(ApiError):
            await note_changes.restore_change(change['change_id'])
        assert (await note_service.get_note(note.note_id)).markdown == 'human'
        assert len(note_changes.list_changes(note.note_id)) == 1
    asyncio.run(scenario())


def test_metadata_changes_and_moves_invalidate_review_and_restore():
    async def scenario():
        note = await note_service.create_note(title='A', markdown='same', folder=None, tags=['old'])
        tool = call('notes.update', note_id=note.note_id, tags=['new'])
        preview = await note_preview.preview_write(tool)
        assert preview['diff']['lines'] == []
        await update_note(NoteUpdateArguments(**tool.arguments), ToolExecutionContext('run', reviewed_write=preview))
        change = note_changes.list_changes(note.note_id)[0]
        await note_changes.restore_change(change['change_id'])
        assert (await note_service.get_note(note.note_id)).tags == ['old']
        await note_service.rename_note(note.note_id, file_name='Moved.md')
        with pytest.raises(ApiError):
            await note_preview.validate_preview(tool, preview)
        await note_service.update_note(note.note_id, markdown='new body')
        assert (await note_service.get_note(note.note_id)).note_id == note.note_id
        assert not (await note_changes.change_detail(note.note_id, change['change_id']))['can_restore']
    asyncio.run(scenario())


def test_desktop_preview_includes_actual_frontmatter_and_history_is_vault_scoped(monkeypatch, tmp_path):
    from app.services import desktop_notes
    from app.config import get_settings
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    assert get_settings().environment == 'desktop'
    document = dict(file_id='stable', path='A.md', content='---\ntags: [keep]\n---\nold', created_at=0, updated_at=1)
    writes = []
    def host_call(method, **params):
        if method == 'write':
            assert params['expected'] == note_changes.content_hash(document['content'])
            writes.append(params['content'])
            document['content'] = params['content']
            return {'result': {'file_id': 'stable', 'path': params['path'], 'hash': note_changes.content_hash(params['content'])}}
        return {**document, 'hash': note_changes.content_hash(document['content'])}
    monkeypatch.setattr(desktop_notes, 'call', host_call)
    async def scenario():
        token = host_bridge.vault_id.set(str(uuid4()))
        try:
            container = build_container()
            tool = call('notes.update', note_id='stable', title='Title', markdown='new')
            preview = await note_preview.preview_write(tool)
            before = document['content']
            result = await container.tools.execute(tool, ToolExecutionContext('run', reviewed_write=preview))
            assert result.success, result
            assert preview['diff'] == note_preview.content_diff(before, writes[0])
            change = note_changes.list_changes('stable')[0]
            await note_changes.restore_change(change['change_id'])
            assert document['content'] == before
            other = host_bridge.vault_id.set(str(uuid4()))
            try:
                assert note_changes.list_changes('stable') == []
                with pytest.raises(ApiError):
                    await note_changes.restore_change(change['change_id'])
            finally:
                host_bridge.vault_id.reset(other)
        finally:
            host_bridge.vault_id.reset(token)
    asyncio.run(scenario())


def test_large_diff_is_bounded_and_unchanged_content_is_not_reported_as_replaced():
    diff = note_preview.content_diff('old\n' * 3000, 'new\n' * 3000)
    assert len(diff['lines']) == 400 and diff['truncated']
    assert diff['added_chars'] == diff['removed_chars'] == 12000
    assert note_preview.content_diff('same\n' * 3000, 'same\n' * 3000)['lines'] == []


def test_history_http_requires_confirmation_and_obeys_write_policy():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.container import container
    from app.agent.permissions import PermissionMode
    with TestClient(app) as client:
        note = client.post('/api/notes', json={'title': 'A', 'markdown': 'before'}).json()
        asyncio.run(update_note(NoteUpdateArguments(note_id=note['note_id'], markdown='after'), ToolExecutionContext('run')))
        base = '/api/notes/' + note['note_id'] + '/changes'
        listing = client.get(base).json()['items']
        assert len(listing) == 1
        change = listing[0]
        assert client.get(base + '/' + change['change_id']).json()['can_restore']
        endpoint = base + '/' + change['change_id'] + '/restore'
        assert client.post(endpoint, json={'expected_content_hash': change['after_hash']}).status_code == 422
        assert client.post(endpoint, json={'expected_content_hash': '0' * 64, 'confirm': True}).status_code == 409
        policy = container.permissions.policy
        previous = policy.mode_for('notes.write')
        try:
            policy.set_rule('notes.write', PermissionMode.deny)
            assert client.post(endpoint, json={'expected_content_hash': change['after_hash'], 'confirm': True}).status_code == 403
        finally:
            policy.set_rule('notes.write', previous)
        restored = client.post(endpoint, json={'expected_content_hash': change['after_hash'], 'confirm': True})
        assert restored.status_code == 200, restored.text
        assert restored.json()['markdown'] == 'before'
        assert len(client.get(base).json()['items']) == 2


def test_denied_write_produces_no_saved_history():
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        container = build_container()
        created = await container.agent.create_run(AgentRunCreateRequest(
            input='/tool notes.update ' + json.dumps({'note_id': note.note_id, 'markdown': 'after'}),
            provider_id='mock', model='mock-1', allowed_tools=['notes.update']))
        async with asyncio.timeout(3):
            async for event in container.agent.events(created.run_id):
                if event.event == AgentEventType.permission_required:
                    request_id = event.data['request_id']
                    break
        assert await container.agent.resolve_permission(created.run_id, request_id, 'deny')
        completed = await container.agent.wait(created.run_id)
        assert not completed.tool_results[0].success
        assert note_changes.list_changes(note.note_id) == []
        assert (await note_service.get_note(note.note_id)).markdown == 'before'
    asyncio.run(scenario())


def test_desktop_receipt_never_attributes_a_later_human_edit_to_ai(monkeypatch):
    from app.config import get_settings
    from app.services import desktop_notes
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    document = dict(file_id='stable', path='A.md', content='before', created_at=0, updated_at=1)
    def host_call(method, **params):
        if method == 'read':
            return {**document, 'hash': note_changes.content_hash(document['content'])}
        committed_hash = note_changes.content_hash(params['content'])
        document['content'] = 'human edit immediately after commit'
        return {'result': {'file_id': 'stable', 'path': 'A.md', 'hash': committed_hash}}
    monkeypatch.setattr(desktop_notes, 'call', host_call)
    async def scenario():
        token = host_bridge.vault_id.set(str(uuid4()))
        try:
            after = await update_note(NoteUpdateArguments(note_id='stable', markdown='AI content'), ToolExecutionContext('run'))
            assert 'AI content' in after['markdown']
            assert 'human edit' not in after['markdown']
            change = note_changes.list_changes('stable')[0]
            assert change['after_hash'] == note_changes.content_hash(after['markdown'])
            assert not (await note_changes.change_detail('stable', change['change_id']))['can_restore']
            with pytest.raises(ApiError):
                await note_changes.restore_change(change['change_id'])
            assert document['content'] == 'human edit immediately after commit'
        finally:
            host_bridge.vault_id.reset(token)
    asyncio.run(scenario())


def test_cancelled_write_stays_pending_for_reconciliation():
    from app.database.db import connect_knowledge
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        async def cancelled(expected):
            raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await note_changes.record_ai_write(origin='agent:run:notes.update', requested_target='A.md', note_id=note.note_id, write=cancelled)
        assert note_changes.list_changes(note.note_id) == []
        conn = connect_knowledge()
        try:
            assert conn.execute('SELECT status FROM note_changes').fetchone()['status'] == 'pending'
        finally:
            conn.close()
    asyncio.run(scenario())


def test_desktop_create_preview_checks_host_inventory_and_preserves_actual_content(monkeypatch):
    from app.config import get_settings
    from app.services import desktop_notes
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    entries = []
    def host_call(method, **params):
        if method == 'list':
            assert set(params) == {'limit', 'offset'}
            return {'items': entries, 'total': len(entries)}
        assert method == 'write'
        result = {'path': params['path'], 'file_id': 'created-id', 'hash': note_changes.content_hash(params['content'])}
        entries.append(result)
        return {'result': result}
    monkeypatch.setattr(desktop_notes, 'call', host_call)
    async def scenario():
        token = host_bridge.vault_id.set(str(uuid4()))
        try:
            container = build_container()
            tool = call('notes.create', title='中文', folder='课程', markdown='content', tags=['tag'])
            preview = await note_preview.preview_write(tool)
            assert preview['file_path'] == '课程/中文.md'
            result = await container.tools.execute(tool, ToolExecutionContext('run', reviewed_write=preview))
            assert result.success, result
            assert preview['diff'] == note_preview.content_diff('', result.output['markdown'])
            with pytest.raises(ApiError) as conflict:
                await note_preview.preview_write(tool)
            assert conflict.value.code == 'RESOURCE_CONFLICT'
        finally:
            host_bridge.vault_id.reset(token)
    asyncio.run(scenario())


def test_restore_does_not_recreate_a_deleted_empty_note():
    from app.services.vault_paths import resolve_in_vault
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        await update_note(NoteUpdateArguments(note_id=note.note_id, markdown=''), ToolExecutionContext('run'))
        change = note_changes.list_changes(note.note_id)[0]
        path = resolve_in_vault(note.file_path)
        path.unlink()
        assert not (await note_changes.change_detail(note.note_id, change['change_id']))['can_restore']
        with pytest.raises(ApiError):
            await note_changes.restore_change(change['change_id'])
        assert not path.exists()
    asyncio.run(scenario())


def test_change_while_approved_write_waits_for_serialization_is_rejected():
    async def scenario():
        note = await note_service.create_note(title='A', markdown='before', folder=None, tags=[])
        container = build_container()
        created = await container.agent.create_run(AgentRunCreateRequest(
            input='/tool notes.update ' + json.dumps({'note_id': note.note_id, 'markdown': 'AI'}),
            provider_id='mock', model='mock-1', allowed_tools=['notes.update']))
        async with asyncio.timeout(3):
            async for event in container.agent.events(created.run_id):
                if event.event == AgentEventType.permission_required:
                    request_id = event.data['request_id']
                    break
        preview = await container.agent.permission_preview(created.run_id, request_id)
        async with container.agent._write_lock:
            assert await container.agent.resolve_permission(created.run_id, request_id, 'allow_once', preview['token'])
            await note_service.update_note(note.note_id, markdown='human edit while queued')
        completed = await container.agent.wait(created.run_id)
        assert not completed.tool_results[0].success
        assert completed.tool_results[0].error_code == 'NOTE_PREVIEW_STALE'
        assert note_changes.list_changes(note.note_id) == []
        assert (await note_service.get_note(note.note_id)).markdown == 'human edit while queued'
    asyncio.run(scenario())
