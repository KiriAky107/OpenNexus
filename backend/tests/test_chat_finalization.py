"""Response teardown preserves partial answers and terminates chat tool state."""
import asyncio
from types import SimpleNamespace

import pytest

from app.contracts import ChatRequest, ModelEvent, ModelEventType as E
from app.routes import chat, utc_now
from app.services import chat_history, chat_windows
from app.services import chat_persistence


def request():
    chat_history.create('Teardown', 'teardown')
    return ChatRequest(provider_id='mock', model='mock', conversation_id='teardown',
                       use_saved_history=True, user_message_id='user', assistant_message_id='answer',
                       messages=[{'role': 'user', 'content': 'request'}])


def event(kind, **data):
    return ModelEvent(event=kind, sequence=0, timestamp=utc_now(), data=data)


@pytest.mark.parametrize('ending', ['cancel', 'close', 'eof', 'failure'])
def test_every_stream_exit_ends_unconfirmed_tools_without_losing_agent_result(monkeypatch, ending):
    async def events(*_):
        yield event(E.text_delta, text='partial answer')
        yield event(E.tool_call_start, tool_call_id='pending', name='agent.create')
        yield event(E.tool_call_delta, tool_call_id='pending', result={'run_id': 'agent-still-running'})
        yield event(E.tool_call_start, tool_call_id='finished', name='rag.search')
        yield event(E.tool_call_end, tool_call_id='finished', status='completed', result={'count': 1})
        if ending == 'cancel':
            await asyncio.Future()
        elif ending == 'failure':
            raise RuntimeError('synthetic interruption')

    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)

    async def scenario():
        response = await chat(request())
        for _ in range(5):
            await anext(response.body_iterator)
        if ending == 'cancel':
            pending = asyncio.create_task(anext(response.body_iterator))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        elif ending == 'close':
            await response.body_iterator.aclose()
        else:
            _ = [chunk async for chunk in response.body_iterator]

    asyncio.run(scenario())
    saved = chat_windows.window('teardown')['items'][-1]
    assert saved.message_id == 'answer' and saved.content.startswith('partial answer')
    assert saved.tool_calls[0]['status'] == 'error'
    assert saved.tool_calls[0]['error_message']
    assert 'agent-still-running' in saved.tool_calls[0]['result']
    assert saved.tool_calls[1]['status'] == 'completed'


def prepare_stream(monkeypatch, *, hang=False):
    async def events(*_):
        yield event(E.text_delta, text='answer')
        if hang:
            await asyncio.Future()
        yield event(E.done, status='completed')
    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace())
    monkeypatch.setattr('app.services.chat_retrieval.stream', events)


def test_sqlite_contention_keeps_loop_responsive_and_done_waits_for_commit(monkeypatch):
    from contextlib import closing
    from threading import Event, Thread
    from app.database.db import connect_knowledge
    prepare_stream(monkeypatch)
    locked, release = Event(), Event()

    def competing_writer():
        with closing(connect_knowledge()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            locked.set()
            release.wait(3)
            conn.execute('COMMIT')

    async def scenario():
        response = await chat(request())
        await anext(response.body_iterator)
        writer = Thread(target=competing_writer)
        writer.start()
        assert await asyncio.to_thread(locked.wait, 2)
        finishing = asyncio.create_task(anext(response.body_iterator))
        try:
            await asyncio.sleep(.03)
            # With the old synchronous finalizer this task could only run after
            # the writer's three-second safety timeout and completion of save.
            assert not finishing.done()
            assert chat_persistence._pending
        finally:
            release.set()
            await asyncio.to_thread(writer.join)
        done = await finishing
        assert 'event: Done' in done
        assert chat_windows.window('teardown')['items'][-1].content == 'answer'
        await response.body_iterator.aclose()
    asyncio.run(scenario())


def test_repeated_cancellation_and_shutdown_wait_for_the_same_write(monkeypatch):
    from threading import Event
    prepare_stream(monkeypatch, hang=True)
    started, release = Event(), Event()
    append = chat_history.append_message
    calls = []

    def delayed(*args, **kwargs):
        calls.append(kwargs['message_id'])
        started.set()
        assert release.wait(3)
        append(*args, **kwargs)
    monkeypatch.setattr(chat_history, 'append_message', delayed)

    async def scenario():
        response = await chat(request())
        await anext(response.body_iterator)
        finishing = asyncio.create_task(anext(response.body_iterator))
        await asyncio.sleep(0)
        finishing.cancel()
        assert await asyncio.to_thread(started.wait, 2)
        finishing.cancel()
        draining = asyncio.create_task(chat_persistence.shutdown())
        try:
            await asyncio.sleep(.02)
            assert not finishing.done() and not draining.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await finishing
        await draining
        assert not chat_persistence._pending
    asyncio.run(scenario())
    assert calls == ['answer']
    assert chat_windows.window('teardown')['items'][-1].content == 'answer'


def test_asgi_level_cancellation_does_not_spin_or_abandon_write(monkeypatch):
    import anyio
    from threading import Event
    prepare_stream(monkeypatch, hang=True)
    append = chat_history.append_message
    release = Event()
    def delayed(*args, **kwargs):
        assert release.wait(3)
        append(*args, **kwargs)
    monkeypatch.setattr(chat_history, 'append_message', delayed)

    async def scenario():
        response = await chat(request())
        await anext(response.body_iterator)
        async def unblock():
            await asyncio.sleep(.05)
            release.set()
        timer = asyncio.create_task(unblock())
        with anyio.move_on_after(.01) as scope:
            await anext(response.body_iterator)
        await timer
        assert scope.cancel_called
        assert not chat_persistence._pending
    asyncio.run(scenario())
    assert chat_windows.window('teardown')['items'][-1].content == 'answer'


def test_failed_history_write_is_reported_before_failed_done(monkeypatch):
    import json
    prepare_stream(monkeypatch)
    def failed(*_args, **_kwargs):
        raise OSError('synthetic write failure')
    monkeypatch.setattr(chat_history, 'append_message', failed)

    async def scenario():
        response = await chat(request())
        return [json.loads(chunk.split('data: ', 1)[1]) async for chunk in response.body_iterator]
    events = asyncio.run(scenario())
    assert [item['event'] for item in events] == ['TextDelta', 'Error', 'Done']
    assert events[1]['data']['code'] == 'CHAT_HISTORY_SAVE_FAILED'
    assert events[-1]['data']['status'] == 'failed'
    assert len({item['sequence'] for item in events}) == len(events)
