"""Core/Host contract doubles; real native ownership is covered by Host and UI QA."""
import asyncio
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from app import host_bridge
from app.agent import experiment_actions as actions
from app.agent.permissions import PermissionMode
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import AgentEventType, AgentRunCreateRequest, ToolCall
from app.errors import ApiError


class ActionHost:
    def __init__(self):
        self.vault = str(uuid4())
        self.entry = str(uuid4())
        self.rows = {}
        self.starts = self.steps = self.cancels = 0
        self.calls = []
        self.prepared = {}
        self.finish_state = 'completed'
        self.hold = False

    def call(self, method, **p):
        assert p.pop('vault_id') == self.vault
        self.calls.append((method, copy.deepcopy(p)))
        action = method.removeprefix('workspace.experiment_agent.')
        if action.startswith('prepare_'):
            key = json.dumps([action, p], sort_keys=True)
            if key in self.prepared:
                return copy.deepcopy(self.rows[self.prepared[key]])
            kind = action.removeprefix('prepare_')
            op = str(uuid4())
            record = {'state': 'awaiting_confirmation', 'approval_id': None, 'confirmed_ms': None}
            if kind == 'run':
                record.update(summary={'request': {'entry': {'file_id': self.entry, 'path': 'experiments/课程 #%.py', 'hash': 'a'*64, 'revision': 1}, 'inputs': [], 'runtime_id': 'bundled-python', 'limits': p['limits']}}, result=None)
            else:
                record.update(plan={'request': {'run_id': p['source_run_id']}}, items=[{'state':'pending','entry':None,'error':None} for _ in p['selections']])
            row = {'kind': 'experiment_'+kind, 'vault_id': self.vault, 'operation_id': op,
                   'fingerprint': hashlib.sha256(op.encode()).hexdigest(), 'context': p['context'], 'record': record}
            self.rows[op] = row
            self.prepared[key] = op
            return copy.deepcopy(row)
        row = self.rows[p['operation_id']]
        assert p['context'] == row['context']
        record = row['record']
        if 'fingerprint' in p:
            assert p['fingerprint'] == row['fingerprint']
        if action == 'start_run':
            assert record['state'] == 'approved'
            self.starts += 1
            record['state'] = 'running'
        elif action == 'run_record' and record['state'] == 'running' and not self.hold:
            record['state'] = self.finish_state
            record['result'] = {'outcome':self.finish_state,'exit_code':0 if self.finish_state=='completed' else 1,
                                'error':None if self.finish_state=='completed' else 'TEST_RUN_FAILED',
                                'logs':{'stdout':{'text':'真实中文输出\r\n','display_truncated':False}}}
        elif action == 'import_next':
            assert record['state'] == 'approved'
            self.steps += 1
            pending = next(item for item in record['items'] if item['state']=='pending')
            pending.update(state='committed',entry={'file_id':str(uuid4()),'path':f'成果/{self.steps}.md'})
            if all(item['state']=='committed' for item in record['items']): record['state']='completed'
        elif action.startswith('cancel_'):
            self.cancels += 1
            record['state'] = 'cancelled'
        return copy.deepcopy(row)

    def native_approve(self, preview):
        record = self.rows[preview['operation_id']]['record']
        record.update(state='approved',approval_id=str(uuid4()),confirmed_ms=1791252000000)


@pytest.fixture
def actor_host(monkeypatch):
    host = ActionHost()
    monkeypatch.setattr(host_bridge,'active',host)
    token=host_bridge.vault_id.set(host.vault)
    yield host
    host_bridge.vault_id.reset(token)


def tool(host, name='experiments.run', **extra):
    args = {'entry_file_id':host.entry} if name=='experiments.run' else {'source_run_id':str(uuid4()),'selections':[{'output_path':'report.md','destination':'成果/report.md'}]}
    return ToolCall(name=name,tool_call_id='provider:id/中文',arguments={**args,**extra})


def context(call, preview, progress=None, authorized=lambda: True):
    return ToolExecutionContext('run_actor',call.tool_call_id,preview,progress,authorized)


def test_no_model_or_registry_approval_can_launch(actor_host):
    async def scenario():
        container=build_container()
        call=tool(actor_host)
        missing=await container.tools.execute(call,ToolExecutionContext('run_actor',call.tool_call_id))
        assert missing.error_code=='EXPERIMENT_REVIEW_REQUIRED'
        preview=await actions.preview_action(call,'run_actor','permission_once')
        with pytest.raises(ApiError) as invalid: await actions.validate_preview(call,preview)
        assert invalid.value.code=='EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
        result=await container.tools.execute(call,context(call,preview))
        assert result.error_code=='EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
        assert actor_host.starts==0
        actor_host.native_approve(preview)
        denied=await container.tools.execute(call,context(call,preview,authorized=None))
        assert denied.error_code=='PERMISSION_DENIED' and actor_host.starts==0
        assert preview['context']['tool_call_id'].startswith('call_')
        assert len(preview['context']['tool_call_id'])==69
    asyncio.run(scenario())


@pytest.mark.parametrize('extra',[{'approved':True},{'command':'python'},{'env':{}},{'runtime_root':'C:/secret'},{'input_file_ids':['bad']},{'limits':{'wall_seconds':1,'cpu_seconds':2}}])
def test_invalid_model_fields_are_rejected_before_host(actor_host,extra):
    async def scenario():
        result=await build_container().tools.execute(tool(actor_host,**extra),ToolExecutionContext('run_actor'))
        assert result.error_code=='TOOL_ARGUMENT_INVALID'
        assert actor_host.calls==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('finish_state',['completed','failed'])
def test_native_approved_execution_reports_real_terminal_state(actor_host,finish_state):
    async def scenario():
        actor_host.finish_state=finish_state
        container=build_container(); call=tool(actor_host)
        preview=await actions.preview_action(call,'run_actor','permission_once')
        actor_host.native_approve(preview)
        events=[]
        async def progress(data): events.append(data)
        result=await container.tools.execute(call,context(call,preview,progress))
        assert result.success==(finish_state=='completed'),result
        assert result.output['record']['state']==finish_state
        assert [event['state'] for event in events]==['running',finish_state]
        if finish_state=='failed': assert result.error_code=='TEST_RUN_FAILED'
        assert actor_host.starts==1
    asyncio.run(scenario())


def test_review_binding_cannot_move_to_another_task_call_vault_or_fingerprint(actor_host):
    async def scenario():
        container=build_container(); call=tool(actor_host)
        preview=await actions.preview_action(call,'run_actor','permission_once'); actor_host.native_approve(preview)
        denied=await container.tools.execute(call,ToolExecutionContext('run_other',call.tool_call_id,preview,is_authorized=lambda:True))
        assert denied.error_code=='EXPERIMENT_REVIEW_STALE'
        other=call.model_copy(update={'tool_call_id':'other'})
        denied=await container.tools.execute(other,context(other,preview))
        assert denied.error_code=='EXPERIMENT_REVIEW_STALE'
        token=host_bridge.vault_id.set(str(uuid4()))
        try:
            with pytest.raises(ApiError): await actions.validate_preview(call,preview)
        finally: host_bridge.vault_id.reset(token)
        actor_host.rows[preview['operation_id']]['fingerprint']='b'*64
        with pytest.raises(ApiError): await actions.validate_preview(call,preview)
        assert actor_host.starts==0
    asyncio.run(scenario())


def test_revocation_stops_only_the_bound_run(actor_host):
    async def scenario():
        actor_host.hold=True
        container=build_container(); call=tool(actor_host)
        preview=await actions.preview_action(call,'run_actor','permission_once'); actor_host.native_approve(preview)
        permitted=True
        async def progress(data):
            nonlocal permitted
            if data['state']=='running': permitted=False
        result=await container.tools.execute(call,context(call,preview,progress,lambda:permitted))
        assert result.error_code=='PERMISSION_DENIED'
        assert actor_host.starts==actor_host.cancels==1
        assert actor_host.rows[preview['operation_id']]['record']['state']=='cancelled'
    asyncio.run(scenario())


def test_task_cancellation_settles_the_owned_host_run(actor_host):
    async def scenario():
        actor_host.hold=True
        container=build_container(); call=tool(actor_host)
        preview=await actions.preview_action(call,'run_actor','permission_once'); actor_host.native_approve(preview)
        running=asyncio.Event()
        async def progress(data):
            if data['state']=='running': running.set()
        task=asyncio.create_task(container.tools.execute(call,context(call,preview,progress)))
        await asyncio.wait_for(running.wait(),2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert actor_host.cancels==1 and actor_host.starts==1
    asyncio.run(scenario())


def test_import_permission_is_separate_and_revocation_preserves_committed_items(actor_host):
    async def scenario():
        container=build_container(); call=tool(actor_host,'experiments.import',selections=[{'output_path':'a.md','destination':'成果/a.md'},{'output_path':'b.md','destination':'成果/b.md'}])
        preview=await actions.preview_action(call,'run_actor','permission_import')
        blocked=await container.tools.execute(call,context(call,preview))
        assert blocked.error_code=='EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
        actor_host.native_approve(preview)
        allowed=True
        async def progress(data):
            nonlocal allowed
            if any(item['state']=='committed' for item in data['items']): allowed=False
        result=await container.tools.execute(call,context(call,preview,progress,lambda:allowed))
        assert result.error_code=='PERMISSION_DENIED' and actor_host.steps==1
        record=actor_host.rows[preview['operation_id']]['record']
        assert record['state']=='cancelled' and record['items'][0]['state']=='committed'
    asyncio.run(scenario())


def test_runtime_requires_native_proof_and_records_real_experiment_events(actor_host):
    async def scenario():
        container=build_container()
        container.permissions.policy.set_rule('experiments.run',PermissionMode.confirm)
        request=AgentRunCreateRequest(input='/tool experiments.run '+json.dumps({'entry_file_id':actor_host.entry}),provider_id='mock',model='mock-1',allowed_tools=['experiments.run'])
        run=await container.agent.create_run(request)
        async with asyncio.timeout(3):
            async for event in container.agent.events(run.run_id):
                if event.event==AgentEventType.permission_required:
                    ticket=event.data['request_id']; break
        preview=await container.agent.permission_preview(run.run_id,ticket)
        with pytest.raises(ApiError) as unapproved:
            await container.agent.resolve_permission(run.run_id,ticket,'allow_once',preview['token'])
        assert unapproved.value.code=='EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
        assert actor_host.starts==0
        actor_host.native_approve(preview)
        assert await container.agent.resolve_permission(run.run_id,ticket,'allow_once',preview['token'])
        finished=await container.agent.wait(run.run_id)
        assert finished.tool_results[0].success,finished.tool_results
        trace=container.agent.get_trace(run.run_id,after_sequence=-1,limit=100)
        states=[e.data['state'] for e in trace.items if e.event==AgentEventType.experiment_state]
        assert states==['running','completed']
        assert actor_host.starts==1
    asyncio.run(scenario())


def test_review_retry_after_native_reply_loss_keeps_the_same_once_only_consent(actor_host):
    async def scenario():
        call = tool(actor_host)
        original = await actions.preview_action(call, 'run_actor', 'permission_once')
        actor_host.native_approve(original)
        retry = await actions.preview_action(call, 'run_actor', 'permission_once')
        assert retry['operation_id'] == original['operation_id'] and retry['token'] == original['token']
        assert retry['record']['state'] == 'approved'
        result = await build_container().tools.execute(call, context(call, retry))
        assert result.success and actor_host.starts == 1
        with pytest.raises(ApiError) as finished:
            await actions.preview_action(call, 'run_actor', 'permission_once')
        assert finished.value.code == 'EXPERIMENT_REQUEST_FINISHED'
        assert actor_host.starts == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('name,consent', [('experiments.run', True), ('experiments.run', '00000000-0000-0000-0000-000000000000'), ('experiments.import', True), ('experiments.import', 0)])
def test_malformed_native_consent_is_not_a_grant(actor_host, name, consent):
    async def scenario():
        call = tool(actor_host, name)
        preview = await actions.preview_action(call, 'run_actor', 'permission_once')
        actor_host.native_approve(preview)
        record = actor_host.rows[preview['operation_id']]['record']
        record['approval_id' if name == 'experiments.run' else 'confirmed_ms'] = consent
        result = await build_container().tools.execute(call, context(call, preview))
        assert not result.success and result.error_code == 'EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
        assert actor_host.starts == actor_host.steps == 0
    asyncio.run(scenario())


def test_default_denial_reaches_no_host_and_import_remains_independent(actor_host):
    async def scenario():
        container = build_container()
        for name in ['experiments.run', 'experiments.import']:
            call = tool(actor_host, name)
            run = await container.agent.create_run(AgentRunCreateRequest(input='/tool '+name+' '+json.dumps(call.arguments),
                provider_id='mock', model='mock-1', allowed_tools=[name]))
            finished = await container.agent.wait(run.run_id)
            assert finished.tool_results[0].error_code == 'PERMISSION_DENIED'
        assert actor_host.calls == []
        await container.agent.shutdown()
    asyncio.run(scenario())


def test_collaboration_cannot_widen_experiment_tools_or_inherit_run_consent(actor_host):
    from app.agent.management import DefinitionConfig, CollaborationPlan, create_definition
    from app.agent.collaboration import coordinator

    async def scenario():
        container = build_container()
        runtime = container.agent
        manager = coordinator(runtime)
        try:
            actor = create_definition(DefinitionConfig(name='Runner', provider_id='mock', model='mock-1', tools=['experiments.run']), runtime)
            plan = CollaborationPlan(title='Once-only permission', members=[{'member_id':'runner', 'agent_id':actor['id'],
                'input':'/tool experiments.run '+json.dumps({'entry_file_id':actor_host.entry})}])
            with pytest.raises(ApiError) as widened:
                manager.plan(plan, tools_ceiling=['experiments.files.read'])
            assert widened.value.code == 'AGENT_TOOL_SCOPE' and actor_host.calls == []
            group = manager.plan(plan, tools_ceiling=['experiments.run'])
            await manager.approve(group['id'], group['revision'])
            await asyncio.wait_for(manager.tasks[group['id']], 5)
            member = manager.get(group['id'])['members'][0]
            assert runtime.get_run(member['run_id']).tool_results[0].error_code == 'PERMISSION_DENIED'
            assert actor_host.calls == []
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('exit_code,error,outcome', [(1, None, 'completed'), (False, None, 'completed'), (0, 'FAILED', 'completed'), (0, None, 'limited')])
def test_completed_state_requires_actual_success_evidence(actor_host, exit_code, error, outcome):
    async def scenario():
        call = tool(actor_host)
        preview = await actions.preview_action(call, 'run_actor', 'permission_once')
        actor_host.native_approve(preview)
        actor_host.rows[preview['operation_id']]['record'].update(state='completed', result={'outcome':outcome, 'exit_code':exit_code, 'error':error})
        result = await build_container().tools.execute(call, context(call, preview))
        assert not result.success and result.error_code == 'EXPERIMENT_REVIEW_STALE'
        assert actor_host.starts == 0
    asyncio.run(scenario())


def test_one_collaboration_member_native_consent_cannot_approve_another_member(actor_host):
    from app.agent.management import DefinitionConfig, CollaborationPlan, create_definition
    from app.agent.collaboration import coordinator

    async def scenario():
        container = build_container()
        runtime = container.agent
        manager = coordinator(runtime)
        container.permissions.policy.set_rule('experiments.run', PermissionMode.confirm)
        group = None
        try:
            actor = create_definition(DefinitionConfig(name='Runner', provider_id='mock', model='mock-1', tools=['experiments.run']), runtime)
            command = '/tool experiments.run '+json.dumps({'entry_file_id':actor_host.entry})
            group = manager.plan(CollaborationPlan(title='Independent native consent', members=[
                {'member_id':'one', 'agent_id':actor['id'], 'input':command},
                {'member_id':'two', 'agent_id':actor['id'], 'input':command}], max_concurrency=2), tools_ceiling=['experiments.run'])
            await manager.approve(group['id'], group['revision'])
            async with asyncio.timeout(5):
                while not all(member['run_id'] for member in manager.get(group['id'])['members']):
                    await asyncio.sleep(.01)
            members = manager.get(group['id'])['members']
            tickets = []
            for member in members:
                async with asyncio.timeout(5):
                    async for event in runtime.events(member['run_id']):
                        if event.event == AgentEventType.permission_required:
                            tickets.append(event.data['request_id']); break
            first = await runtime.permission_preview(members[0]['run_id'], tickets[0])
            second = await runtime.permission_preview(members[1]['run_id'], tickets[1])
            assert first['operation_id'] != second['operation_id']
            actor_host.native_approve(first)
            assert await runtime.resolve_permission(members[0]['run_id'], tickets[0], 'allow_once', first['token'])
            await runtime.wait(members[0]['run_id'])
            with pytest.raises(ApiError) as other_token:
                await runtime.resolve_permission(members[1]['run_id'], tickets[1], 'allow_once', first['token'])
            assert other_token.value.code == 'EXPERIMENT_PREVIEW_REQUIRED'
            with pytest.raises(ApiError) as other_consent:
                await runtime.resolve_permission(members[1]['run_id'], tickets[1], 'allow_once', second['token'])
            assert other_consent.value.code == 'EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED'
            assert actor_host.starts == 1
            assert actor_host.rows[second['operation_id']]['record']['state'] == 'awaiting_confirmation'
        finally:
            if group is not None: await manager.cancel(group['id'])
            await runtime.shutdown()
    asyncio.run(scenario())
