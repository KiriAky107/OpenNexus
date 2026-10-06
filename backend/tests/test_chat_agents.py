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


def test_chat_source_tools_are_discoverable_and_survive_the_operator_skill_ceiling():
    from app.container import container
    request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True, messages=[])
    available = {item.name for item in chat_agents._available_agent_tools(request)}
    configuration = chat_agents._operator_configuration(request)
    assert configuration is not None
    for name in ['experiments.files.list', 'experiments.files.read', 'experiments.files.write']:
        assert name in available and name in configuration.allowed_tools
        assert container.tools.get(name).definition.permission in configuration.permissions
    assert 'experiments.run' not in configuration.permissions
    assert 'experiments.import' not in configuration.permissions


def test_chat_creation_ignores_model_supplied_provider_override(monkeypatch):
    from app.container import container

    requests = []

    async def create(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(run_id='run_selected', status=SimpleNamespace(value='queued'),
                               output=None, error_message=None)

    monkeypatch.setattr(container.agent, 'create_run', create)
    request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True, messages=[])
    result = asyncio.run(chat_agents.execute(ToolCall(
        tool_call_id='create', name='agent.create',
        arguments={'input': 'create a note', 'tools': ['notes.create'],
                   'provider_id': 'openai', 'model': 'gpt-4o-mini'},
    ), request))
    assert result['run_id'] == 'run_selected'
    assert requests[0].provider_id == 'mock'
    assert requests[0].model == 'mock-1'


def test_regenerated_agent_create_reuses_user_turn_operation_id():
    request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True, messages=[],
                          conversation_id='conversation', user_message_id='user-turn',
                          assistant_message_id='first-answer')
    first = ToolCall(tool_call_id='first', name='agent.create', arguments={'input': 'write note A'})
    regenerated = ToolCall(tool_call_id='second', name='agent.create', arguments={'input': 'write note A, revised'})
    assert chat_agents.operation_id(first, request) == chat_agents.operation_id(
        regenerated, request.model_copy(update={'assistant_message_id': 'second-answer'}))


def test_create_only_retry_guard_rejects_other_writes():
    request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True, messages=[],
                          retry_message_id='previous', metadata={'retry_write_policy': 'create_only'})
    with pytest.raises(ValueError, match='Only the original Agent launch'):
        asyncio.run(chat_agents.execute(ToolCall(
            tool_call_id='start', name='agent.start', arguments={}), request))


def test_regenerated_create_returns_original_run_without_relaunch(monkeypatch):
    from app import container as container_module

    isolated = container_module.build_container()
    monkeypatch.setattr(container_module, 'container', isolated)

    async def scenario():
        try:
            request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True, messages=[],
                                  conversation_id='conversation', user_message_id='user-turn',
                                  assistant_message_id='answer-one')
            first = await chat_agents.execute(ToolCall(
                tool_call_id='first', name='agent.create', arguments={'input': 'Say hello'}), request)
            await isolated.agent.wait(first['run_id'])
            regenerated = await chat_agents.execute(ToolCall(
                tool_call_id='second', name='agent.create', arguments={'input': 'Say hello with different wording'}),
                request.model_copy(update={'assistant_message_id': 'answer-two',
                                           'retry_message_id': 'answer-one',
                                           'metadata': {'retry_write_policy': 'create_only'}}))
            assert regenerated['run_id'] == first['run_id']
            assert first['reused'] is False
            assert regenerated['reused'] is True
            assert isolated.agent.list_runs(20, 0)[1] == 1
        finally:
            await isolated.agent.shutdown()

    asyncio.run(scenario())


def test_chat_receives_real_agent_result_before_final_reply(monkeypatch):
    from app import container as container_module

    isolated = container_module.build_container()
    monkeypatch.setattr(container_module, 'container', isolated)

    class ChatAdapter:
        def __init__(self): self.rounds = 0
        async def stream(self, request):
            self.rounds += 1
            if self.rounds == 1:
                yield chat_retrieval.event(E.tool_call_start, {
                    'tool_call_id': 'create', 'name': 'agent.create',
                    'arguments': {'input': 'Say hello'},
                })
            else:
                result = request.messages[-1].content
                assert '"status": "completed"' in result
                yield chat_retrieval.event(E.text_delta, {'text': '智能体已完成。'})
            yield chat_retrieval.event(E.done, {})

    async def scenario():
        try:
            request = ChatRequest(provider_id='mock', model='mock-1', allow_agent=True,
                use_rag=False, conversation_id='real-agent-chat',
                user_message_id='real-agent-turn', assistant_message_id='real-agent-answer',
                messages=[Message(role='user', content='Say hello')])
            provider = SimpleNamespace(adapter=ChatAdapter(), config=SimpleNamespace(
                capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))
            events = [item async for item in chat_retrieval.stream(request, provider)]
            finished = next(item for item in events if item.event == E.tool_call_end)
            assert finished.data['result']['status'] == 'completed'
            assert isolated.agent.get_run(finished.data['result']['run_id']).status.value == 'completed'
            assert next(item for item in events if item.event == E.tool_call_delta and item.data.get('result')).data['result']['status'] == 'queued'
            assert [item.event for item in events].index(E.tool_call_end) < [item.event for item in events].index(E.text_delta)
            assert events[-1].data['status'] == 'completed'
        finally:
            await isolated.agent.shutdown()

    asyncio.run(scenario())


def test_chat_delegates_once_and_keeps_snapshot_in_model_context(monkeypatch):
    from app.container import container
    calls, seen = [], []
    async def execute(call, request):
        calls.append(call)
        return {'run_id':'run_test','status':'queued'}
    monkeypatch.setattr(chat_agents, 'execute', execute)
    monkeypatch.setattr(container.agent, 'get_run', lambda _: SimpleNamespace(
        conversation_id=None, status=SimpleNamespace(value='completed'),
        output='finished work', error_message=None))
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


def test_safe_regeneration_offers_agent_creation_without_a_saved_definition():
    seen = []

    class Adapter:
        async def stream(self, request):
            seen.append(request)
            yield chat_retrieval.event(E.text_delta, {'text': 'ready'})
            yield chat_retrieval.event(E.done, {})

    provider = SimpleNamespace(adapter=Adapter(), config=SimpleNamespace(capabilities=[ModelCapability.chat, ModelCapability.tool_calling]))

    async def run(policy):
        request = ChatRequest(provider_id='mock', model='mock-1', use_rag=False, allow_agent=True,
                              retry_message_id='previous', metadata={'retry_write_policy': policy},
                              messages=[Message(role='user', content='Create the note in this vault')])
        return [item async for item in chat_retrieval.stream(request, provider)]

    safe_events = asyncio.run(run('full'))
    reused_events = asyncio.run(run('create_only'))
    guarded_events = asyncio.run(run('read_only'))
    assert 'agent.create' in {tool.name for tool in seen[0].tools}
    assert 'agent.create' in {tool.name for tool in seen[1].tools}
    assert 'agent.start' not in {tool.name for tool in seen[1].tools}
    assert 'agent.create' not in {tool.name for tool in seen[2].tools}
    assert 'agent.create 不依赖已保存的智能体配置' in seen[0].system
    assert '先前版本尝试过 agent.create' in seen[1].system
    assert '此轮是只读重试' in seen[2].system
    assert not any(item.event == E.context_status for item in safe_events)
    assert any(item.event == E.context_status and '复用原运行' in item.data['message'] for item in reused_events)
    assert any(item.event == E.context_status and '仅可读取' in item.data['message'] for item in guarded_events)
