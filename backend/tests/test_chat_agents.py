import asyncio
from types import SimpleNamespace
import pytest
from pydantic import BaseModel, ConfigDict
from app.contracts import ChatRequest, ToolCall, ToolDefinition, ModelCapability, Message, ModelEventType as E
from app.services import chat_agents, chat_retrieval


def test_delegation_uses_existing_runtime_limits_and_no_network(monkeypatch):
    from app.container import container
    requests = []
    async def create(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(run_id='run_test', status=SimpleNamespace(value='queued'), output=None, error_message=None)
    monkeypatch.setattr(container.agent, 'create_run', create)
    request = ChatRequest(provider_id='local', model='model', allow_agent=True, conversation_id='chat', messages=[], workspace_context={'file_path':'draft.md','content':'unsaved'})
    call = ToolCall(tool_call_id='call', name='agent.create', arguments={'input':'summarize'})
    result = asyncio.run(chat_agents.execute(call, request))
    assert result['status'] == 'queued'
    assert requests[0].metadata['conversation_id'] == 'chat'
    assert 'unsaved' in requests[0].input
    assert requests[0].allow_network is False
    assert 'notes.patch_markdown' in requests[0].allowed_tools
    with pytest.raises(ValueError):
        asyncio.run(chat_agents.execute(call, request.model_copy(update={'allow_agent':False})))


def test_chat_searches_live_mcp_catalog_and_delegates_selected_tool(monkeypatch):
    from app.container import container

    class Arguments(BaseModel):
        model_config = ConfigDict(extra='forbid')
        text: str

    async def executor(arguments, _):
        return {'text': arguments.text}

    name = 'mcp.demo-server.echo'
    container.tools.register(ToolDefinition(
        name=name,
        description='Echo text through the demo MCP server.',
        parameters=Arguments.model_json_schema(),
        source='mcp_server',
    ), Arguments, executor)
    requests = []

    async def create(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(
            run_id='run_mcp', status=SimpleNamespace(value='queued'),
            output=None, error_message=None,
        )

    monkeypatch.setattr(container.agent, 'create_run', create)
    request = ChatRequest(
        provider_id='mock', model='mock-1', allow_agent=True, messages=[]
    )
    try:
        search = asyncio.run(chat_agents.execute(ToolCall(
            tool_call_id='search', name='agent.search_tools',
            arguments={'query': 'demo echo', 'source': 'mcp_server'},
        ), request))
        assert search['total'] == 1
        assert search['items'][0]['name'] == name
        assert search['items'][0]['source'] == 'mcp_server'

        result = asyncio.run(chat_agents.execute(ToolCall(
            tool_call_id='create', name='agent.create',
            arguments={'input': 'echo the message', 'tools': [name]},
        ), request))
        assert result['run_id'] == 'run_mcp'
        assert requests[0].skill_id == 'chat-operator'
        assert name in requests[0].allowed_tools
        assert requests[0].allow_network is False
    finally:
        container.tools.unregister(name)


def test_chat_rejects_unlisted_agent_tool_selection():
    request = ChatRequest(
        provider_id='mock', model='mock-1', allow_agent=True, messages=[]
    )
    with pytest.raises(ValueError, match='unavailable'):
        asyncio.run(chat_agents.execute(ToolCall(
            tool_call_id='create', name='agent.create',
            arguments={'input': 'work', 'tools': ['mcp.missing.tool']},
        ), request))


def test_chat_delegates_once_and_keeps_snapshot_in_model_context(monkeypatch):
    calls, seen = [], []
    async def execute(call, request):
        calls.append(call)
        return {'run_id':'run_test','status':'queued'}
    monkeypatch.setattr(chat_agents, 'execute', execute)
    class Adapter:
        async def stream(self, request):
            seen.append(request)
            assert 'unsaved text' in request.system
            if len(seen) < 3:
                yield chat_retrieval.event(E.tool_call_start, {'tool_call_id':'call','name':'agent.create','arguments':{'input':'work'}})
            else:
                yield chat_retrieval.event(E.text_delta, {'text':'started'})
            yield chat_retrieval.event(E.done, {})
    request = ChatRequest(provider_id='local', model='model', use_rag=False, allow_agent=True, messages=[Message(role='user',content='do work')], workspace_context={'file_path':'a.md','content':'unsaved text'})
    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))
    async def run(): return [event async for event in chat_retrieval.stream(request, provider)]
    events = asyncio.run(run())
    assert len(calls) == 1
    assert all(t.name != 'rag.search' for t in seen[0].tools)
    assert any(e.event == E.tool_call_end and e.data.get('result',{}).get('run_id') == 'run_test' for e in events)
    assert any(e.event == E.tool_call_end and e.data['status'] == 'failed' for e in events)
