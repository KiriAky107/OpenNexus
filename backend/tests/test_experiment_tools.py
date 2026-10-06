import asyncio
import base64
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from app import host_bridge
from app.agent import experiment_tools as tools
from app.agent.permissions import PermissionManager, PermissionMode, PermissionPolicy
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import AgentEventType, AgentRunCreateRequest, ToolCall
from app.errors import ApiError


def call(name='experiments.files.write', **arguments):
    return ToolCall(name=name, tool_call_id='tool_' + uuid4().hex, arguments=arguments)


class SourceHost:
    """Transport double. Physical identity/path protection is covered by Host tests."""
    def __init__(self, root, vault):
        self.root, self.vault = root, vault
        self.files, self.calls = {}, []
        self.writes = 0

    def put(self, path, content):
        previous = self.files.get(path)
        self.files[path] = {'file_id': previous['file_id'] if previous else str(uuid4()),
            'path': path, 'hash': hashlib.sha256(content.encode()).hexdigest(),
            'revision': previous['revision'] + 1 if previous else 1, 'deleted': False, 'is_folder': False}
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.encode())

    def snapshot(self, path):
        if path not in self.files:
            return None
        data = (self.root / path).read_bytes()
        return {'entry': copy.deepcopy(self.files[path]), 'content_base64': base64.b64encode(data).decode(),
                'byte_size': len(data)}

    def call(self, method, **params):
        if params.pop('vault_id') != self.vault:
            raise RuntimeError('VAULT_PERMISSION_CHANGED')
        self.calls.append((method, params))
        action = method.removeprefix('workspace.experiments.')
        if action == 'snapshot':
            return self.snapshot(params['path'])
        if action == 'read':
            path = next(p for p,e in self.files.items() if e['file_id']==params['file_id'])
            return self.snapshot(path)
        if action == 'list':
            items = sorted(self.files.values(), key=lambda e:e['path'])
            return {'items':copy.deepcopy(items[params['offset']:params['offset']+params['limit']]),'total':len(items)}
        if action == 'write':
            before = self.files.get(params['path'])
            assert (before['hash'] if before else '') == params['expected']
            assert (before['file_id'] if before else None) == params['expected_file_id']
            assert (before['revision'] if before else None) == params['expected_revision']
            self.put(params['path'], base64.b64decode(params['content_base64']).decode('utf-8'))
            self.writes += 1
            return {'operation_id':params['operation_id'],'state':'committed','result':copy.deepcopy(self.files[params['path']])}
        raise AssertionError('Unexpected execution/import/fallback method: ' + method)


@pytest.fixture
def source_host(tmp_path, monkeypatch):
    vault = str(uuid4())
    host = SourceHost(tmp_path / 'vault', vault)
    host.put('experiments/课程 #%.py', "print('中文😀')\r\n")
    monkeypatch.setattr(host_bridge, 'active', host)
    token = host_bridge.vault_id.set(vault)
    yield host
    host_bridge.vault_id.reset(token)


def test_independent_defaults_and_no_session_execution_authority():
    async def scenario():
        policy = PermissionPolicy()
        assert policy.mode_for('notes.read') == PermissionMode.allow
        assert policy.mode_for('experiments.files.read') == PermissionMode.confirm
        assert policy.mode_for('experiments.run') == PermissionMode.deny
        assert policy.mode_for('experiments.import') == PermissionMode.deny
        manager = PermissionManager(policy)
        for permission in ['experiments.files.write', 'experiments.run', 'experiments.import']:
            policy.set_rule(permission, PermissionMode.allow)
            assert manager.mode_for(permission, 'unlimited-run') == PermissionMode.confirm
            ticket = manager.create_ticket('unlimited-run', permission)
            assert not manager.resolve('unlimited-run', ticket.request_id, 'allow_session')
            assert not ticket.future.done()
            assert manager.resolve('unlimited-run', ticket.request_id, 'allow_once')
            assert await manager.wait(ticket) == 'allow_once'
            assert not manager.resolve('unlimited-run', ticket.request_id, 'allow_once')
            assert manager.mode_for(permission, 'unlimited-run') == PermissionMode.confirm
        ticket = manager.create_ticket('reader', 'experiments.files.read')
        assert manager.resolve('reader', ticket.request_id, 'allow_session')
        assert await manager.wait(ticket) == 'allow_session'
        assert manager.mode_for('experiments.files.read', 'reader') == PermissionMode.allow
        assert manager.mode_for('experiments.files.read', 'child') == PermissionMode.confirm
        assert manager.mode_for('experiments.files.write', 'reader') == PermissionMode.confirm
    asyncio.run(scenario())


def test_source_reads_are_bounded_and_keep_full_content_hash(source_host):
    async def scenario():
        container = build_container()
        entry = source_host.files['experiments/课程 #%.py']
        result = await container.tools.execute(call('experiments.files.read', file_id=entry['file_id'],
            offset=7, max_chars=4), ToolExecutionContext('reader'))
        assert result.success
        assert result.output['content'] == '中文😀\''
        assert result.output['hash'] == entry['hash']
        assert result.output['next_offset'] == 11
        assert result.output['truncated']
        assert source_host.writes == 0
    asyncio.run(scenario())


def test_preview_and_write_preserve_raw_utf8_and_line_endings(source_host):
    async def scenario():
        container = build_container()
        path = 'experiments/课程 #%.py'
        proposed = '# 中文😀\r\nprint(2)\r\n'
        tool = call(path=path, content=proposed, expected_hash=source_host.files[path]['hash'])
        before = (source_host.root/path).read_bytes()
        preview = await tools.preview_write(tool)
        assert preview['overwrite'] and preview['before_bytes']==len(before)
        assert preview['after_bytes']==len(proposed.encode())
        assert (source_host.root/path).read_bytes() == before
        result = await container.tools.execute(tool, ToolExecutionContext('run', tool.tool_call_id, preview))
        assert result.success, result
        assert (source_host.root/path).read_bytes()==proposed.encode()
        assert result.output['result']['revision']==2
        assert source_host.writes==1
    asyncio.run(scenario())


def test_creation_preview_and_missing_review_do_not_write(source_host):
    async def scenario():
        container = build_container()
        tool = call(path='experiments/input.json', content='{"中文":3}\r\n')
        result = await container.tools.execute(tool, ToolExecutionContext('run'))
        assert not result.success and result.error_code=='EXPERIMENT_PREVIEW_REQUIRED'
        preview = await tools.preview_write(tool)
        assert preview['operation']=='create' and not preview['overwrite']
        assert not (source_host.root/'experiments/input.json').exists()
        result = await container.tools.execute(tool, ToolExecutionContext('run', tool.tool_call_id, preview))
        assert result.success, result
        assert (source_host.root/'experiments/input.json').read_bytes() == '{"中文":3}\r\n'.encode()
    asyncio.run(scenario())


def test_stale_revision_and_changed_proposal_do_not_overwrite(source_host):
    async def scenario():
        path='experiments/课程 #%.py'
        container=build_container()
        tool=call(path=path, content='print(2)', expected_hash=source_host.files[path]['hash'])
        preview=await tools.preview_write(tool)
        original=(source_host.root/path).read_bytes()
        source_host.put(path, original.decode())  # Same bytes, later revision.
        result=await container.tools.execute(tool, ToolExecutionContext('run', tool.tool_call_id, preview))
        assert result.error_code=='EXPERIMENT_PREVIEW_STALE'
        preview=await tools.preview_write(tool)
        tool.arguments['content']='print(3)'
        result=await container.tools.execute(tool, ToolExecutionContext('run', tool.tool_call_id, preview))
        assert result.error_code=='EXPERIMENT_PREVIEW_STALE'
        assert (source_host.root/path).read_bytes()==original
        assert source_host.writes==0
    asyncio.run(scenario())


def test_runtime_review_requires_fresh_token_and_consumes_approval_once(source_host):
    async def scenario():
        container=build_container()
        arguments={'path':'experiments/input.csv','content':'项目,数量\r\n中文,3\r\n'}
        run=await container.agent.create_run(AgentRunCreateRequest(
            input='/tool experiments.files.write '+json.dumps(arguments), provider_id='mock', model='mock-1',
            token_budget=None, allowed_tools=['experiments.files.write']))
        async with asyncio.timeout(3):
            async for event in container.agent.events(run.run_id):
                if event.event==AgentEventType.permission_required:
                    request=event.data['request_id']; break
        with pytest.raises(ApiError) as missing:
            await container.agent.resolve_permission(run.run_id,request,'allow_once')
        assert missing.value.code=='EXPERIMENT_PREVIEW_REQUIRED'
        preview=await container.agent.permission_preview(run.run_id,request)
        assert 'binding' not in preview and preview['kind']=='experiment_file'
        with pytest.raises(ApiError):
            await container.agent.resolve_permission(run.run_id,request,'allow_session',preview['token'])
        assert source_host.writes==0
        assert await container.agent.resolve_permission(run.run_id,request,'allow_once',preview['token'])
        assert not await container.agent.resolve_permission(run.run_id,request,'allow_once',preview['token'])
        completed=await container.agent.wait(run.run_id)
        assert completed.tool_results[0].success,completed.tool_results
        assert source_host.writes==1
        assert (source_host.root/'experiments/input.csv').read_bytes()==arguments['content'].encode()
    asyncio.run(scenario())


def test_default_source_read_waits_for_human_and_deny_never_calls_host(source_host):
    async def scenario():
        container=build_container()
        run=await container.agent.create_run(AgentRunCreateRequest(input='/tool experiments.files.list {}',
            provider_id='mock',model='mock-1',allowed_tools=['experiments.files.list']))
        async with asyncio.timeout(3):
            async for event in container.agent.events(run.run_id):
                if event.event==AgentEventType.permission_required:
                    request=event.data['request_id']; break
        assert source_host.calls==[]
        assert await container.agent.resolve_permission(run.run_id,request,'deny')
        completed=await container.agent.wait(run.run_id)
        assert completed.tool_results[0].error_code=='PERMISSION_DENIED'
        assert source_host.calls==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('extra', [{'approved':True},{'command':'python'},{'env':{'TOKEN':'x'}},{'argv':['-c','bad']}])
def test_models_cannot_supply_execution_or_approval_fields(source_host,extra):
    async def scenario():
        container=build_container()
        result=await container.tools.execute(call(path='experiments/x.py',content='print(1)',**extra),ToolExecutionContext('run'))
        assert result.error_code=='TOOL_ARGUMENT_INVALID'
        assert source_host.calls==[]
    asyncio.run(scenario())


def test_utf8_byte_limit_applies_before_preview_reads(source_host):
    async def scenario():
        with pytest.raises(ApiError) as invalid:
            await tools.preview_write(call(path='experiments/x.py',content='中'*(tools.MAX_FILE_BYTES//3+1)))
        assert invalid.value.code=='EXPERIMENT_INPUT_TOO_LARGE'
        assert source_host.calls==[]
    asyncio.run(scenario())


def test_changed_vault_invalidates_review_and_missing_host_has_no_fallback(source_host,monkeypatch):
    async def scenario():
        tool=call(path='experiments/x.py',content='print(1)')
        preview=await tools.preview_write(tool)
        token=host_bridge.vault_id.set(str(uuid4()))
        try:
            # A captured transport refuses the newly substituted Vault.
            with pytest.raises(ApiError) as changed: await tools.validate_preview(tool,preview)
            assert changed.value.code == 'VAULT_PERMISSION_CHANGED'
            # Even an identical file snapshot in a different Vault cannot use
            # the previous Vault's review token.
            original_vault = source_host.vault
            source_host.vault = host_bridge.vault_id.get()
            try:
                with pytest.raises(ApiError) as stale: await tools.validate_preview(tool,preview)
                assert stale.value.code == 'EXPERIMENT_PREVIEW_STALE'
            finally: source_host.vault = original_vault
        finally: host_bridge.vault_id.reset(token)
        monkeypatch.setattr(host_bridge,'active',None)
        with pytest.raises(ApiError) as absent: await tools.preview_write(tool)
        assert absent.value.code=='HOST_UNAVAILABLE'
        assert not (source_host.root/'experiments/x.py').exists()
    asyncio.run(scenario())


def test_corrupt_snapshot_never_becomes_file_content(source_host):
    value=source_host.snapshot('experiments/课程 #%.py')
    value['content_base64']=base64.b64encode(b'wrong').decode()
    with pytest.raises(ApiError) as invalid: tools.decode_snapshot(value)
    assert invalid.value.code=='EXPERIMENT_SNAPSHOT_INVALID'


def test_invalid_unicode_is_rejected_before_host_reads(source_host):
    async def scenario():
        with pytest.raises(ApiError) as invalid:
            await tools.preview_write(call(path='experiments/x.py',content='\ud800'))
        assert invalid.value.code=='TOOL_ARGUMENT_INVALID'
        assert source_host.calls==[]
    asyncio.run(scenario())
