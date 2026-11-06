import asyncio
import base64
import copy
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app import host_bridge
from app.agent import canvas_tools
from app.agent.permissions import PermissionMode, PermissionPolicy
from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.config import get_settings
from app.contracts import ModelCapability, ToolCall
from app.errors import ApiError
from app.providers.base import ProviderError, ProviderTurn
from app.services import canvas_service, handwriting, tool_write_preview
from app.services.handwriting_contract import KEY, valid


def ink():
    return {'version': 1, 'mode': 'ink', 'width': 960, 'height': 640, 'paper': 'ruled', 'fontSize': 28,
        'lineHeight': 1.7, 'spacing': 1, 'variation': .08, 'seed': 42,
        'strokes': [{'id': 'stroke', 'color': '#263746', 'width': 3, 'points': [{'x': 5, 'y': 5, 'pressure': .5}]}]}


def document():
    return {'nodes': [{'id': 'original', 'type': 'text', 'x': 0, 'y': 0, 'width': 480, 'height': 360,
                       'text': '原始笔迹', KEY: ink()}], 'edges': [], 'extension': {'keep': True}}


@pytest.mark.parametrize('change', [
    lambda v: v['strokes'][0]['points'][0].update(x=-1),
    lambda v: v['strokes'][0]['points'][0].update(pressure=True),
    lambda v: v['strokes'][0].update(color='url(secret)'),
    lambda v: v['strokes'].append(copy.deepcopy(v['strokes'][0])),
    lambda v: v.update(mode='text'),
    lambda v: v.update(width=10**1000),
    lambda v: v['strokes'][0].update(points=[{'x': 1, 'y': 1, 'pressure': .5}] * 20001),
])
def test_ink_contract_rejects_invalid_input(change):
    value = ink()
    assert valid(value)
    change(value)
    assert not valid(value)
    doc = document()
    doc['nodes'][0][KEY] = value
    with pytest.raises(ApiError):
        canvas_service.validate(json.dumps(doc).encode())


def create():
    get_settings().vault_path.mkdir(parents=True, exist_ok=True)
    return asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(path='notes.canvas', content=json.dumps(document(), ensure_ascii=False))))


def call_for(before):
    return ToolCall(tool_call_id='one-reviewed-append', name='canvas.handwrite', arguments={
        'path': 'notes.canvas', 'text': '# 手写总结\n\n原文保留，3 * 4 = 12。', 'expected_content_hash': before.content_hash})


def test_agent_append_is_reviewed_preserves_original_and_uses_cas():
    before = create()
    call = call_for(before)
    preview = asyncio.run(tool_write_preview.preview_write(call))
    assert preview['kind'] == 'canvas_handwriting'
    assert not json.loads(canvas_service.read('notes.canvas').content)['nodes'][0].get('overwritten')
    result = asyncio.run(canvas_tools.handwrite(canvas_tools.Handwrite.model_validate(call.arguments),
        ToolExecutionContext(run_id='run', tool_call_id=call.tool_call_id, reviewed_write=preview)))
    after = json.loads(result['content'])
    assert after['nodes'][0] == document()['nodes'][0]
    assert after['extension'] == {'keep': True}
    assert after['nodes'][1]['text'] == call.arguments['text']
    assert after['nodes'][1][KEY]['mode'] == 'text'
    with pytest.raises(ApiError):
        asyncio.run(canvas_tools.handwrite(canvas_tools.Handwrite.model_validate(call.arguments),
            ToolExecutionContext(run_id='run', tool_call_id=call.tool_call_id, reviewed_write=preview)))


def test_agent_cannot_write_without_review_or_after_human_edit():
    before = create()
    call = call_for(before)
    with pytest.raises(ApiError, match='审核'):
        asyncio.run(canvas_tools.handwrite(canvas_tools.Handwrite.model_validate(call.arguments), ToolExecutionContext(run_id='run')))
    preview = asyncio.run(canvas_tools.preview_write(call))
    changed = json.loads(before.content)
    changed['nodes'][0]['text'] = 'human edit'
    asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(path='notes.canvas', expected_content_hash=before.content_hash, content=json.dumps(changed))))
    with pytest.raises(ApiError):
        asyncio.run(canvas_tools.validate_preview(call, preview))
    assert json.loads(canvas_service.read('notes.canvas').content)['nodes'][0]['text'] == 'human edit'


def test_agent_review_is_once_only_and_tools_are_registered():
    policy = PermissionPolicy()
    assert policy.mode_for('canvas.write') == PermissionMode.confirm
    policy.set_rule('canvas.write', PermissionMode.allow)
    assert policy.mode_for('canvas.write') == PermissionMode.confirm
    registry = ToolRegistry()
    canvas_tools.register(registry)
    assert {x.name for x in registry.definitions()} == {'canvas.read', 'canvas.handwrite', 'canvas.analyze_handwriting'}


def test_native_identity_change_during_execution_refuses_write(monkeypatch):
    content = json.dumps(document())
    original = {'path': 'notes.canvas', 'content': content, 'content_hash': 'a'*64, 'file_id': 'original'}
    responses = iter([original, original, {**original, 'file_id': 'replacement'}])
    async def snapshot(_):
        return next(responses)
    monkeypatch.setattr(canvas_tools, 'snapshot', snapshot)
    call = ToolCall(name='canvas.handwrite', tool_call_id='call', arguments={'path': 'notes.canvas', 'expected_content_hash': 'a'*64, 'text': 'summary'})
    preview = asyncio.run(canvas_tools.preview_write(call))
    with pytest.raises(ApiError) as error:
        asyncio.run(canvas_tools.handwrite(canvas_tools.Handwrite.model_validate(call.arguments), ToolExecutionContext(run_id='r', tool_call_id='call', reviewed_write=preview)))
    assert error.value.code == 'CANVAS_PREVIEW_STALE'


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


class Provider:
    def __init__(self, vision=True, result=None):
        self.calls = []
        self.config = SimpleNamespace(capabilities=[ModelCapability.chat] + ([ModelCapability.vision] if vision else []))
        self.adapter = self
        self.result = result or ProviderTurn(text='# Summary')
    def get(self, provider_id):
        assert provider_id == 'selected'
        return self
    async def complete(self, request):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_analysis_is_explicit_multimodal_preview_only():
    provider = Provider()
    result = asyncio.run(handwriting.analyze(handwriting.AnalyzeRequest(provider_id='selected', model='vision',
        action='organize', image=png(), text='original'), provider))
    assert result['persisted'] is False and result['text'] == '# Summary'
    request = provider.calls[0]
    assert request.model == 'vision' and request.tools == [] and request.messages[0].images == [png()]
    assert not request.attachments


def test_nonvision_and_invalid_images_fail_before_model_call():
    provider = Provider(vision=False)
    with pytest.raises(ApiError) as error:
        asyncio.run(handwriting.analyze(handwriting.AnalyzeRequest(provider_id='selected', model='text', action='recognize', image=png()), provider))
    assert error.value.code == 'HANDWRITING_VISION_REQUIRED' and not provider.calls
    for image in ['https://evil.invalid/private', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,YmFk']:
        with pytest.raises(ValidationError):
            handwriting.AnalyzeRequest(provider_id='selected', model='vision', action='recognize', image=image)


@pytest.mark.parametrize('result', [ProviderTurn(text=''), ProviderTurn(text='x', tool_calls=[{}]), ProviderError('PROVIDER_AUTH_FAILED', 'secret-url')])
def test_bad_model_results_do_not_write_or_echo_provider_details(result):
    provider = Provider(result=result)
    with pytest.raises(ApiError) as error:
        asyncio.run(handwriting.analyze(handwriting.AnalyzeRequest(provider_id='selected', model='m', action='summarize', text='source'), provider))
    assert 'secret-url' not in error.value.message
    assert len(provider.calls) == 1
