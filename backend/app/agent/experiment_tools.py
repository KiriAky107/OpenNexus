"""Structured source-file tools; execution and import have separate permissions."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app import host_bridge
from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import ToolCall, ToolDefinition
from app.errors import ApiError
from app.services.desktop_notes import call as host_call
from app.services.note_preview import content_diff, digest

MAX_FILE_BYTES = 2 * 1024 * 1024
WRITE_TOOL = 'experiments.files.write'


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ListArguments(Arguments):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class ReadArguments(Arguments):
    file_id: str = Field(min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=16_384, ge=1, le=65_536)


class WriteArguments(Arguments):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')


def decode_snapshot(value: dict | None) -> tuple[dict | None, str]:
    if value is None:
        return None, ''
    try:
        data = base64.b64decode(value['content_base64'], validate=True)
        entry = value['entry']
        if (len(data) > MAX_FILE_BYTES or len(data) != value['byte_size']
                or hashlib.sha256(data).hexdigest() != entry['hash']):
            raise ValueError('snapshot mismatch')
        return entry, data.decode('utf-8')
    except (KeyError, TypeError, ValueError, binascii.Error, UnicodeDecodeError):
        raise ApiError(409, 'EXPERIMENT_SNAPSHOT_INVALID', '源文件回执无效，请重新读取。') from None


async def list_files(args: ListArguments, _: ToolExecutionContext) -> dict:
    value = await asyncio.to_thread(host_call, 'experiments.list', **args.model_dump())
    return {**value, 'page': {'offset': args.offset, 'limit': args.limit, 'total': value['total']}}


async def read_file(args: ReadArguments, _: ToolExecutionContext) -> dict:
    value = await asyncio.to_thread(host_call, 'experiments.read', file_id=args.file_id)
    entry, content = decode_snapshot(value)
    if entry is None:
        raise ApiError(404, 'FILE_NOT_FOUND', '实验文件不存在。')
    end = min(len(content), args.offset + args.max_chars)
    return {**entry, 'content': content[args.offset:end], 'offset': args.offset,
            'next_offset': end if end < len(content) else None,
            'total_chars': len(content), 'byte_size': value['byte_size'],
            'truncated': args.offset > 0 or end < len(content)}


async def preview_write(call: ToolCall) -> dict:
    from pydantic import ValidationError
    try:
        args = WriteArguments.model_validate(call.arguments)
    except ValidationError:
        raise ApiError(422, 'TOOL_ARGUMENT_INVALID', '实验文件参数无效，请重新提出修改。') from None
    try:
        encoded = args.content.encode('utf-8')
    except UnicodeEncodeError:
        raise ApiError(422, 'EXPERIMENT_INPUT_NOT_UTF8', '源文件必须是有效 UTF-8 文本。') from None
    if len(encoded) > MAX_FILE_BYTES:
        raise ApiError(422, 'EXPERIMENT_INPUT_TOO_LARGE', '实验文件超过 2 MiB 上限。')
    value = await asyncio.to_thread(host_call, 'experiments.snapshot', path=args.path)
    before, content = decode_snapshot(value)
    if (before['hash'] if before else '') != args.expected_hash:
        raise ApiError(409, 'EXPERIMENT_FILE_CONFLICT', '源文件已变化，请重新读取后提出修改。')
    binding = {'vault_id': host_bridge.vault_id.get(), 'tool': call.name,
               'arguments': args.model_dump(), 'before': before,
               'path': args.path, 'after_hash': hashlib.sha256(encoded).hexdigest()}
    return {'kind': 'experiment_file', 'token': digest(binding), 'binding': binding,
            'file_path': args.path, 'note_id': None, 'file_id': before['file_id'] if before else None,
            'operation': 'replace' if before else 'create', 'overwrite': before is not None,
            'before_bytes': value['byte_size'] if value else 0, 'after_bytes': len(encoded),
            'metadata': {'title': None, 'tags': None}, 'diff': content_diff(content, args.content)}


async def validate_preview(call: ToolCall, preview: dict) -> None:
    current = await preview_write(call)
    if current['token'] != preview.get('token'):
        raise ApiError(409, 'EXPERIMENT_PREVIEW_STALE', '源文件、修订或操作已变化，请重新预览。')


async def write_file(args: WriteArguments, context: ToolExecutionContext) -> dict:
    preview = context.reviewed_write
    if not preview or preview.get('kind') != 'experiment_file':
        raise ApiError(409, 'EXPERIMENT_PREVIEW_REQUIRED', '请先审核当前源文件修改。')
    call = ToolCall(name=WRITE_TOOL, arguments=args.model_dump(),
                    tool_call_id=context.tool_call_id or 'reviewed-write')
    await validate_preview(call, preview)
    before = preview['binding']['before']
    operation = host_bridge.operation_id.get() or str(uuid5(
        NAMESPACE_URL, f'opennexus:{context.run_id}:{context.tool_call_id}'))
    receipt = await asyncio.to_thread(host_call, 'experiments.write', path=args.path,
        expected=args.expected_hash, expected_file_id=before['file_id'] if before else None,
        expected_revision=before['revision'] if before else None,
        content_base64=base64.b64encode(args.content.encode('utf-8')).decode('ascii'),
        operation_id=operation)
    result = receipt.get('result', {})
    if (receipt.get('state') != 'committed' or receipt.get('operation_id') != operation
            or result.get('hash') != preview['binding']['after_hash']
            or result.get('path') != args.path or not result.get('file_id')
            or (before and result['file_id'] != before['file_id'])):
        raise ApiError(409, 'EXPERIMENT_WRITE_RECONCILIATION_REQUIRED', '写入回执不一致，请核对实际源文件。')
    return receipt


def register(registry: ToolRegistry) -> None:
    for name, model, executor, permission, description in [
        ('experiments.files.list', ListArguments, list_files, 'experiments.files.read',
         'List saved Python, JSON and CSV files under experiments/ with stable IDs and revisions.'),
        ('experiments.files.read', ReadArguments, read_file, 'experiments.files.read',
         'Read a bounded UTF-8 source-file segment by stable ID. Preserve hash for reviewed writes.'),
        (WRITE_TOOL, WriteArguments, write_file, 'experiments.files.write',
         'Propose a saved source-file write for human diff review. Empty expected_hash creates; '
         'replacement requires the current hash. This does not run code or import outputs.'),
    ]:
        registry.register(ToolDefinition(name=name, description=description,
            parameters=model.model_json_schema(), permission=permission), model, executor)
    from app.agent.experiment_actions import register as register_actions
    register_actions(registry)
