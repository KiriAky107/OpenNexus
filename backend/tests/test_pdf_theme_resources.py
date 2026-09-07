"""PDF theme and resource policy regressions; no real providers or user files."""
import asyncio
import base64
from io import BytesIO
import pytest
from PIL import Image
from pydantic import ValidationError
from app.contracts import ExportAsset, ExportOptions, ExportRequest
from app.export.assets import validate_assets, enrich_document, source_hash
from app.export.exporters.pdf import PdfExporter
from app.export.markdown import parse_document
from app.export.themes import PALETTES
from app.export import service


def png_asset(size=(40,30), source='graph LR; A-->B'):
    out=BytesIO(); Image.new('RGBA',size,(0,0,0,0)).save(out,'PNG')
    return ExportAsset(kind='mermaid',source_hash=source_hash(source),png_base64=base64.b64encode(out.getvalue()).decode())


@pytest.mark.parametrize('theme',list(PALETTES))
def test_pdf_theme_colors_are_written_on_every_page(theme):
    import re, zlib
    palette=PALETTES[theme]
    doc=parse_document(('## Section\n\nText body\n\n> Quoted text\n\n```python\nprint(1)\n```\n\n')*30)
    result=PdfExporter().render(doc,ExportOptions(theme_id=theme))
    streams=[]
    for match in re.finditer(rb'stream\r?\n(.*?)endstream',result.content,re.S):
        try: streams.append(zlib.decompress(base64.a85decode(match[1].strip().removesuffix(b'~>'))))
        except Exception: pass
    from reportlab.lib.rl_accel import fp_str
    command=(fp_str(*[int(palette[0][i:i+2],16)/255 for i in (1,3,5)])+' rg').encode()
    pages=[s for s in streams if b'BT' in s and b'/F' in s]
    assert len(pages)>1
    assert all(command in s for s in pages)
    assert not any('浅色打印' in w for w in result.warnings)


def test_pdf_accepts_asset_contract_beyond_previous_count_and_size():
    assets=[png_asset(source=str(i)) for i in range(65)]
    assets[0]=assets[0].model_copy(update={'png_base64':'A'*2800004})
    values=dict(source={'type':'markdown','markdown':'content'},assets=assets)
    ExportRequest(format='pdf',**values)
    with pytest.raises(ValidationError): ExportRequest(format='html',**values)
    with pytest.raises(ValidationError): ExportRequest(format='docx',**values)


def test_pdf_large_png_still_requires_valid_format():
    asset=png_asset((2100,2000))
    assert validate_assets([asset],unlimited=True)
    with pytest.raises(Exception): validate_assets([asset])
    with pytest.raises(Exception): validate_assets([asset.model_copy(update={'png_base64':'invalid'})],unlimited=True)


def test_pdf_embeds_more_than_64_resources_with_theme_background():
    doc=parse_document(('```mermaid\ngraph LR; A-->B\n```\n\n')*65)
    from app.export.assets import attach_assets
    attach_assets(doc,validate_assets([png_asset()],unlimited=True))
    assert not enrich_document(doc,unlimited=True,options=ExportOptions(theme_id='dark'))
    assert all('static_png' in node.attributes for node in doc.children)
    with Image.open(BytesIO(doc.children[-1].attributes['static_png'])) as image:
        assert image.getpixel((0,0)) == (13,17,23)
    assert PdfExporter().render(doc,ExportOptions(theme_id='dark')).content.startswith(b'%PDF')


def test_pdf_pipeline_ignores_source_and_output_quotas(monkeypatch):
    monkeypatch.setattr(service,'MAX_MARKDOWN_CHARS',8)
    monkeypatch.setattr(service,'MAX_EXPORT_BYTES',8)
    async def run():
        job=await service.create_export(ExportRequest(format='pdf',source={'type':'markdown','markdown':'Beyond the previous quota.'}))
        done=await service.wait_for_export(job.job_id)
        assert done.status.value=='completed'
        assert service.get_export_file(job.job_id).stat().st_size>8
        with pytest.raises(Exception):
            await service.create_export(ExportRequest(format='html',source={'type':'markdown','markdown':'Beyond the previous quota.'}))
    asyncio.run(run())


def test_pdf_accepts_more_than_16_curves_and_keeps_expression_safety():
    doc=parse_document('```function-plot\n'+'\n'.join(f'y=x+{i}' for i in range(17))+'\n```')
    result=PdfExporter().render(doc,ExportOptions(theme_id='dark'))
    assert not any('函数图像' in w for w in result.warnings)
    unsafe=PdfExporter().render(parse_document('```function-plot\ny=__import__("os")\n```'),ExportOptions())
    assert any('函数图像' in w for w in unsafe.warnings)


def test_pdf_custom_palette_and_math_color():
    palette=dict(zip(('page','surface','text','muted','code','border','accent'),PALETTES['midnight-purple']))
    options=ExportOptions(theme_id='my-theme',palette=palette)
    doc=parse_document('Formula $x^2$')
    assert not enrich_document(doc,unlimited=True,options=options)
    math=next(n for n in doc.children[0].children if n.type=='math_inline')
    with Image.open(BytesIO(math.attributes['static_png'])) as image:
        assert image.getpixel((0,0))==(25,19,34)
    assert not any('主题' in w for w in PdfExporter().render(doc,options).warnings)
    with pytest.raises(ValidationError): ExportOptions(palette={**palette,'text':'url(file:///private)'})
