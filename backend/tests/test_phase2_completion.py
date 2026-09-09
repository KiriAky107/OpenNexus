import asyncio
import base64
import json
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
import pytest
from PIL import Image
from app.contracts import ExportAsset, ExportRequest, AgentBenchmarkRequest, BenchmarkStatus
from app.export import service as exports
from app.export.assets import validate_assets, source_hash
from app.errors import ApiError

def asset(source='flowchart LR\n A --> B'):
    buf=BytesIO(); Image.new('RGB',(60,40),'blue').save(buf,'PNG')
    return ExportAsset(kind='mermaid', source_hash=source_hash(source), png_base64=base64.b64encode(buf.getvalue()).decode())

@pytest.mark.parametrize('format',['html','pdf','docx'])
def test_static_mermaid_in_export(format):
    async def run():
        job=await exports.create_export(ExportRequest(source={'type':'markdown','markdown':'```mermaid\nflowchart LR\n A --> B\n```'},format=format,assets=[asset()],title='snapshot'))
        finished=await exports.wait_for_export(job.job_id)
        assert finished.status.value=='completed'
        assert not any('mermaid' in w for w in finished.warnings)
        data=exports.get_export_file(job.job_id).read_bytes()
        if format=='html': assert b'data:image/png;base64,' in data
        elif format=='pdf': assert b'/Subtype /Image' in data
        else:
            with ZipFile(BytesIO(data)) as archive: assert any(n.startswith('word/media/') for n in archive.namelist())
    asyncio.run(run())

def test_asset_invalid_and_duplicate():
    with pytest.raises(ApiError): validate_assets([asset().model_copy(update={'png_base64':'not png'})])
    with pytest.raises(ApiError): validate_assets([asset(),asset()])

def test_stale_asset_does_not_replace_source():
    from app.export.assets import attach_assets
    from app.export.markdown import parse_document
    document=parse_document('```mermaid\nflowchart LR\n X --> Y\n```')
    attach_assets(document,validate_assets([asset()]))
    assert 'static_png' not in document.children[0].attributes

@pytest.mark.parametrize('format',['html','pdf','docx'])
def test_math_and_local_image_export(format):
    from app.config import get_settings
    vault=get_settings().vault_path; vault.mkdir(parents=True)
    Image.new('RGB',(100,50),'green').save(vault/'figure.png')
    async def run():
        job=await exports.create_export(ExportRequest(source={'type':'markdown','file_path':'demo.md',
            'markdown':'Formula $\\frac{x^2}{2}$\n\n![figure](figure.png)'},format=format))
        done=await exports.wait_for_export(job.job_id)
        assert done.status.value=='completed'
        assert not any('公式' in w or '图片' in w for w in done.warnings)
        data=exports.get_export_file(job.job_id).read_bytes()
        if format=='html': assert data.count(b'data:image/png;base64,')==2
        if format=='docx':
            with ZipFile(BytesIO(data)) as archive: assert len([n for n in archive.namelist() if n.startswith('word/media/')])==2
    asyncio.run(run())

def test_local_image_path_escape_and_tex_fallback():
    from app.export.assets import enrich_document
    from app.export.markdown import parse_document
    document=parse_document('![no](../outside.png)\n\n$\\unknownmacro{x}$')
    warnings=enrich_document(document,'demo.md')
    assert len(warnings)==2

@pytest.mark.parametrize('theme',['light','dark','sepia','paper-moments','ocean-blue','midnight-purple'])
def test_function_preview_theme_and_parser(theme):
    from app.plot_routes import PlotRequest, preview
    result=preview(PlotRequest(source='y = x^2\ny = sin(x)',theme_id=theme))
    assert '<polyline' in result.result.content
    assert 'nan' not in result.result.content
    assert preview(PlotRequest(source='y = __import__("os")')).result is None

def test_agent_benchmark_real_runtime_offline_lifecycle():
    from app.config import get_settings
    from app.benchmarks import agent, service
    from app.container import container
    directory=get_settings().benchmark_datasets_path; directory.mkdir(parents=True,exist_ok=True)
    (directory/'agent-test.json').write_text(json.dumps({'dataset_id':'agent-test','kind':'agent','version':'1', 'cases':[
        {'case_id':'hello','prompt':'hello','output_contains':['definitely-absent'],'allowed_tools':[]}
    ]}),encoding='utf-8')
    async def run():
        request=AgentBenchmarkRequest(dataset_id='agent-test',provider_id='mock',model='mock-model',offline=True)
        with pytest.raises(ApiError): await agent.create_run(request.model_copy(update={'offline':False}))
        created=await agent.create_run(request)
        done=await service.wait_for_run(created.run_id)
        assert done.status==BenchmarkStatus.completed
        report=service.get_report(created.run_id)
        assert report.metrics['task_success_rate']==0
        case=report.cases[0]
        assert case.agent_run_id and container.agent.get_run(case.agent_run_id)
        assert report.config_snapshot['execution']=='offline'
        events=service.get_events(created.run_id)
        assert [e.sequence for e in events]==list(range(len(events)))
        assert sum(e.event.value.startswith('Run') and e.event.value!='RunStarted' for e in events)==1
        second=await agent.create_run(request); service.cancel_run(second.run_id)
        assert (await service.wait_for_run(second.run_id)).status==BenchmarkStatus.cancelled
    asyncio.run(run())

def test_agent_score_counts_duplicate_and_invalid_calls():
    from types import SimpleNamespace as NS
    from app.contracts import AgentDatasetCase
    from app.benchmarks.agent import score,aggregate
    case=AgentDatasetCase(case_id='x',prompt='x',allowed_tools=['math.add'],expected_tools=[{'name':'math.add','arguments':{'left':2}}])
    events=[NS(event=NS(value='ToolCall'),data={'name':'math.add','arguments':{'left':2}}) for _ in range(2)]
    run=NS(status=NS(value='completed'),tool_results=[NS(success=False,name='math.add',error_code='TOOL_ARGUMENT_INVALID')],output='',citations=[],run_id='r',current_step=2,token_usage=10,error_code=None)
    result=score(case,run,events,10,0)
    assert not result.success
    assert aggregate([result])['tool_argument_accuracy']==.5
    assert aggregate([result])['invalid_tool_call_rate']==.5

def test_local_embedding_cache_is_config_scoped_and_returns_copies(monkeypatch,tmp_path):
    from app.local_models import runtime as local
    from app.retrieval.provenance import capture_embedding
    monkeypatch.setattr(local,'read_state',lambda key:{'status':'installed'})
    monkeypatch.setattr(local,'interpreter',lambda config=None:Path(__file__))
    monkeypatch.setattr(local,'model_path',lambda key:tmp_path/key)
    calls=[]
    async def infer(*args,**kwargs):
        calls.append(args);return [[.5]*384]
    monkeypatch.setattr(local.runtime,'infer',infer)
    async def run():
        embedding=local.LocalEmbedding(local.RuntimeConfig())
        first=await embedding.embed_documents(['query'])
        first[0][0]=999
        with capture_embedding() as observation:
            second=await embedding.embed_documents(['query'])
        assert second[0][0]==.5 and observation['query_embedding_cache']=='hit'
        assert len(calls)==1
        await local.LocalEmbedding(local.RuntimeConfig(version=2)).embed_documents(['query'])
        assert len(calls)==2
    asyncio.run(run())


def test_preview_http_and_agent_benchmark_validation():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        response = client.post('/api/plots/function', json={'source':'y = sin(x)', 'theme_id':'dark'})
        assert response.status_code == 200 and '<polyline' in response.json()['result']['content']
        assert client.post('/api/plots/function', json={'source':'x'*20001}).status_code == 422
        bad = client.post('/api/benchmarks/agent/runs', json={
            'dataset_id':'missing', 'provider_id':'missing', 'model':'missing'})
        assert bad.status_code == 404
        assert client.get('/api/benchmarks/runs/missing/report').status_code == 404
        schema = client.get('/openapi.json').json()
        assert '/api/benchmarks/agent/runs' in schema['paths']


def test_preview_rejects_aggregate_complexity_before_sampling():
    from app.plot_routes import PlotRequest, preview
    source = '\n'.join('y = '+ '+'.join(['(x+x)']*150) for _ in range(16))
    result = preview(PlotRequest(source=source))
    assert result.result is None
    assert result.diagnostics[0].code == 'PLOT_BUDGET_EXCEEDED'


def test_repeated_static_assets_share_document_resource_budget():
    from app.export.assets import attach_assets, enrich_document
    from app.export.markdown import parse_document
    document = parse_document(('```mermaid\nflowchart LR\n A --> B\n```\n\n')*65)
    attach_assets(document, validate_assets([asset()]))
    warnings = enrich_document(document)
    assert sum(bool(node.attributes.get('static_png')) for node in document.children) == 64
    assert any('预算' in warning for warning in warnings)


@pytest.mark.parametrize('order', [(1, 2), (2, 1)])
def test_agent_parameter_matching_is_independent_of_call_order(order):
    from types import SimpleNamespace as NS
    from app.benchmarks.agent import score
    from app.contracts import AgentDatasetCase
    case = AgentDatasetCase(case_id='overlap', prompt='test', allowed_tools=['math.add'],
        expected_tools=[{'name':'math.add','arguments':{}}, {'name':'math.add','arguments':{'left':1}}])
    events = [NS(event=NS(value='ToolCall'), data={'name':'math.add','arguments':{'left':value}}) for value in order]
    run = NS(status=NS(value='completed'),tool_results=[],output='',citations=[],run_id='test',current_step=1,token_usage=0,error_code=None)
    result = score(case, run, events, 1, 0)
    assert result.success and result.accurate_calls == result.selected_calls == 2
    # 两个期望不能重复使用一个匹配的调用。
    result = score(case, run, events[:1], 1, 0)
    assert not result.success and result.accurate_calls == 1


@pytest.mark.parametrize('page_size', ['A4', 'Letter'])
@pytest.mark.parametrize('dimensions', [(200, 2000), (2000, 200)])
def test_docx_static_images_fit_both_page_dimensions(page_size, dimensions):
    from app.export.markdown import parse_document
    from app.export.exporters.docx import DocxExporter
    from app.contracts import ExportOptions
    from docx import Document
    png = BytesIO(); Image.new('RGB', dimensions, 'white').save(png, 'PNG')
    document = parse_document('```mermaid\nflowchart TD\n A-->B\n```')
    document.children[0].attributes['static_png'] = png.getvalue()
    result = DocxExporter().render(document, ExportOptions(page_size=page_size))
    word = Document(BytesIO(result.content)); section = word.sections[0]; shape = word.inline_shapes[0]
    assert shape.width <= section.page_width - section.left_margin - section.right_margin
    assert shape.height < section.page_height - section.top_margin - section.bottom_margin
    assert shape.width / shape.height == pytest.approx(dimensions[0] / dimensions[1], rel=1e-5)
