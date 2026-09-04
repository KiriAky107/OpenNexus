import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.local_models import manager
from app.local_models.runtime import Runtime
from app.providers.base import ProviderError


def test_download_resumes_partial_and_checks_digest(monkeypatch):
    payload = b'verified-model-weights'
    entry = {'path':'model.safetensors','size':len(payload),'hash':hashlib.sha256(payload).hexdigest(),
             'algorithm':'sha256','url':'https://fixture.invalid/weights'}
    async def manifest(client, spec):
        return [entry]
    monkeypatch.setattr(manager, '_manifest', manifest)
    path = manager.model_path('bekko')
    path.mkdir(parents=True)
    (path/'model.safetensors.partial').write_bytes(payload[:5])
    requests = []
    def respond(request):
        requests.append(request)
        assert request.headers['range'] == 'bytes=5-'
        return httpx.Response(206, headers={'content-range':f'bytes 5-{len(payload)-1}/{len(payload)}'},content=payload[5:])
    original = httpx.AsyncClient
    monkeypatch.setattr(manager.httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(respond)))
    asyncio.run(manager._download('bekko'))
    assert manager.read_state('bekko')['status'] == 'installed'
    assert (path/'model.safetensors').read_bytes() == payload
    assert manager.valid_file(path/'model.safetensors',entry)
    (path/'model.safetensors').write_bytes(b'x'*len(payload))
    assert not manager.valid_file(path/'model.safetensors',entry)
    assert len(requests) == 1


def test_local_model_missing_is_explicit():
    with pytest.raises(ProviderError) as error:
        asyncio.run(Runtime().infer('qwen3-asr','transcription',{'source':'missing.wav'}))
    assert error.value.code == 'LOCAL_MODEL_NOT_INSTALLED'


def test_cancel_reaps_active_model_process(monkeypatch):
    import app.local_models.runtime as module
    monkeypatch.setattr(module,'read_state',lambda key:{'status':'installed'})
    monkeypatch.setattr(module,'interpreter',lambda:Path(sys.executable))
    class Input:
        def write(self, value):
            request = json.loads(value)
            assert request['config']['device'] == 'cpu'
        async def drain(self):
            pass
        def close(self):
            pass
    class Process:
        returncode = None
        stdin = Input()
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.killed = False
        def kill(self):
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
        async def wait(self):
            return self.returncode
    async def scenario():
        started = asyncio.Event()
        process = Process()
        async def spawn(*args, **kwargs):
            assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
            started.set()
            return process
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
        runtime = Runtime()
        task = asyncio.create_task(runtime.infer('qwen3-asr','transcription',{'source':'fixture.wav'}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and not runtime.active
    asyncio.run(scenario())
