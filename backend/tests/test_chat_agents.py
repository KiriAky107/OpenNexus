import asyncio
from types import SimpleNamespace
import pytest
from app.contracts import ChatRequest, ToolCall, ModelCapability, Message, ModelEventType as E
from app.services import chat_agents, chat_retrieval


def test_delegation_uses_existing_runtime_limits_and_no_network(monkeypatch):
    from app.container import container
    requests = []
    async def create(request):
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
