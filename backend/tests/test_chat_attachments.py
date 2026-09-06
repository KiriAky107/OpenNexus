import asyncio
import zipfile
from types import SimpleNamespace
import pytest
from app.services import chat_attachments as service
from app.contracts import ChatRequest, ModelCapability

@pytest.mark.parametrize('suffix,name,xml,expected', [
    ('.docx','word/document.xml','<document><p><t>Hello</t></p><p><t>World</t></p></document>','Hello\nWorld'),
    ('.pptx','ppt/slides/slide1.xml','<slide><p><t>Title</t></p></slide>','第 1 页\nTitle'),
])
def test_office_text_extraction(tmp_path,suffix,name,xml,expected):
    path=tmp_path/('file'+suffix)
    with zipfile.ZipFile(path,'w') as z: z.writestr(name,xml)
    assert service.extract_document(path)==(expected,False)

def test_markdown_truncation_and_invalid_document(tmp_path):
    path=tmp_path/'file.md';path.write_text('a'*200001,encoding='utf-8')
    text,truncated=service.extract_document(path)
    assert len(text)==200000 and truncated
    path=tmp_path/'file.docx';path.write_bytes(b'invalid')
    with pytest.raises(zipfile.BadZipFile): service.extract_document(path)

def test_native_vision_precedes_registered_fallback(tmp_path):
    path=tmp_path/'image.png';path.write_bytes(b'\x89PNG\r\n\x1a\nimage')
    seen=[]
    class Adapter:
        async def list_models(self): return []
        async def complete(self,request):
            seen.append(request)
            return SimpleNamespace(text='image description')
    provider=SimpleNamespace(config=SimpleNamespace(capabilities=[ModelCapability.vision]),adapter=Adapter())
    request=ChatRequest(provider_id='mock',model='mock',messages=[])
    result=asyncio.run(service.describe_image(path,request,provider))
    assert result[1]=='native' and seen[0].messages[0].images[0].startswith('data:image/png;base64,')

def test_fallback_order_is_mcp_then_plugin(tmp_path,monkeypatch):
    from app.container import container
    from app.contracts import ToolDefinition
    path=tmp_path/'image.png';path.write_bytes(b'\x89PNG\r\n\x1a\nimage')
    definitions=[ToolDefinition(name='plugin.image',description='',source='plugin'),ToolDefinition(name='mcp.image',description='',source='mcp_server')]
    monkeypatch.setattr(container.tools,'definitions',lambda:definitions)
    seen=[]
    async def execute(call,context):
        seen.append(call.name)
        if call.name == 'mcp.image': raise TimeoutError('MCP timeout')
        return SimpleNamespace(success=True,output={'text':'fallback'})
    monkeypatch.setattr(container.tools,'execute',execute)
    class Adapter:
        async def list_models(self): return []
    provider=SimpleNamespace(config=SimpleNamespace(capabilities=[]),adapter=Adapter())
    request=ChatRequest(provider_id='mock',model='mock',messages=[],image_fallback_tools=['plugin.image','mcp.image'])
    result=asyncio.run(service.describe_image(path,request,provider))
    assert seen==['mcp.image','plugin.image'] and result[1]=='plugin.image'


def test_audio_uses_persistent_transcription_and_returns_text_context(tmp_path,monkeypatch):
    from app.services import transcription_service as jobs
    from app.services.attachment_service import attachment_path
    path=attachment_path('audio.wav');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'audio')
    seen=[]
    async def transcribe(attachment_id,**kwargs):
        seen.append((attachment_id,kwargs))
        return SimpleNamespace(status='completed',text='transcript',job_id='job_test',warnings=[])
    monkeypatch.setattr(jobs,'create_transcription',transcribe)
    request=ChatRequest(provider_id='mock',model='mock',messages=[],attachments=['audio.wav'])
    result=asyncio.run(service.prepare(request,None))
    assert seen==[('audio.wav',{'wait':True})]
    assert result.attachments==[] and 'transcript' in result.system
    assert result.metadata['chat_attachment_context'][0]['route']=='transcription:job_test'


def test_legacy_ppt_reads_unicode_text_records(tmp_path,monkeypatch):
    import io,struct,olefile
    path=tmp_path/'legacy.ppt';path.write_bytes(b'compound-file-fixture')
    text='旧版演示文稿'.encode('utf-16-le');data=struct.pack('<HHI',0,4000,len(text))+text
    class Ole:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def openstream(self,name):
            assert name=='PowerPoint Document'
            return io.BytesIO(data)
    monkeypatch.setattr(olefile,'OleFileIO',lambda path:Ole())
    assert service.extract_document(path)==('旧版演示文稿',False)


def test_compatible_provider_serializes_native_image_parts():
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.contracts import ModelRequest, Message
    request=ModelRequest(provider_id='p',model='m',messages=[Message(role='user',content='describe',images=['data:image/png;base64,aW1hZ2U='])])
    wire=OpenAICompatibleProvider._messages(None,request)
    assert wire[0]['content']==[{'type':'text','text':'describe'},{'type':'image_url','image_url':{'url':'data:image/png;base64,aW1hZ2U='}}]
