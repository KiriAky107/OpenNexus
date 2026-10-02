import json
from contextlib import closing
from time import perf_counter

from fastapi.testclient import TestClient
import pytest

from app.database.db import connect_knowledge, transaction
from app.errors import ApiError
from app.main import app
from app.services import chat_history as history, chat_windows as windows


def seed(count=10000, conversation='large'):
    history.create('Synthetic window', conversation)
    stamp = '2026-09-30T08:00:00+00:00'
    with closing(connect_knowledge()) as conn, transaction(conn):
        conn.executemany('''INSERT INTO chat_messages
            (message_id,conversation_id,sequence,parent_message_id,role,content,thinking,
             citations_json,tool_calls_json,usage_json,created_at)
            VALUES(?,?,?,?,?,?,NULL,'[]',?,NULL,?)''',
            ((f'{conversation}-{index}', conversation, index, f'{conversation}-{index-1}' if index else None,
              'user' if index%2 == 0 else 'assistant', f'needle {index} ' + 'x'*1024,
              json.dumps([{'tool_call_id': f'tool-{index}', 'name': 'rag.search', 'status': 'completed', 'result': 'y'*2048}]), stamp) for index in range(count)))
        conn.execute('UPDATE chat_conversations SET active_leaf=? WHERE conversation_id=?', (f'{conversation}-{count-1}', conversation))


def ids(result): return [message.message_id for message in result['items']]


def test_large_window_decodes_only_requested_bodies_and_overlaps_are_stable(monkeypatch):
    seed()
    with closing(connect_knowledge()) as conn:
        start = perf_counter()
        baseline = conn.execute('SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY sequence', ('large',)).fetchall()
        baseline_ms = (perf_counter()-start)*1000
        baseline_bytes = sum(len(row['content'].encode())+len(row['tool_calls_json'].encode()) for row in baseline)
    decode = history._message
    decoded = []
    def record(row):
        decoded.append(row['message_id']); return decode(row)
    monkeypatch.setattr(history, '_message', record)
    start = perf_counter()
    tail = windows.window('large')
    tail_ms = (perf_counter()-start)*1000
    assert tail['total'] == 10000 and tail['start'] == 9940
    assert tail['after'] is None and tail['before']
    assert len(decoded) == 60
    assert ids(tail) == [f'large-{index}' for index in range(9940, 10000)]
    before = windows.window('large', cursor=tail['before'])
    assert before['start'] == 9910
    assert ids(before)[30:] == ids(tail)[:30]
    assert ids(windows.window('large', cursor=before['after'])) == ids(tail)
    around = windows.window('large', around='large-5000')
    assert around['start'] == 4970 and 'large-5000' in ids(around)
    assert len(decoded) == 240
    payload_bytes = len(json.dumps([message.model_dump(mode='json') for message in tail['items']]).encode())
    print(f'CHAT_WINDOW_PERF rows=10000 baseline_body_rows=10000 baseline_bytes={baseline_bytes} baseline_ms={baseline_ms:.3f} tail_body_rows=60 tail_payload_bytes={payload_bytes} tail_ms={tail_ms:.3f} around_body_rows=60')


def test_branch_cursors_pin_leaf_across_new_appends_and_keep_versions():
    history.create('Branches', 'branches')
    for message_id, role in [('u1','user'),('a1','assistant'),('u2','user'),('a2','assistant')]:
        history.append_message('branches', message_id=message_id, role=role, content=message_id)
    original = windows.window('branches', limit=2)
    history.prepare_retry('branches', 'a1')
    history.reserve_response('branches', 'retry')
    history.append_message('branches', message_id='retry', role='assistant', content='retry', parent_message_id='u1')
    history.append_message('branches', message_id='u3', role='user', content='new question')
    current = windows.window('branches')
    assert ids(current) == ['u1','retry','u3']
    assert current['items'][1].versions == ['a1','retry']
    old = windows.window('branches', cursor=original['before'], limit=2)
    assert old['branch_leaf'] == 'a2' and old['active_leaf'] == 'u3'
    assert ids(old) == ['a1','u2']
    assert old['items'][0].versions == ['a1','retry']
    assert ids(windows.window('branches', branch_leaf='a2', around='a1', limit=4)) == ['u1','a1','u2','a2']
    with pytest.raises(ApiError) as error: windows.window('branches', around='a1')
    assert error.value.code == 'CHAT_MESSAGE_OUTSIDE_BRANCH'


def test_empty_short_edges_cursor_validation_and_http():
    history.create('Empty', 'empty')
    assert windows.window('empty') == {'items': [], 'total': 0, 'start': 0, 'branch_leaf': None, 'active_leaf': None, 'before': None, 'after': None}
    seed(3, 'short')
    first = windows.window('short', around='short-0', limit=2)
    last = windows.window('short', cursor=first['after'], limit=2)
    assert ids(first) == ['short-0','short-1'] and first['before'] is None
    assert ids(last) == ['short-1','short-2'] and last['after'] is None
    for cursor in ['bad!', 'e30', 'a'*2049]:
        with pytest.raises(ApiError) as error: windows.window('short', cursor=cursor)
        assert error.value.code == 'CHAT_CURSOR_INVALID'
    with pytest.raises(ApiError) as error: windows.window('empty', cursor=first['after'])
    assert error.value.code == 'CHAT_CURSOR_SCOPE_CONFLICT'
    with TestClient(app) as client:
        assert client.get('/api/chat/conversations/short/window', params={'around': 'short-0'}).json()['total'] == 3
        assert client.get('/api/chat/conversations/missing/window').status_code == 404
        assert client.get('/api/chat/conversations/short/window', params={'limit': 1000}).status_code == 422
        assert client.get('/api/chat/conversations/short/window', params={'cursor': first['after'], 'around': 'short-0'}).status_code == 400


def test_cross_vault_cursor_cannot_read_same_conversation_and_message_ids(monkeypatch):
    from app import host_bridge
    from app.config import get_settings
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop'); get_settings.cache_clear()
    first = host_bridge.vault_id.set('11111111-1111-4111-8111-111111111111')
    try:
        seed(4, 'same')
        cursor = windows.window('same', limit=2)['before']
        second = host_bridge.vault_id.set('22222222-2222-4222-8222-222222222222')
        try:
            seed(4, 'same')
            with pytest.raises(ApiError) as error: windows.window('same', cursor=cursor)
            assert error.value.code == 'CHAT_CURSOR_SCOPE_CONFLICT'
        finally: host_bridge.vault_id.reset(second)
        assert windows.window('same', cursor=cursor)['total'] == 4
    finally:
        host_bridge.vault_id.reset(first); get_settings.cache_clear()


def test_cycle_is_bounded_and_deleted_conversation_invalidates_cursor():
    seed(4, 'cycle')
    cursor = windows.window('cycle', limit=2)['before']
    with closing(connect_knowledge()) as conn:
        conn.execute("UPDATE chat_messages SET parent_message_id='cycle-3' WHERE message_id='cycle-0'")
    with pytest.raises(ApiError) as error: windows.window('cycle')
    assert error.value.code == 'CHAT_BRANCH_CORRUPT'
    history.delete('cycle')
    with pytest.raises(ApiError) as error: windows.window('cycle', cursor=cursor)
    assert error.value.code == 'CONVERSATION_NOT_FOUND'


def saved_request(**kwargs):
    from app.contracts import ChatRequest
    return ChatRequest(provider_id='mock', model='mock-1', conversation_id='context', use_saved_history=True,
                       messages=[{'role': 'user', 'content': 'followup'}], **kwargs)


def test_saved_generation_uses_complete_branch_content_and_thinking_not_reading_window(monkeypatch):
    seed(80, 'context')
    with closing(connect_knowledge()) as conn:
        conn.execute("UPDATE chat_messages SET thinking='earlier reasoning' WHERE message_id='context-1'")
    monkeypatch.setattr(history, '_message', lambda _: pytest.fail('Generation must not decode tool-result bodies'))
    prepared = windows.prepare_request(saved_request(expected_branch_leaf='context-79'), 'new-user', 'new-answer')
    assert len(prepared.messages) == 81
    assert prepared.messages[0].content.startswith('needle 0 ')
    assert prepared.messages[1].reasoning_content == 'earlier reasoning'
    assert prepared.messages[-1].content == 'followup'
    assert prepared.user_message_id == 'new-user'
    with closing(connect_knowledge()) as conn:
        row = conn.execute("SELECT active_leaf,active_response_id FROM chat_conversations WHERE conversation_id='context'").fetchone()
    assert tuple(row) == ('new-user','new-answer')


def test_saved_regeneration_uses_stored_user_and_edit_uses_exact_branch_prefix():
    seed(80, 'context')
    regenerated = windows.prepare_request(saved_request(retry_message_id='context-79'), 'ignored-client-user', 'retry')
    assert regenerated.user_message_id == 'context-78'
    assert len(regenerated.messages) == 79
    assert regenerated.messages[-1].content.startswith('needle 78 ')
    assert regenerated.metadata['retry_write_policy'] == 'full'
    edited = windows.prepare_request(saved_request(retry_message_id='context-60'), 'edited-user', 'edited-answer')
    assert len(edited.messages) == 61 and edited.messages[-1].content == 'followup'
    assert edited.messages[-2].content.startswith('needle 59 ')
    assert edited.metadata['retry_write_policy'] == 'read_only'
    assert ids(windows.window('context', branch_leaf='context-79'))[-1] == 'context-79'


def test_saved_send_rejects_stale_branch_atomically_and_reservation_checks_concurrent_append(monkeypatch):
    seed(4, 'context')
    with pytest.raises(ApiError) as error:
        windows.prepare_request(saved_request(expected_branch_leaf='context-1'), 'stale-user', 'stale-answer')
    assert error.value.code == 'CHAT_BRANCH_CHANGED'
    with closing(connect_knowledge()) as conn:
        assert conn.execute("SELECT 1 FROM chat_messages WHERE message_id='stale-user'").fetchone() is None
    generation = windows.generation_messages
    def competing_append(conversation, leaf):
        result = generation(conversation, leaf)
        history.append_message(conversation, message_id='other-window', role='user', content='other window')
        return result
    monkeypatch.setattr(windows, 'generation_messages', competing_append)
    with pytest.raises(ApiError) as error:
        windows.prepare_request(saved_request(expected_branch_leaf='context-3'), 'new-user', 'new-answer')
    assert error.value.code == 'CHAT_BRANCH_CHANGED'
    with closing(connect_knowledge()) as conn:
        assert conn.execute("SELECT active_response_id FROM chat_conversations WHERE conversation_id='context'").fetchone()[0] is None


def test_saved_request_route_preserves_full_context_and_persists_reply_under_new_user(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.contracts import ModelEvent, ModelEventType
    from app.routes import chat
    seed(80, 'context')
    seen = []
    async def events(request, provider):
        seen.append(request)
        yield ModelEvent(event=ModelEventType.text_delta, sequence=0, data={'text': 'reply'})
    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)
    async def scenario():
        response = await chat(saved_request(expected_branch_leaf='context-79', user_message_id='route-user', assistant_message_id='route-answer'))
        _ = [chunk async for chunk in response.body_iterator]
    asyncio.run(scenario())
    assert len(seen[0].messages) == 81 and seen[0].messages[0].content.startswith('needle 0 ')
    with closing(connect_knowledge()) as conn:
        assert conn.execute("SELECT parent_message_id FROM chat_messages WHERE message_id='route-answer'").fetchone()[0] == 'route-user'
