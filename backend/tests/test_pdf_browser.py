import asyncio
from pathlib import Path
import pytest
from app.contracts import ExportRequest
from app.export import service
from app.export.document import ExportResult
from app.export.browser_pdf import render_snapshot, browser_executable


def test_browser_snapshot_uses_print_pipeline(monkeypatch):
    calls=[]
    def render(html,size):
        calls.append((html,size));return ExportResult(content=b'%PDF-browser',mime_type='application/pdf')
    monkeypatch.setattr('app.export.browser_pdf.render_snapshot',render)
    monkeypatch.setattr(service,'parse_document',lambda _:pytest.fail('Browser snapshots must not be reparsed by ReportLab'))
    async def run():
        request=ExportRequest(format='pdf',source={'type':'markdown','markdown':'snapshot'},print_html='<style>h1::before{content:"tape"}</style><h1>Note</h1>')
        job=await service.create_export(request);done=await service.wait_for_export(job.job_id)
        assert done.status.value=='completed'
        assert calls==[(request.print_html,'A4')]
    asyncio.run(run())


def test_preview_resources_keeps_vault_boundary_and_plot_quota_removed():
    async def run():
        source='![outside](../../private.png)\n\n```function-plot\n'+'\n'.join(f'y=x+{i}' for i in range(17))+'\n```'
        resources=await service.preview_resources(ExportRequest(format='pdf',source={'type':'markdown','markdown':source}))
        assert resources['images'][0]['data'] is None
        assert resources['images'][0]['warnings']
        assert resources['plots'][0]['svg'].startswith('<svg')
    asyncio.run(run())


@pytest.mark.skipif(browser_executable() is None,reason='No installed Chromium browser')
def test_browser_prints_css_without_executing_document_scripts(tmp_path):
    # The script would erase all text if executed. Embedded CSS and fonts must
    # survive the browser path while network and file resources stay blocked.
    html='<style>h1{color:#875343;font-size:37px} h1::before{content:"Theme "}</style><h1>Snapshot</h1><script>document.body.innerHTML="EXECUTED"</script><img src="file:///private.png">'
    result=render_snapshot(html,'A4')
    assert result.content.startswith(b'%PDF')
    assert b'/Subtype /Type0' in result.content or b'/Type /Font' in result.content
    import shutil, subprocess
    if shutil.which('pdftotext'):
        pdf=tmp_path/'snapshot.pdf'; pdf.write_bytes(result.content)
        text=subprocess.check_output(['pdftotext',str(pdf),'-']).decode('utf-8')
        assert 'Theme Snapshot' in text
        assert 'EXECUTED' not in text


@pytest.mark.parametrize('source', ['<div><IMG SRC="assets/a&amp;b.png"></div>', 'inline <img src="assets/a&amp;b.png"/> image'])
def test_preview_embeds_html_images(source):
    from app.config import get_settings
    from PIL import Image
    import base64
    folder=get_settings().vault_path/'notes'/'assets'
    folder.mkdir(parents=True)
    Image.new('RGBA',(2,2),(10,20,30,128)).save(folder/'a&b.png')
    resources=asyncio.run(service.preview_resources(ExportRequest(format='pdf',source={'type':'markdown','markdown':source,'file_path':'notes/test.md'})))
    image=resources['images'][0]
    assert image['source']=='assets/a&b.png'
    assert base64.b64decode(image['data'].split(',')[1]).startswith(b'\x89PNG')
    assert image['warnings']==[]


def test_html_images_keep_path_validation_and_code_is_not_an_image():
    source='<img src="../../private.png">\n\ninline <img src="https://example.com/a.png">\n\n`<img src="code.png">`\n\n```html\n<img src="fenced.png">\n```'
    resources=asyncio.run(service.preview_resources(ExportRequest(format='pdf',source={'type':'markdown','markdown':source})))
    assert [image['source'] for image in resources['images']]==['../../private.png','https://example.com/a.png']
    assert all(image['data'] is None and image['warnings'] for image in resources['images'])
