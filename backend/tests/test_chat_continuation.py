import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.contracts import ChatRequest, Message, ModelCapability, ModelEventType as E, ModelRequest, ToolDefinition
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.base import ProviderError
from app.services import chat_agents, chat_retrieval as service


def collect(adapter, **kwargs):
    request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True,
                          messages=[Message(role='user', content='整理笔记')], **kwargs)
    provider = SimpleNamespace(adapter=adapter, config=SimpleNamespace(capabilities=[ModelCapability.tool_calling]))
    async def run():
        return [item async for item in service.stream(request, provider)]
    return asyncio.run(run())


def test_discovery_does_not_exhaust_creation_or_status_budget(monkeypatch):
    executed, requests = [], []
    async def execute(call, _):
        executed.append(call.name)
        return {'items': []} if call.name.endswith('search_tools') else {'run_id': 'run_one', 'status': 'queued'}
    monkeypatch.setattr(chat_agents, 'execute', execute)
    class Adapter:
        async def stream(self, request):
            requests.append(request)
            names = ['agent.search_tools'] * 4 + ['agent.create', 'agent.status']
            if len(requests) <= len(names):
                name = names[len(requests) - 1]
                assert name in {tool.name for tool in request.tools}
                yield service.event(E.tool_call_start, {'tool_call_id': 'call', 'name': name, 'arguments': {}})
            else:
                assert request.messages[-1].role.value == 'tool'
                yield service.event(E.text_delta, {'text': '任务已排队，不是已完成。'})
            yield service.event(E.done, {'status': 'completed'})
    events = collect(Adapter())
    assert executed == ['agent.search_tools'] * 4 + ['agent.create', 'agent.status']
    assert len(requests) == 7
    assert events[-1].data['status'] == 'completed'
    assert len({e.data['tool_call_id'] for e in events if e.event == E.tool_call_start}) == 6


def test_protocol_text_is_not_executed_and_repair_is_bounded(monkeypatch):
    async def forbidden(*_):
        pytest.fail('Protocol text must never execute a tool')
    monkeypatch.setattr(chat_agents, 'execute', forbidden)
    seen = []
    class Adapter:
        async def stream(self, request):
            seen.append(request)
            yield service.event(E.text_delta, {'text': '<｜DSML｜invoke name="agent.create">fake</｜DSML｜invoke>'})
            yield service.event(E.done, {'status': 'completed'})
    events = collect(Adapter())
    assert len(seen) == 2
    assert events[-1].data['status'] == 'failed'
    assert any(e.data.get('code') == 'CHAT_TOOL_PROTOCOL_INVALID' for e in events)


def test_normal_direct_answer_does_not_force_agent_creation():
    seen = []
    class Adapter:
        async def stream(self, request):
            seen.append(request)
            yield service.event(E.text_delta, {'text': '这里是说明。'})
            yield service.event(E.done, {})
    assert collect(Adapter())[-1].data['status'] == 'completed'
    assert len(seen) == 1


def test_search_ranks_partial_bilingual_matches_without_leaking_unavailable_tools(monkeypatch):
    monkeypatch.setattr(chat_agents, '_available_agent_tools', lambda _: [
        ToolDefinition(name='notes.create', description='Create a note', parameters={}),
        ToolDefinition(name='tasks.read', description='Read a task', parameters={}),
    ])
    result = chat_agents._search_tools(chat_agents.SearchToolsArguments(query='创建笔记 写入 markdown 保存 note create'), None)
    assert result['items'][0]['name'] == 'notes.create'
    assert {item['name'] for item in result['items']} == {'notes.create'}


@pytest.mark.parametrize('reason,code', [('length', 'PROVIDER_OUTPUT_LIMIT'), ('content_filter', 'PROVIDER_CONTENT_FILTERED'), ('unknown', 'PROVIDER_UNEXPECTED_STOP')])
def test_finish_reason_does_not_report_success_or_execute_partial_calls(reason, code):
    def handler(_):
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c', 'function': {'name': 'agent_create', 'arguments': '{"input":"work"}'}}]}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': reason}]},
        ]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n')
    adapter = OpenAICompatibleProvider('https://provider.test', None, SimpleNamespace(resolve=lambda _: None), transport=httpx.MockTransport(handler))
    async def run():
        return [e async for e in adapter.stream(ModelRequest(provider_id='test', model='demo', messages=[]))]
    events = asyncio.run(run())
    assert events[-1].data['status'] == 'failed'
    assert any(e.data.get('code') == code for e in events)
    assert not any(e.event == E.tool_call_start for e in events)


def test_nonstream_length_limit_is_also_rejected():
    adapter = OpenAICompatibleProvider('https://provider.test', None, SimpleNamespace(resolve=lambda _: None),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]})))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(ModelRequest(provider_id='test', model='demo', messages=[])))
    assert error.value.code == 'PROVIDER_OUTPUT_LIMIT'
