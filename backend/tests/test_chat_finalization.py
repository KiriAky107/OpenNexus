"""Response teardown preserves partial answers and terminates chat tool state."""
import asyncio
from types import SimpleNamespace

import pytest

from app.contracts import ChatRequest, ModelEvent, ModelEventType as E
from app.routes import chat, utc_now
from app.services import chat_history, chat_windows


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
