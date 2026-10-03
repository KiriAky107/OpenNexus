"""Atomic generation preparation, using isolated databases and the async route."""

import asyncio
from contextlib import closing
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.contracts import ChatRequest, ModelEvent, ModelEventType
from app.database.db import connect_knowledge
from app.errors import ApiError
from app.routes import chat, utc_now
from app.services import chat_history as history, chat_windows as windows


def seed():
    history.create('Branches', 'branches')
    for message_id, role in [('u1', 'user'), ('a1', 'assistant'), ('u2', 'user'), ('a2', 'assistant')]:
        history.append_message('branches', message_id=message_id, role=role, content=message_id)
    history.reserve_response('branches', 'previous-response')


def request(name, retry='u2', **kwargs):
    return ChatRequest(
        provider_id='mock', model='mock', conversation_id='branches',
        use_saved_history=True, expected_branch_leaf='a2', retry_message_id=retry,
        user_message_id=f'{name}-user', assistant_message_id=f'{name}-answer',
        messages=[{'role': 'user', 'content': name}], **kwargs,
    )


def snapshot():
    with closing(connect_knowledge()) as conn:
        return (
            [dict(row) for row in conn.execute('SELECT * FROM chat_conversations ORDER BY conversation_id')],
            [dict(row) for row in conn.execute('SELECT * FROM chat_messages ORDER BY message_id')],
        )


@pytest.mark.parametrize('retry_a,retry_b', [('u2', 'u1'), ('a2', 'u1'), ('u2', None)])
def test_concurrent_route_requests_commit_one_exact_branch_and_reject_stale_peer(monkeypatch, retry_a, retry_b):
    seed()
    prepare = windows.prepare_request
    barrier = Barrier(2)
    seen = []

    def simultaneous_prepare(*args):
        barrier.wait(timeout=10)
        return prepare(*args)

    async def events(prepared, _provider):
        seen.append(prepared)
        yield ModelEvent(event=ModelEventType.text_delta, sequence=0, data={'text': 'answer'}, timestamp=utc_now())

    monkeypatch.setattr(windows, 'prepare_request', simultaneous_prepare)
    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)
    requests = [request('A', retry_a), request('B', retry_b)]

    async def scenario():
        results = await asyncio.gather(*(chat(item) for item in requests), return_exceptions=True)
        successes = [index for index, result in enumerate(results) if not isinstance(result, BaseException)]
        assert len(successes) == 1
        winner = successes[0]
        loser = results[1 - winner]
        assert isinstance(loser, ApiError) and loser.code == 'CHAT_BRANCH_CHANGED'
        _ = [chunk async for chunk in results[winner].body_iterator]
        return winner

    winner = asyncio.run(scenario())
    winning = requests[winner]
    retry = winning.retry_message_id
    expected_prefix = {'u1': [], 'u2': ['u1', 'a1'], 'a2': ['u1', 'a1', 'u2'], None: ['u1', 'a1', 'u2', 'a2']}[retry]
    expected_content = expected_prefix if retry == 'a2' else expected_prefix + [winning.messages[-1].content]
    assert [message.content for message in seen[0].messages] == expected_content
    user_id = 'u2' if retry == 'a2' else winning.user_message_id
    branch = windows.window('branches')
    assert [message.message_id for message in branch['items']][-2:] == [user_id, winning.assistant_message_id]
    assert branch['items'][-1].content == 'answer'
    with closing(connect_knowledge()) as conn:
        assert conn.execute('SELECT 1 FROM chat_messages WHERE message_id=?', (requests[1 - winner].user_message_id,)).fetchone() is None
        assert conn.execute('SELECT parent_message_id FROM chat_messages WHERE message_id=?', (winning.assistant_message_id,)).fetchone()[0] == user_id
        if retry != 'a2':
            assert conn.execute('SELECT parent_message_id FROM chat_messages WHERE message_id=?', (user_id,)).fetchone()[0] == (expected_prefix[-1] if expected_prefix else None)
    # Both original branches remain available after editing the root or a later turn.
    assert [message.message_id for message in windows.window('branches', branch_leaf='a2')['items']] == ['u1', 'a1', 'u2', 'a2']


@pytest.mark.parametrize('retry', ['u1', 'u2', 'a2', None])
@pytest.mark.parametrize('stage', ['context', 'reservation'])
def test_failure_rolls_back_user_branch_title_and_response_reservation(monkeypatch, retry, stage):
    seed()
    before = snapshot()
    reserve = history._reserve_response_in_transaction

    def fail_context(*_args):
        raise ApiError(409, 'CHAT_BRANCH_CORRUPT', 'Synthetic invalid branch')

    def fail_reservation(*args):
        reserve(*args)
        raise sqlite3.OperationalError('Synthetic reservation write failure')

    if stage == 'context':
        monkeypatch.setattr(windows, '_generation_messages_in_transaction', fail_context)
        expected = ApiError
    else:
        monkeypatch.setattr(history, '_reserve_response_in_transaction', fail_reservation)
        expected = sqlite3.OperationalError
    with pytest.raises(expected):
        windows.prepare_request(request('edited', retry), 'edited-user', 'edited-answer')
    assert snapshot() == before


def test_root_edit_and_regeneration_use_explicit_parent_and_stored_user():
    seed()
    prepared = windows.prepare_request(request('root', 'u1'), 'root-user', 'root-answer')
    assert [message.content for message in prepared.messages] == ['root']
    with closing(connect_knowledge()) as conn:
        assert conn.execute("SELECT parent_message_id FROM chat_messages WHERE message_id='root-user'").fetchone()[0] is None
    history.append_message('branches', message_id='root-answer', role='assistant', content='root reply', parent_message_id='root-user')
    regenerate = request('untrusted replacement', 'root-answer').model_copy(update={'expected_branch_leaf': 'root-answer'})
    prepared = windows.prepare_request(regenerate, 'ignored-user', 'regenerated-answer')
    assert prepared.user_message_id == 'root-user'
    assert [message.content for message in prepared.messages] == ['root']
    assert prepared.metadata['retry_write_policy'] == 'full'
    with closing(connect_knowledge()) as conn:
        assert conn.execute("SELECT 1 FROM chat_messages WHERE message_id='ignored-user'").fetchone() is None


def test_branch_selection_invalidates_pending_edit_and_late_response_cannot_steal_it():
    seed()
    prepared = windows.prepare_request(request('edited'), 'edited-user', 'edited-answer')
    history.select_version('branches', 'u2')
    before = snapshot()
    stale = request('stale').model_copy(update={'expected_branch_leaf': prepared.user_message_id})
    with pytest.raises(ApiError, match='会话分支已变化'):
        windows.prepare_request(stale, 'stale-user', 'stale-answer')
    assert snapshot() == before
    selected_leaf = windows.window('branches')['active_leaf']
    history.append_message('branches', message_id='edited-answer', role='assistant', content='late', parent_message_id='edited-user')
    assert windows.window('branches')['active_leaf'] == selected_leaf
    assert windows.window('branches', branch_leaf='edited-answer')['items'][-1].content == 'late'


def test_cancelled_old_stream_keeps_partial_history_without_replacing_new_branch(monkeypatch):
    seed()

    async def events(_request, _provider):
        yield ModelEvent(event=ModelEventType.text_delta, sequence=0, data={'text': 'partial'}, timestamp=utc_now())
        await asyncio.Future()

    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)

    async def scenario():
        response = await chat(request('first'))
        await anext(response.body_iterator)
        reading = asyncio.create_task(anext(response.body_iterator))
        await asyncio.sleep(0)
        second = request('second', 'u1').model_copy(update={'expected_branch_leaf': 'first-user'})
        await asyncio.to_thread(windows.prepare_request, second, 'second-user', 'second-answer')
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading

    asyncio.run(scenario())
    assert windows.window('branches')['active_leaf'] == 'second-user'
    partial = windows.window('branches', branch_leaf='first-answer')['items']
    assert partial[-1].content == 'partial' and partial[-1].parent_message_id == 'first-user'


def test_missing_user_and_invalid_regeneration_parent_do_not_leave_retry_switch():
    seed()
    empty = request('edited').model_copy(update={'messages': []})
    before = snapshot()
    with pytest.raises(ApiError) as error:
        windows.prepare_request(empty, 'edited-user', 'edited-answer')
    assert error.value.code == 'CHAT_USER_REQUIRED'
    assert snapshot() == before
    with closing(connect_knowledge()) as conn:
        conn.execute("UPDATE chat_messages SET parent_message_id=NULL WHERE message_id='a2'")
    before = snapshot()
    with pytest.raises(ApiError) as error:
        windows.prepare_request(request('regenerate', 'a2'), 'ignored-user', 'new-answer')
    assert error.value.code == 'CHAT_USER_REQUIRED'
    assert snapshot() == before


def test_atomic_retry_retains_server_tool_policy_and_legacy_explicit_context():
    seed()
    history.append_message('branches', message_id='other-answer', role='assistant', content='attempt', parent_message_id='u2',
                           tool_calls=[{'name': 'agent.create', 'status': 'error'}])
    retry = request('provided legacy context', 'a2', metadata={'retry_write_policy': 'full'}).model_copy(update={'use_saved_history': False})
    prepared = windows.prepare_request(retry, 'client-user', 'legacy-answer')
    assert [message.content for message in prepared.messages] == ['provided legacy context']
    assert prepared.user_message_id == 'u2'
    assert prepared.metadata['retry_write_policy'] == 'create_only'
    history.append_message('branches', message_id='write-answer', role='assistant', content='attempt', parent_message_id='u2',
                           tool_calls=[{'name': 'agent.define', 'status': 'error'}])
    prepared = windows.prepare_request(retry, 'client-user', 'legacy-answer-2')
    assert prepared.metadata['retry_write_policy'] == 'read_only'


def test_concurrent_routes_keep_context_and_writes_in_their_vault(monkeypatch):
    from app import host_bridge
    from app.config import get_settings

    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    vaults = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222']
    for index, vault in enumerate(vaults):
        token = host_bridge.vault_id.set(vault)
        try:
            seed()
            with closing(connect_knowledge()) as conn:
                conn.execute("UPDATE chat_messages SET content=? WHERE message_id='u1'", (f'vault-{index}',))
        finally:
            host_bridge.vault_id.reset(token)
    seen = {}

    async def events(prepared, _provider):
        seen[host_bridge.vault_id.get()] = [message.content for message in prepared.messages]
        yield ModelEvent(event=ModelEventType.text_delta, sequence=0, data={'text': 'isolated reply'}, timestamp=utc_now())

    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)

    async def generate(vault):
        token = host_bridge.vault_id.set(vault)
        try:
            response = await chat(request('same edit'))
            _ = [chunk async for chunk in response.body_iterator]
            assert windows.window('branches')['active_leaf'] == 'same edit-answer'
        finally:
            host_bridge.vault_id.reset(token)

    async def scenario():
        await asyncio.gather(*(generate(vault) for vault in vaults))

    try:
        asyncio.run(scenario())
        assert seen == {vault: [f'vault-{index}', 'a1', 'same edit'] for index, vault in enumerate(vaults)}
    finally:
        get_settings.cache_clear()
