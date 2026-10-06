"""Agent proposals and recorded native consent; all effects stay in the Host."""
from __future__ import annotations

import asyncio
import hashlib
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from app import host_bridge
from app.agent.experiment_tools import Arguments
from app.agent.tools import ToolExecutionContext, ToolExecutionError, ToolRegistry
from app.contracts import ToolCall, ToolDefinition
from app.errors import ApiError
from app.services.desktop_notes import call as host_call
from app.services.note_preview import digest

ACTION_TOOLS = frozenset({'experiments.run', 'experiments.import'})
ID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
ACTIVE_RUN = {'starting', 'running', 'cancel_requested'}


class Limits(Arguments):
    wall_seconds: int = Field(default=60, ge=1, le=120)
    cpu_seconds: int = Field(default=30, ge=1, le=60)
    memory_mib: int = Field(default=256, ge=64, le=512)
    processes: int = Field(default=4, ge=1, le=8)
    disk_mib: int = Field(default=64, ge=8, le=128)
    output_mib: int = Field(default=16, ge=1, le=128)
    log_kib: int = Field(default=256, ge=16, le=1024)
    objects: int = Field(default=1024, ge=32, le=2048)

    @model_validator(mode='after')
    def consistent(self):
        if self.cpu_seconds > self.wall_seconds or self.output_mib > self.disk_mib:
            raise ValueError('CPU must fit wall time and outputs must fit the disk budget.')
        return self


class RunArguments(Arguments):
    entry_file_id: str = Field(pattern=ID)
    input_file_ids: list[str] = Field(default_factory=list, max_length=31)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode='after')
    def inputs(self):
        if (self.entry_file_id in self.input_file_ids or len(set(self.input_file_ids)) != len(self.input_file_ids)
                or any(str(UUID(value)) != value for value in self.input_file_ids)):
            raise ValueError('Select each stable input ID once.')
        self.input_file_ids.sort()
        return self


class Selection(Arguments):
    output_path: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)


class ImportArguments(Arguments):
    source_run_id: str = Field(pattern=ID)
    selections: list[Selection] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def ordered(self):
        self.selections.sort(key=lambda item: item.destination)
        return self


def arguments(call: ToolCall) -> dict:
    try:
        model = RunArguments if call.name == 'experiments.run' else ImportArguments
        return model.model_validate(call.arguments).model_dump()
    except (ValidationError, ValueError):
        raise ApiError(422, 'TOOL_ARGUMENT_INVALID', '实验操作参数无效，请提出新的结构化请求。') from None


def _method(name: str) -> str:
    return 'experiment_agent.' + name


async def _call(name: str, **params):
    return await asyncio.to_thread(host_call, _method(name), **params)


def _kind(call: ToolCall) -> str:
    return 'run' if call.name == 'experiments.run' else 'import'


def _checked(value: dict, binding: dict) -> dict:
    try:
        if (value['vault_id'] != binding['vault_id'] or value['context'] != binding['context']
                or value['kind'] != 'experiment_' + binding['action']
                or str(UUID(value['operation_id'])) != value['operation_id']
                or len(value['fingerprint']) != 64
                or any(c not in '0123456789abcdef' for c in value['fingerprint'])
                or ('operation_id' in binding and value['operation_id'] != binding['operation_id'])
                or ('fingerprint' in binding and value['fingerprint'] != binding['fingerprint'])
                or not isinstance(value['record'], dict)):
            raise ValueError('review binding differs')
        record = value['record']
        if binding['action'] == 'run' and record.get('state') == 'completed':
            result = record.get('result')
            if (not isinstance(result, dict) or result.get('outcome') != 'completed'
                    or type(result.get('exit_code')) is not int or result['exit_code'] != 0
                    or result.get('error') is not None):
                raise ValueError('completed run has no successful result')
        if binding['action'] == 'import' and record.get('state') == 'completed':
            if not record.get('items') or any(item.get('state') != 'committed' for item in record['items']):
                raise ValueError('completed import has pending items')
    except (KeyError, TypeError, ValueError):
        raise ApiError(409, 'EXPERIMENT_REVIEW_STALE', '实验审核已变化，请重新提出请求。') from None
    return value


async def preview_action(call: ToolCall, run_id: str, request_id: str) -> dict:
    args = arguments(call)
    # Provider tool IDs can contain arbitrary characters. Bind their exact bytes
    # with a digest rather than placing model-controlled text in a native dialog.
    tool_id = 'call_' + hashlib.sha256(call.tool_call_id.encode()).hexdigest()
    binding = {'vault_id': host_bridge.vault_id.get(), 'tool': call.name, 'arguments': args,
               'original_tool_call_id': call.tool_call_id, 'action': _kind(call),
               'context': {'agent_run_id': run_id, 'tool_call_id': tool_id, 'request_id': request_id}}
    value = _checked(await _call('prepare_' + binding['action'], context=binding['context'], **args), binding)
    if value['record']['state'] not in {'awaiting_confirmation', 'approved'}:
        raise ApiError(409, 'EXPERIMENT_REQUEST_FINISHED', '该请求已有处理记录，请核对记录后提出新的操作。',
                       {'operation_id': value['operation_id'], 'state': value['record']['state']})
    binding.update(operation_id=value['operation_id'], fingerprint=value['fingerprint'])
    return {**value, 'token': digest(binding), 'binding': binding}


def _binding(call: ToolCall, preview: dict, context: ToolExecutionContext | None = None) -> dict:
    b = preview.get('binding', {})
    if (b.get('tool') != call.name or b.get('arguments') != arguments(call)
            or b.get('original_tool_call_id') != call.tool_call_id
            or b.get('vault_id') != host_bridge.vault_id.get()
            or preview.get('token') != digest(b)
            or (context and (b.get('context', {}).get('agent_run_id') != context.run_id
                             or context.tool_call_id != call.tool_call_id))):
        raise ApiError(409, 'EXPERIMENT_REVIEW_STALE', '实验请求或知识库已变化，请重新审核。')
    return b


async def validate_preview(call: ToolCall, preview: dict) -> dict:
    b = _binding(call, preview)
    value = _checked(await _call(b['action'] + '_record', context=b['context'], operation_id=b['operation_id']), b)
    record = value['record']
    # These fields are minted by Host native actions, never by request JSON.
    consent = record.get('approval_id') if b['action'] == 'run' else record.get('confirmed_ms')
    if b['action'] == 'run':
        try:
            has_consent = isinstance(consent, str) and str(UUID(consent)) == consent and UUID(consent).int != 0
        except ValueError:
            has_consent = False
    else:
        has_consent = type(consent) is int and consent > 0
    if not has_consent or record['state'] == 'awaiting_confirmation':
        raise ApiError(409, 'EXPERIMENT_NATIVE_CONFIRMATION_REQUIRED', '请在桌面原生确认框中确认本次操作。')
    if record['state'] in {'rejected', 'cancelled', 'interrupted', 'forgotten'}:
        raise ApiError(409, 'EXPERIMENT_REQUEST_FINISHED', '实验请求已停止，请核对记录。')
    return value


async def cancel_preview(preview: dict) -> None:
    b = preview.get('binding', {})
    if b.get('action') not in {'run', 'import'} or b.get('vault_id') != host_bridge.vault_id.get():
        return
    await _call('cancel_' + b['action'], context=b['context'], operation_id=b['operation_id'])


def _authorized(context: ToolExecutionContext) -> None:
    if not context.is_authorized or not context.is_authorized():
        raise ApiError(403, 'PERMISSION_DENIED', '当前任务未获准执行此实验操作。')


async def _progress(context: ToolExecutionContext, value: dict) -> None:
    if context.progress:
        record = value['record']
        data = {'kind': value['kind'], 'vault_id': value['vault_id'], 'operation_id': value['operation_id'],
                'state': record['state'], 'fingerprint': value['fingerprint']}
        if value['kind'] == 'experiment_run':
            data.update(entry=record['summary']['request']['entry'], result=record.get('result'))
        else:
            plan = record['plan']
            data.update(items=record['items'], source_run_id=plan['request']['run_id'],
                        entry=plan.get('source', {}).get('request', {}).get('entry'),
                        plan_items=[{'target': item['target'], 'output': item['output']}
                                    for item in plan.get('items', [])])
        await context.progress(data)


async def _stop(context: ToolExecutionContext, b: dict) -> None:
    # Cancellation is always limited to this exact request. A tool timeout must
    # not leave its owned run executing merely because the coroutine was stopped.
    try:
        value = _checked(await _call('cancel_' + b['action'], context=b['context'], operation_id=b['operation_id']), b)
        deadline = asyncio.get_running_loop().time() + 3
        while value['record']['state'] in ACTIVE_RUN and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.1)
            value = _checked(await _call(b['action'] + '_record', context=b['context'], operation_id=b['operation_id']), b)
        await _progress(context, value)
    except (ApiError, RuntimeError):
        # Host vault transition/shutdown performs its own stop and cleanup.
        # Do not replace the original cancellation with a claim of completion.
        return


async def execute(args: Arguments, context: ToolExecutionContext, name: str) -> dict:
    preview = context.reviewed_write
    if not preview or preview.get('kind') != 'experiment_' + ('run' if name == 'experiments.run' else 'import'):
        raise ApiError(409, 'EXPERIMENT_REVIEW_REQUIRED', '请审核并单独确认当前实验操作。')
    call = ToolCall(name=name, tool_call_id=context.tool_call_id or '', arguments=args.model_dump())
    b = _binding(call, preview, context)
    value = await validate_preview(call, preview)
    started = False
    try:
        _authorized(context)
        if b['action'] == 'run' and value['record']['state'] == 'approved':
            # Set before dispatch: a lost reply may still have started the run.
            started = True
            value = _checked(await _call('start_run', context=b['context'], operation_id=b['operation_id'], fingerprint=b['fingerprint']), b)
        await _progress(context, value)
        while value['record']['state'] in (ACTIVE_RUN if b['action'] == 'run' else {'approved'}):
            _authorized(context)
            if b['action'] == 'run':
                started = True
                await asyncio.sleep(.3)
                current = _checked(await _call('run_record', context=b['context'], operation_id=b['operation_id']), b)
            else:
                started = True
                current = _checked(await _call('import_next', context=b['context'], operation_id=b['operation_id'], fingerprint=b['fingerprint']), b)
            if current['record'] != value['record']:
                await _progress(context, current)
            value = current
        if value['record']['state'] != 'completed':
            record = value['record']
            error = record.get('error') or (record.get('result') or {}).get('error') or 'EXPERIMENT_OPERATION_FAILED'
            raise ToolExecutionError(error, '实验操作未完成，请查看真实运行或导入记录。', output=value)
        return value
    except (asyncio.CancelledError, ApiError):
        if started:
            await asyncio.shield(_stop(context, b))
        raise


async def run(args: RunArguments, context: ToolExecutionContext):
    return await execute(args, context, 'experiments.run')


async def import_outputs(args: ImportArguments, context: ToolExecutionContext):
    return await execute(args, context, 'experiments.import')


def register(registry: ToolRegistry):
    for name, model, executor, description in [
        ('experiments.run', RunArguments, run, 'Propose one isolated run of saved Python and selected inputs by stable IDs. Human review and native consent are required. Network stays disabled; wait for real output or failure.'),
        ('experiments.import', ImportArguments, import_outputs, 'Propose selected outputs and exact vault destinations. Requires separate human/native import consent. Each file commits independently; report partial failures truthfully.'),
    ]:
        registry.register(ToolDefinition(name=name, permission=name, description=description,
            parameters=model.model_json_schema()), model, executor)
