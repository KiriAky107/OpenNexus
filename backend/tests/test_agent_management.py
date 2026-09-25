import asyncio
import pytest
from pydantic import ValidationError
from app import host_bridge
from app.container import build_container
from app.agent.management import DefinitionConfig, DefinitionWrite, CollaborationPlan, StartRequest, create_definition, update_definition, store
from app.agent.collaboration import coordinator
from app.agent.runtime import AgentRunNotFoundError
from app.errors import ApiError
from app.agent.management import ReviewRequest
from app.contracts import AgentRunCreateRequest, ToolDefinition
from app.providers.base import ProviderTurn, ProviderToolCall, ProviderError
from app.agent.permissions import PermissionMode
from pydantic import BaseModel


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


def definition(runtime, name='Reviewer'):
    return create_definition(DefinitionConfig(name=name, provider_id='mock', model='mock-1', tools=['system.echo']), runtime)


def test_manual_defaults_unlimited_but_chat_definitions_are_bounded():
    from app.services.chat_agents import ChatDefinitionConfig
    runtime = build_container().agent
    manual = definition(runtime)
    assert manual['origin'] == 'manual'
    assert manual['config']['token_budget'] is None
    config = ChatDefinitionConfig(name='chat', provider_id='mock', model='mock-1')
    chat = create_definition(config, runtime, origin='chat')
    assert chat['origin'] == 'chat'
    assert chat['config']['token_budget'] == 8000
    with pytest.raises(ValidationError):
        ChatDefinitionConfig(name='chat', provider_id='mock', model='mock-1', token_budget=None)
    changed = update_definition(manual['id'], DefinitionWrite(config={**manual['config'], 'token_budget': 12000}, expected_revision=1), runtime)
    assert changed['origin'] == 'manual'
    assert changed['config']['token_budget'] == 12000


def test_unlimited_manual_agent_is_still_bounded_by_chat_and_group():
    async def scenario():
        runtime = build_container().agent
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            direct = await manager.start(agent['id'], StartRequest(input='hello', expected_revision=1, operation_id='manual'))
            assert direct.token_budget is None
            await runtime.wait(direct.run_id)
            delegated = await manager.start(agent['id'], StartRequest(input='hello', expected_revision=1, operation_id='chat'), conversation_id='test', tools_ceiling=['system.echo'])
            assert delegated.token_budget == 8000
            await runtime.wait(delegated.run_id)
            group = manager.plan(CollaborationPlan(title='bounded', token_budget=1, members=[{'member_id':'a','agent_id':agent['id'],'input':'/tool system.echo {"text":"once"}'}]))
            await manager.approve(group['id'], 1)
            await until(lambda: manager.get(group['id'])['status'] == 'waiting_budget')
            paused = manager.get(group['id'])
            await manager.extend_budget(group['id'], paused['budget_request']['request_id'], 1000)
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            assert manager.get(group['id'])['status'] == 'completed'
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_versioned_definition_and_idempotent_start():
    async def scenario():
        runtime = build_container().agent
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            request = StartRequest(input='hello', expected_revision=1, operation_id='once')
            first = await manager.start(agent['id'], request)
            duplicate = await manager.start(agent['id'], request)
            assert first.run_id == duplicate.run_id
            updated = update_definition(agent['id'], DefinitionWrite(config={**agent['config'], 'name': 'New name'}, expected_revision=1), runtime)
            assert updated['revision'] == 2
            assert runtime.get_run(first.run_id).definition_snapshot['config']['name'] == 'Reviewer'
            with pytest.raises(ApiError):
                update_definition(agent['id'], DefinitionWrite(config=agent['config'], expected_revision=1), runtime)
            assert (await runtime.wait(first.run_id)).status.value == 'completed'
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('members', [
    [{'member_id': 'a', 'agent_id': 'x', 'input': 'a', 'depends_on': ['a']}],
    [{'member_id': 'a', 'agent_id': 'x', 'input': 'a', 'depends_on': ['missing']}],
    [{'member_id': 'a', 'agent_id': 'x', 'input': 'a'}] * 2,
])
def test_invalid_dependency_graph(members):
    with pytest.raises(ValidationError):
        CollaborationPlan(title='test', members=members)


def test_collaboration_dependencies_and_budget():
    async def scenario():
        runtime = build_container().agent
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            group = manager.plan(CollaborationPlan(title='group', token_budget=1, members=[
                {'member_id': 'a', 'agent_id': agent['id'], 'input': '/tool system.echo {"text":"one"}'},
                {'member_id': 'b', 'agent_id': agent['id'], 'input': 'summarize', 'depends_on': ['a']},
            ]))
            assert not runtime.list_runs(100, 0)[0]
            await manager.approve(group['id'], 1)
            async with asyncio.timeout(5):
                while manager.get(group['id'])['status'] != 'waiting_budget':
                    await asyncio.sleep(.02)
                paused = manager.get(group['id'])
                request_id = paused['budget_request']['request_id']
                assert paused['members'][1]['run_id'] is None
                await manager.extend_budget(group['id'], request_id, 1000)
                await manager.extend_budget(group['id'], request_id, 1000)
                await manager.tasks[group['id']]
            completed = manager.get(group['id'])
            assert completed['status'] == 'completed', completed
            assert len(completed['summary']) == 2
            assert completed['tool_calls'] == 1
            assert completed['plan']['token_budget'] == paused['token_usage'] + 1000
            second = runtime.get_run(completed['members'][1]['run_id'])
            assert '上游执行结果' in second.input
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_scope_is_checked_for_runs_definitions_and_collaborations():
    async def scenario():
        runtime = build_container().agent
        token = host_bridge.vault_id.set('vault-a')
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            run = await manager.start(agent['id'], StartRequest(input='hello', operation_id='one', expected_revision=1))
            await runtime.wait(run.run_id)
            group = manager.plan(CollaborationPlan(title='group', members=[{'member_id': 'a', 'agent_id': agent['id'], 'input': 'hello'}]))
            host_bridge.vault_id.set('vault-b')
            assert not store.list('definition')
            assert runtime.list_runs(100, 0) == ([], 0)
            with pytest.raises(ApiError):
                manager.get(group['id'])
            with pytest.raises(AgentRunNotFoundError):
                await runtime.cancel(run.run_id)
            with pytest.raises(AgentRunNotFoundError):
                runtime.get_trace(run.run_id, after_sequence=-1, limit=100)
        finally:
            host_bridge.vault_id.reset(token)
            await runtime.shutdown()
    asyncio.run(scenario())


def test_parallel_failure_blocks_only_dependents(monkeypatch):
    async def scenario():
        runtime = build_container().agent
        active, maximum = 0, 0
        both_started = asyncio.Event()
        async def complete(request):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            try:
                if active == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 3)
                if request.messages[0].content == 'fail':
                    raise ProviderError('MODEL_FAILED', 'test failure')
                return ProviderTurn(text='real result', input_tokens=1)
            finally:
                active -= 1
        monkeypatch.setattr(runtime.providers.get('mock').adapter, 'complete', complete)
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            group = manager.plan(CollaborationPlan(title='parallel', members=[
                {'member_id': 'a', 'agent_id': agent['id'], 'input': 'fail'},
                {'member_id': 'b', 'agent_id': agent['id'], 'input': 'independent'},
                {'member_id': 'c', 'agent_id': agent['id'], 'input': 'blocked', 'depends_on': ['a']},
            ]))
            await manager.approve(group['id'], 1)
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            result = manager.get(group['id'])
            assert maximum == 2
            assert result['status'] == 'partial_failure'
            assert [member['status'] for member in result['members']] == ['failed', 'completed', 'blocked']
            assert result['members'][2]['run_id'] is None
            assert (await manager.cancel(group['id']))['status'] == 'partial_failure'
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_member_cancel_and_whole_cancel_stop_scheduling(monkeypatch):
    async def scenario():
        runtime = build_container().agent
        gate = asyncio.Event()
        async def complete(request):
            await gate.wait()
            return ProviderTurn(text='done', input_tokens=1)
        monkeypatch.setattr(runtime.providers.get('mock').adapter, 'complete', complete)
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            plan = CollaborationPlan(title='cancel', max_concurrency=1, members=[
                {'member_id': 'a', 'agent_id': agent['id'], 'input': 'wait'},
                {'member_id': 'b', 'agent_id': agent['id'], 'input': 'next', 'depends_on': ['a']},
            ])
            group = manager.plan(plan)
            await manager.approve(group['id'], 1)
            await until(lambda: manager.get(group['id'])['members'][0]['run_id'])
            await manager.cancel(group['id'], 'a')
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            result = manager.get(group['id'])
            assert [m['status'] for m in result['members']] == ['cancelled', 'blocked']
            second = manager.plan(plan)
            await manager.approve(second['id'], 1)
            await until(lambda: manager.get(second['id'])['members'][0]['run_id'])
            result = await manager.cancel(second['id'])
            assert result['status'] == 'cancelled'
            assert result['members'][1]['run_id'] is None
            assert runtime.get_run(result['members'][0]['run_id']).status.value == 'cancelled'
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_review_is_atomic_idempotent_and_revision_guarded():
    runtime = build_container().agent
    agent = definition(runtime)
    def proposal():
        return store.insert('change', {'agent_id': agent['id'], 'expected_revision': 1, 'before': agent['config'],
            'config': {**agent['config'], 'name': 'Changed'}, 'status': 'pending', 'action': 'update'})
    change, stale = proposal(), proposal()
    approved = store.review_change(change['id'], ReviewRequest(expected_revision=1, decision='approve'), runtime)
    assert approved['status'] == 'approved'
    assert store.review_change(change['id'], ReviewRequest(expected_revision=1, decision='approve'), runtime) == approved
    assert store.get('definition', agent['id'])['revision'] == 2
    with pytest.raises(ApiError):
        store.review_change(stale['id'], ReviewRequest(expected_revision=1, decision='approve'), runtime)
    assert store.get('change', stale['id'])['status'] == 'pending'


def test_restart_never_relaunches_collaboration_members():
    runtime = build_container().agent
    agent = definition(runtime)
    manager = coordinator(runtime)
    group = manager.plan(CollaborationPlan(title='lost process', members=[{'member_id': 'a', 'agent_id': agent['id'], 'input': 'work'}]))
    store.save('collaboration', {**group, 'status': 'running'}, 1)
    result = manager.get(group['id'])
    assert result['status'] == 'interrupted'
    assert result['members'][0]['status'] == 'blocked'
    assert not runtime.list_runs(20, 0)[0]


def test_temporary_launch_is_idempotent():
    async def scenario():
        runtime = build_container().agent
        try:
            manager = coordinator(runtime)
            request = AgentRunCreateRequest(input='hello', provider_id='mock', model='mock-1')
            a, b = await asyncio.gather(manager.start_temporary(request, 'once'), manager.start_temporary(request, 'once'))
            assert a.run_id == b.run_id
            await runtime.wait(a.run_id)
            assert runtime.list_runs(20, 0)[1] == 1
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_session_permission_is_run_scoped_and_revocation_wins():
    async def scenario():
        runtime = build_container().agent
        manager = runtime.permissions
        ticket = manager.create_ticket('run_a', 'notes.write')
        assert manager.resolve('run_a', ticket.request_id, 'allow_session')
        assert await manager.wait(ticket, 1) == 'allow_session'
        assert manager.mode_for('notes.write', 'run_a') == PermissionMode.allow
        assert manager.mode_for('notes.write', 'run_b') == PermissionMode.confirm
        manager.policy.set_rule('notes.write', PermissionMode.deny)
        assert manager.mode_for('notes.write', 'run_a') == PermissionMode.deny
        await runtime.shutdown()
    asyncio.run(scenario())


def test_writes_are_serialized_across_parallel_members(monkeypatch):
    async def scenario():
        runtime = build_container().agent
        active, maximum = 0, 0
        class Arguments(BaseModel):
            text: str
        async def write(arguments, context):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(.04)
            active -= 1
            return {'text': arguments.text}
        runtime.tools.register(ToolDefinition(name='test.write', description='test', parameters=Arguments.model_json_schema()), Arguments, write)
        try:
            agent = create_definition(DefinitionConfig(name='writer', provider_id='mock', model='mock-1', tools=['test.write']), runtime)
            manager = coordinator(runtime)
            group = manager.plan(CollaborationPlan(title='writes', members=[{'member_id': key, 'agent_id': agent['id'], 'input': '/tool test.write {"text":"once"}'} for key in ['a', 'b']]))
            await manager.approve(group['id'], 1)
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            assert manager.get(group['id'])['status'] == 'completed'
            assert maximum == 1
            assert sum(len(runtime.get_run(m['run_id']).tool_results) for m in manager.get(group['id'])['members']) == 2
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_recursive_tools_and_stale_plan_are_rejected():
    with pytest.raises(ValidationError):
        DefinitionConfig(name='unsafe', provider_id='mock', model='mock-1', tools=['agent.create'])
    async def scenario():
        runtime = build_container().agent
        try:
            agent = definition(runtime)
            manager = coordinator(runtime)
            group = manager.plan(CollaborationPlan(title='review', members=[{'member_id': 'a', 'agent_id': agent['id'], 'input': 'work'}]))
            update_definition(agent['id'], DefinitionWrite(config={**agent['config'], 'enabled': False}, expected_revision=1), runtime)
            with pytest.raises(ApiError):
                await manager.approve(group['id'], 1)
            assert not runtime.list_runs(20, 0)[0]
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_individual_member_limit_uses_one_shared_confirmation():
    async def scenario():
        runtime = build_container().agent
        try:
            agent = create_definition(DefinitionConfig(name='limited', provider_id='mock', model='mock-1', tools=['system.echo'], token_budget=1), runtime)
            manager = coordinator(runtime)
            group = manager.plan(CollaborationPlan(title='member budget', token_budget=10000, members=[
                {'member_id': 'a', 'agent_id': agent['id'], 'input': '/tool system.echo {"text":"once"}'}]))
            await manager.approve(group['id'], 1)
            await until(lambda: manager.get(group['id'])['status'] == 'waiting_budget')
            paused = manager.get(group['id'])
            assert paused['budget_request']['member_name'] == 'limited'
            assert paused['token_usage'] < paused['plan']['token_budget']
            await manager.extend_budget(group['id'], paused['budget_request']['request_id'], 1000)
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            assert manager.get(group['id'])['status'] == 'completed'
            assert manager.get(group['id'])['tool_calls'] == 1
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())
