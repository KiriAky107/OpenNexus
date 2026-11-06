"""Canvas Agent tools: original ink reads, model proposals, reviewed append-only handwriting."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from app import host_bridge
from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import ToolCall, ToolDefinition
from app.errors import ApiError
from app.services import canvas_service
from app.services.desktop_notes import call as host_call
from app.services.handwriting_contract import KEY, valid
from app.services.note_preview import content_diff, digest
from app.config import get_settings

WRITE_TOOL = 'canvas.handwrite'


def _vault_binding():
    if host_bridge.active is not None:
        return host_bridge.vault_id.get()
    return 'web:' + hashlib.sha256(str(get_settings().vault_path.resolve()).encode()).hexdigest()


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Read(Arguments):
    path: str = Field(min_length=1, max_length=1024)


class Handwrite(Read):
    expected_content_hash: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    text: str = Field(min_length=1, max_length=8000)
    x: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    y: int | None = Field(default=None, ge=-1_000_000, le=990_000)
    font_size: int = Field(default=28, ge=12, le=48)


class Analyze(Read):
    node_id: str = Field(min_length=1, max_length=128)
    action: Literal['recognize', 'summarize', 'organize'] = 'recognize'
    instruction: str = Field(default='', max_length=4000)


def _relative(path):
    relative = path.removeprefix('/')
    if not relative.lower().endswith('.canvas') or not canvas_service._safe_file(relative):
        raise ApiError(422, 'CANVAS_PATH_INVALID', '请选择当前知识库内的画布。')
    return relative


async def snapshot(path):
    path = _relative(path)
    if host_bridge.active is not None:
        value = await asyncio.to_thread(host_call, 'canvas.snapshot', path=path)
        if value is None:
            return None
        canvas_service.validate(value['content'].encode('utf-8'))
        return {'path': path, 'content': value['content'], 'content_hash': value['hash'], 'file_id': value['file_id']}
    try:
        return {**canvas_service.read(path).model_dump(), 'path': path, 'file_id': None}
    except ApiError as exc:
        if exc.code == 'RESOURCE_NOT_FOUND':
            return None
        raise


def _append(before, args, call_id):
    document = json.loads(before['content']) if before else {'nodes': [], 'edges': []}
    document.setdefault('nodes', [])
    document.setdefault('edges', [])
    # Keep Unicode, Markdown/math source and existing ink intact. Layout is a view.
    x, lines = 24.0, 1
    for char in args.text.replace('\r\n', '\n').replace('\r', '\n'):
        advance = args.font_size * (1 if ord(char) > 255 else .4 if char == ' ' else .62) + 1
        if char == '\n':
            lines += 1
            x = 24
            continue
        if x + advance > 936:
            lines += 1
            x = 24
        x += advance
    height = max(640, int(48 + args.font_size + (lines - 1) * args.font_size * 1.7 + 1))
    if height > 4096:
        raise ApiError(422, 'HANDWRITING_PAGE_FULL', '内容超过一页，请分多次追加手写节点。')
    y = args.y if args.y is not None else max((n['y'] + n['height'] + 40 for n in document['nodes']), default=0)
    node_id = str(uuid5(NAMESPACE_URL, f'opennexus-canvas:{call_id}:{digest(args.model_dump())}'))
    if any(n['id'] == node_id for n in document['nodes']):
        raise ApiError(409, 'CANVAS_OPERATION_ALREADY_PRESENT', '这个手写操作已在画布中，请先读取原结果。')
    ink = {'version': 1, 'mode': 'text', 'width': 960, 'height': height, 'paper': 'ruled', 'strokes': [],
           'fontSize': args.font_size, 'lineHeight': 1.7, 'spacing': 1, 'variation': .08, 'seed': 42}
    document['nodes'].append({'id': node_id, 'type': 'text', 'x': args.x, 'y': y, 'width': 480,
                              'height': height / 2 + 40, 'text': args.text, KEY: ink})
    content = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    canvas_service.validate(content.encode())
    return content, node_id


async def preview_write(call):
    args = Handwrite.model_validate(call.arguments)
    before = await snapshot(args.path)
    if (before['content_hash'] if before else '') != args.expected_content_hash:
        raise ApiError(409, 'CANVAS_CONTENT_CONFLICT', '画布已变化，请重新读取后生成预览。')
    content, node_id = _append(before, args, call.tool_call_id)
    binding = {'vault_id': _vault_binding(), 'tool': call.name, 'arguments': args.model_dump(),
               'before': {k: v for k, v in before.items() if k != 'content'} if before else None,
               'after_hash': hashlib.sha256(content.encode()).hexdigest(), 'node_id': node_id}
    return {'kind': 'canvas_handwriting', 'token': digest(binding), 'binding': binding,
            'file_path': _relative(args.path), 'note_id': None, 'operation': 'append' if before else 'create',
            'overwrite': before is not None, 'before_bytes': len(before['content'].encode()) if before else 0,
            'after_bytes': len(content.encode()), 'metadata': {'title': None, 'tags': None},
            'diff': content_diff(before['content'] if before else '', content)}


async def validate_preview(call, preview):
    current = await preview_write(call)
    if preview.get('kind') != 'canvas_handwriting' or current['token'] != preview.get('token'):
        raise ApiError(409, 'CANVAS_PREVIEW_STALE', '画布或当前工作区已变化，请重新审核。')


async def handwrite(args: Handwrite, context: ToolExecutionContext):
    preview = context.reviewed_write
    if not preview or preview.get('kind') != 'canvas_handwriting':
        raise ApiError(409, 'CANVAS_PREVIEW_REQUIRED', '请先审核这次手写追加。')
    call = ToolCall(name=WRITE_TOOL, tool_call_id=context.tool_call_id or 'canvas-write', arguments=args.model_dump())
    await validate_preview(call, preview)
    before = await snapshot(args.path)
    current_identity = {k: v for k, v in before.items() if k != 'content'} if before else None
    if current_identity != preview['binding']['before'] or _vault_binding() != preview['binding']['vault_id']:
        raise ApiError(409, 'CANVAS_PREVIEW_STALE', '原画布身份或工作区已变化，请重新审核。')
    content, node_id = _append(before, args, call.tool_call_id)
    if hashlib.sha256(content.encode()).hexdigest() != preview['binding']['after_hash']:
        raise ApiError(409, 'CANVAS_PREVIEW_STALE', '画布已变化，请重新审核。')
    if context.is_authorized and not context.is_authorized():
        raise ApiError(403, 'PERMISSION_DENIED', '当前 Agent 已停止或写入权限已撤销。')
    if host_bridge.active is None:
        result = await canvas_service.write(canvas_service.CanvasWriteRequest(path=_relative(args.path),
            expected_content_hash=args.expected_content_hash, content=content))
        return {**result.model_dump(), 'node_id': node_id}
    operation = host_bridge.operation_id.get() or str(uuid5(NAMESPACE_URL, f'{context.run_id}:{context.tool_call_id}'))
    receipt = await asyncio.to_thread(host_call, 'canvas.write', path=_relative(args.path), expected=args.expected_content_hash,
        expected_file_id=before['file_id'] if before else None, content=content, operation_id=operation)
    entry = receipt.get('result', {})
    if (receipt.get('state') != 'committed' or receipt.get('operation_id') != operation
            or entry.get('hash') != preview['binding']['after_hash'] or entry.get('path') != _relative(args.path)
            or (before and entry.get('file_id') != before['file_id'])):
        raise ApiError(409, 'CANVAS_RECONCILIATION_REQUIRED', '请核对原操作回执，不要盲目重复追加。')
    return {**receipt, 'node_id': node_id}


async def read(args: Read, _):
    value = await snapshot(args.path)
    if value is None:
        raise ApiError(404, 'RESOURCE_NOT_FOUND', '画布不存在。')
    return value


async def analyze(args: Analyze, context: ToolExecutionContext):
    from app.container import container
    from app.services.handwriting import AnalyzeRequest, analyze as infer
    if not context.provider_id or not context.model:
        raise ApiError(409, 'HANDWRITING_MODEL_REQUIRED', '请为当前 Agent 选择一个已有模型。')
    value = await snapshot(args.path)
    if value is None:
        raise ApiError(404, 'RESOURCE_NOT_FOUND', '画布不存在。')
    node = next((n for n in json.loads(value['content']).get('nodes', []) if n['id'] == args.node_id), None)
    if not node or not valid(node.get(KEY)):
        raise ApiError(422, 'CANVAS_HANDWRITING_REQUIRED', '请选择一个手写节点。')
    ink, image = node[KEY], ''
    if ink['strokes']:
        paper = Image.new('RGB', (int(ink['width']), int(ink['height'])), 'white')
        draw = ImageDraw.Draw(paper)
        for stroke in ink['strokes']:
            points = stroke['points']
            for index, point in enumerate(points):
                previous = points[max(0, index - 1)]
                width = max(1, round(stroke['width'] * (.25 + .75 * point['pressure'])))
                draw.line([(previous['x'], previous['y']), (point['x'], point['y'])], fill=stroke['color'], width=width)
                r = width / 2
                draw.ellipse((point['x']-r, point['y']-r, point['x']+r, point['y']+r), fill=stroke['color'])
        buffer = io.BytesIO()
        paper.save(buffer, format='PNG')
        image = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    result = await infer(AnalyzeRequest(provider_id=context.provider_id, model=context.model, action=args.action,
        text=node.get('text', ''), image=image, instruction=args.instruction), container.providers, run_id=context.run_id)
    return {**result, 'source_path': value['path'], 'source_hash': value['content_hash'], 'node_id': args.node_id}


def register(registry: ToolRegistry):
    for name, model, executor, permission, description in [
        ('canvas.read', Read, read, 'notes.read', 'Read a JSON Canvas including editable original handwriting and its current content_hash.'),
        (WRITE_TOOL, Handwrite, handwrite, 'canvas.write', 'Append editable handwritten text to a canvas after human review. Read it first and pass expected_content_hash; empty hash only creates a new canvas. Original ink is preserved. Use for writing, summaries and organized notes.'),
        ('canvas.analyze_handwriting', Analyze, analyze, 'notes.read', 'Use this Agent\'s selected model to transcribe, summarize or organize one handwriting node; ink requires a vision model. Cannot choose another provider. Returns a preview and preserves original ink. Save reviewed text with notes.create or canvas.handwrite.'),
    ]:
        registry.register(ToolDefinition(name=name, description=description, permission=permission,
            parameters=model.model_json_schema()), model, executor)
