import asyncio
import json
from types import SimpleNamespace

import pytest

from app.contracts import ChatRequest, Message, ModelEvent, ModelEventType, SearchRequest
from app.routes import chat, utc_now
from app.services import note_service
from app.services.chat_context import prepare


@pytest.mark.parametrize('enabled', [True, False])
def test_chat_stream_retrieves_real_notes_and_emits_sources(monkeypatch, enabled):
    received = []

    class Adapter:
        async def stream(self, request):
            received.append(request)
            yield ModelEvent(event=ModelEventType.text_delta, sequence=0, data={'text': 'answer [1]'}, timestamp=utc_now())
            yield ModelEvent(event=ModelEventType.done, sequence=1, data={}, timestamp=utc_now())

    monkeypatch.setattr('app.routes.provider_or_404', lambda _: SimpleNamespace(adapter=Adapter()))

    async def scenario():
        note = await note_service.create_note(title='Orchard', markdown='apple orchard knowledge', folder=None, tags=[])
        request = ChatRequest(provider_id='test', model='test', use_rag=enabled,
                              system='Keep original instructions',
                              messages=[Message(role='user', content='apple')],
                              retrieval=SearchRequest(query='apple', mode='fts'))
        response = await chat(request)
        chunks = [chunk async for chunk in response.body_iterator]
        events = [json.loads(chunk.split('data: ', 1)[1]) for chunk in chunks]
        assert [e['sequence'] for e in events] == list(range(len(events)))
        assert events[-1]['event'] == 'Done'
        assert received[0].messages == request.messages
        if enabled:
            assert events[0]['event'] == 'Citation'
            assert events[0]['data']['note_id'] == note.note_id
            assert 'apple orchard knowledge' in received[0].system
            assert 'Keep original instructions' in received[0].system
        else:
            assert all(e['event'] != 'Citation' for e in events)
            assert received[0].system == request.system
        assert request.system == 'Keep original instructions'

    asyncio.run(scenario())


def test_empty_knowledge_base_has_no_invented_citations():
    async def scenario():
        request = ChatRequest(provider_id='test', model='test', messages=[Message(role='user', content='missing')])
        grounded, sources = await prepare(request)
        assert sources == []
        assert '不要编造' in grounded.system
    asyncio.run(scenario())
