import asyncio
from types import SimpleNamespace
import pytest
from app.contracts import ChatRequest, Message, ModelCapability, ModelEventType as E
from app.services import chat_retrieval as service


def test_chat_coordination_budget_pauses_and_resumes_without_replaying_tools(monkeypatch):
    from app.services import chat_budget
    model_calls = []
    async def prepare(request):
        return request, []
    monkeypatch.setattr(service, 'prepare', prepare)
    class Adapter:
        async def stream(self, request):
            model_calls.append(request)
            if len(model_calls) == 1:
                yield service.event(E.tool_call_start, {'tool_call_id': 'search', 'name': 'rag.search', 'arguments': {'query': 'test'}})
                yield service.event(E.usage, {'input_tokens': 48000, 'output_tokens': 10})
            else:
                yield service.event(E.text_delta, {'text': 'finished'})
            yield service.event(E.done, {})
    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
    request = ChatRequest(provider_id='x', model='x', conversation_id='chat', assistant_message_id='answer',
        messages=[Message(role='user', content='question')])
    async def run():
        stream = service.stream(request, provider)
        before = []
        while True:
            item = await anext(stream)
            before.append(item)
            if item.event == E.budget_required:
                break
        assert len(model_calls) == 1
        assert sum(item.event == E.tool_call_end for item in before) == 1
        pending = before[-1].data
        assert pending['minimum_additional_tokens'] == 11
        chat_budget.resolve(pending['request_id'], 'chat', 'answer', 8000)
        after = [item async for item in stream]
        assert len(model_calls) == 2
        assert [item.event for item in after].count(E.budget_resolved) == 1
        assert [item.event for item in after].count(E.tool_call_start) == 0
        assert any(item.event == E.text_delta and item.data['text'] == 'finished' for item in after)
        assert after[-1].data['status'] == 'completed'
    asyncio.run(run())


def test_chat_budget_stop_prevents_unexecuted_agent_call(monkeypatch):
    from app.services import chat_agents, chat_budget
    executed = []
    async def execute(call, request):
        executed.append(call)
        return {'run_id': 'run_new', 'status': 'queued'}
    monkeypatch.setattr(chat_agents, 'execute', execute)
    class Adapter:
        async def stream(self, request):
            yield service.event(E.tool_call_start, {'tool_call_id': 'start', 'name': 'agent.create', 'arguments': {'input': 'work'}})
            yield service.event(E.usage, {'input_tokens': 45000, 'output_tokens': 1})
            yield service.event(E.done, {})
    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))
    request = ChatRequest(provider_id='x', model='x', conversation_id='chat-stop', assistant_message_id='answer-stop',
        allow_agent=True, use_rag=False, messages=[Message(role='user', content='work')])
    async def run():
        stream = service.stream(request, provider)
        before = []
        while True:
            item = await anext(stream)
            before.append(item)
            if item.event == E.budget_required:
                break
        assert executed == []
        assert before[-1].data['reason'] == 'agent_reservation'
        chat_budget.resolve(before[-1].data['request_id'], 'chat-stop', 'answer-stop', 0)
        after = [item async for item in stream]
        assert executed == []
        assert any(item.event == E.tool_call_end and item.data['result']['code'] == 'CHAT_BUDGET_STOPPED' for item in after)
        assert after[-1].data['status'] == 'cancelled'
    asyncio.run(run())


def test_chat_budget_approval_executes_pending_agent_call_once(monkeypatch):
    from app.services import chat_agents, chat_budget
    from app.container import container
    executed = []
    async def execute(call, request):
        executed.append(call.tool_call_id)
        return {'run_id': 'run_new', 'status': 'queued'}
    monkeypatch.setattr(chat_agents, 'execute', execute)
    monkeypatch.setattr(container.agent, 'get_run', lambda _: SimpleNamespace(
        conversation_id='chat-continue', status=SimpleNamespace(value='completed'),
        output='finished work', error_message=None))
    class Adapter:
        def __init__(self): self.rounds = 0
        async def stream(self, request):
            self.rounds += 1
            if self.rounds == 1:
                yield service.event(E.tool_call_start, {'tool_call_id': 'start', 'name': 'agent.create', 'arguments': {'input': 'work'}})
                yield service.event(E.usage, {'input_tokens': 45000, 'output_tokens': 1})
            else:
                assert request.messages[-1].role.value == 'tool'
                yield service.event(E.text_delta, {'text': 'Task started'})
            yield service.event(E.done, {})
    adapter = Adapter()
    provider = SimpleNamespace(adapter=adapter, config=SimpleNamespace(capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))
    request = ChatRequest(provider_id='x', model='x', conversation_id='chat-continue', assistant_message_id='answer-continue',
        allow_agent=True, use_rag=False, messages=[Message(role='user', content='work')])
    async def run():
        stream = service.stream(request, provider)
        while (item := await anext(stream)).event != E.budget_required:
            pass
        assert executed == []
        chat_budget.resolve(item.data['request_id'], 'chat-continue', 'answer-continue', 8000)
        after = [event async for event in stream]
        assert executed == ['retrieval_0_start']
        assert adapter.rounds == 2
        assert any(event.event == E.tool_call_end and event.data.get('result', {}).get('run_id') == 'run_new' for event in after)
        assert after[-1].data['status'] == 'completed'
    asyncio.run(run())


def test_chat_waits_for_agent_budget_decision_and_real_result(monkeypatch):
    from app.container import container
    from app.services import chat_agents

    state = {'status': 'queued'}
    async def execute(call, request):
        return {'run_id': 'run_wait', 'status': 'queued'}
    def get_run(run_id):
        assert run_id == 'run_wait'
        return SimpleNamespace(conversation_id='chat-wait',
            status=SimpleNamespace(value=state['status']),
            output='笔记已实际写入' if state['status'] == 'completed' else None,
            error_message=None)
    monkeypatch.setattr(chat_agents, 'execute', execute)
    monkeypatch.setattr(container.agent, 'get_run', get_run)
    monkeypatch.setattr(service, 'AGENT_POLL_SECONDS', .001)

    class Adapter:
        def __init__(self): self.rounds = 0
        async def stream(self, request):
            self.rounds += 1
            if self.rounds == 1:
                yield service.event(E.tool_call_start, {'tool_call_id': 'create', 'name': 'agent.create', 'arguments': {'input': '写入笔记'}})
            else:
                assert request.messages[-1].role.value == 'tool'
                assert '笔记已实际写入' in request.messages[-1].content
                yield service.event(E.text_delta, {'text': '笔记已写入。'})
            yield service.event(E.done, {})

    async def run():
        request = ChatRequest(provider_id='x', model='x', conversation_id='chat-wait',
            assistant_message_id='answer-wait', allow_agent=True, use_rag=False,
            messages=[Message(role='user', content='写入笔记')])
        provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(
            capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))
        stream = service.stream(request, provider)
        before = []
        while True:
            item = await anext(stream)
            before.append(item)
            if item.event == E.tool_call_delta and item.data.get('result', {}).get('run_id') == 'run_wait':
                break
        assert not any(item.event == E.done for item in before)
        state['status'] = 'waiting_budget'
        paused = []
        while not any(item.event == E.context_status and '预算确认' in item.data['message'] for item in paused):
            paused.append(await asyncio.wait_for(anext(stream), timeout=1))
        assert not any(item.event in {E.tool_call_end, E.done} for item in paused)
        state['status'] = 'completed'
        after = [item async for item in stream]
        result = next(item.data['result'] for item in after if item.event == E.tool_call_end)
        assert result['status'] == 'completed'
        assert result['output'] == '笔记已实际写入'
        assert any(item.event == E.text_delta and item.data['text'] == '笔记已写入。' for item in after)
        assert after[-1].data['status'] == 'completed'
    asyncio.run(run())


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
