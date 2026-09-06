import asyncio
from types import SimpleNamespace
import pytest
from app.contracts import ChatRequest, Message, ModelCapability, ModelEventType as E
from app.services import chat_retrieval as service


def test_stream_searches_again_and_preserves_numbers(monkeypatch):
    seen = []
    async def prepare(request):
        query = request.retrieval.query if request.retrieval else 'initial'
        return request, [{'block_id': 'a' if query == 'initial' else 'b', 'number': 1, 'content': query, 'citation_id': 'cit_blk_test'}]
    monkeypatch.setattr(service, 'prepare', prepare)
    class Adapter:
        async def stream(self, request):
            seen.append(request)
            if len(seen) == 1:
                yield service.event(E.text_delta, {'text': '需要补充资料。'})
                yield service.event(E.tool_call_start, {'tool_call_id': 'call', 'name': 'rag.search'})
                yield service.event(E.tool_call_delta, {'tool_call_id': 'call', 'arguments_delta': '{"query":"new"}'})
                yield service.event(E.tool_call_end, {'tool_call_id': 'call'})
            else:
                assert request.messages[-1].role.value == 'tool'
                assert '"number": 1' in request.messages[-1].content
                assert 'cit_blk_test' not in request.messages[-1].content
                assert 'block_id' not in request.messages[-1].content
                yield service.event(E.text_delta, {'text': '根据新证据 [1]'})
            yield service.event(E.usage, {'input_tokens': 10, 'output_tokens': 2})
            yield service.event(E.done, {})
    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
    request = ChatRequest(provider_id='x', model='x', messages=[Message(role='user', content='question')])
    async def run(): return [item async for item in service.stream(request, provider)]
    events = asyncio.run(run())
    assert len(seen) == 2
    assert any(e.event == E.text_delta and e.data['text'] == '\n\n' for e in events)
    assert events[0].event == E.text_delta
    assert [e.data['number'] for e in events if e.event == E.citation] == [1]
    assert sum(e.event == E.done for e in events) == 1
    assert next(e.data for e in events if e.event == E.usage) == {'input_tokens': 20, 'output_tokens': 4}
    assert [e.event for e in events].index(E.tool_call_end) > max(i for i, e in enumerate(events) if e.event == E.citation)


@pytest.mark.parametrize('tool_name', ['rag.search', 'notes.update'])
def test_loop_is_bounded_and_never_executes_write_tools(monkeypatch, tool_name):
    searches, requests = [], []
    async def prepare(request):
        searches.append(request)
        return request, []
    monkeypatch.setattr(service, 'prepare', prepare)
    class Adapter:
        async def stream(self, request):
            requests.append(request)
            yield service.event(E.tool_call_start, {'tool_call_id': 'same', 'name': tool_name, 'arguments': {'query': 'again'}})
            yield service.event(E.done, {})
    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
    async def run():
        return [e async for e in service.stream(ChatRequest(provider_id='x', model='x', messages=[Message(role='user', content='q')]), provider)]
    events = asyncio.run(run())
    assert len(requests) == 4
    assert requests[-1].tools == []
    assert len(searches) == (3 if tool_name == 'rag.search' else 0)
    assert len({e.data['tool_call_id'] for e in events if e.event == E.tool_call_start}) == 4
    assert events[-1].data['status'] == 'failed'


def test_closing_stream_closes_provider(monkeypatch):
    closed = []
    async def prepare(request): return request, []
    monkeypatch.setattr(service, 'prepare', prepare)
    class Adapter:
        async def stream(self, request):
            try:
                yield service.event(E.text_delta, {'text': 'partial'})
                await asyncio.sleep(60)
            finally:
                closed.append(True)
    async def run():
        provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
        events = service.stream(ChatRequest(provider_id='x', model='x', messages=[Message(role='user', content='q')]), provider)
        await anext(events)
        await events.aclose()
    asyncio.run(run())
    assert closed == [True]


def test_no_search_without_a_model_call_and_timeout_allows_continuation(monkeypatch):
    called = []
    monkeypatch.setattr(service, 'SEARCH_TIMEOUT_SECONDS', .01)
    async def slow_search(request):
        called.append(True)
        await asyncio.sleep(10)
    monkeypatch.setattr(service, 'prepare', slow_search)
    requests = []
    class Adapter:
        async def stream(self, request):
            requests.append(request)
            if len(requests) == 1:
                assert called == []
                yield service.event(E.text_delta, {'text': '我来查看笔记。'})
                yield service.event(E.tool_call_start, {'tool_call_id': 'search', 'name': 'rag.search', 'arguments': {'query': 'q'}})
            else:
                assert 'Retrieval failed' in request.messages[-1].content
                yield service.event(E.text_delta, {'text': '检索超时，暂时无法核对笔记。'})
            yield service.event(E.done, {})
    async def run():
        provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
        return [e async for e in service.stream(ChatRequest(provider_id='x', model='x', messages=[Message(role='user', content='q')]), provider)]
    events = asyncio.run(run())
    assert events[0].event == E.text_delta
    assert next(e for e in events if e.event == E.tool_call_end).data['status'] == 'failed'
    assert events[-1].data['status'] == 'completed'


def test_thinking_is_replayed_on_real_compatible_wire(monkeypatch):
    import json
    import httpx
    from app.providers.openai_compatible import OpenAICompatibleProvider
    requests = []
    async def prepare(request): return request, []
    monkeypatch.setattr(service, 'prepare', prepare)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            alias = payload['tools'][0]['function']['name']
            deltas = [{'reasoning_content': 'Need '}, {'reasoning_content': 'more evidence.'},
                      {'tool_calls': [{'index': i, 'id': f'call{i}', 'type': 'function', 'function': {'name': alias, 'arguments': '{"query":"Python"}'}} for i in range(2)]}]
        else:
            assistant = next(m for m in payload['messages'] if m.get('tool_calls'))
            if assistant.get('reasoning_content') != 'Need more evidence.':
                return httpx.Response(400, json={'error': {'message': 'reasoning_content required'}})
            assert {c['id'] for c in assistant['tool_calls']} == {m['tool_call_id'] for m in payload['messages'] if m['role'] == 'tool'}
            deltas = [{'content': 'Answer after retrieval'}]
        body = ''.join('data: ' + json.dumps({'choices': [{'delta': delta}]}) + '\n\n' for delta in deltas) + 'data: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
    adapter = OpenAICompatibleProvider('https://provider.test', None, SimpleNamespace(resolve=lambda _: None), transport=httpx.MockTransport(handler))
    provider = SimpleNamespace(adapter=adapter, config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
    async def run():
        return [e async for e in service.stream(ChatRequest(provider_id='x', model='x', messages=[Message(role='user', content='q')]), provider)]
    events = asyncio.run(run())
    assert len(requests) == 2
    assert not any(e.event == E.error for e in events)
    assert any(e.data.get('text') == 'Answer after retrieval' for e in events)
