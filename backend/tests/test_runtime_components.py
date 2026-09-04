import asyncio
import json
import os

import pytest

from app.errors import ApiError
from app.local_models import components, runtime


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(components, 'ROOT', tmp_path / 'cuda')
    monkeypatch.setattr(components, 'state', {'status': 'unchecked', 'stage': '', 'cuda_available': None})
    monkeypatch.setattr(components, 'task', None)


def test_status_checks_without_installing_and_detects_existing_cuda(monkeypatch):
    python = components.ROOT / 'Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []
    async def execute(args, timeout):
        calls.append(args)
        return [json.dumps({'torch': '2.9.1+cu128', 'cuda_available': True})]
    monkeypatch.setattr(components, 'execute', execute)
    async def scenario():
        assert (await components.status())['status'] == 'checking'
        await components.task
        assert (await components.status())['status'] == 'installed'
        assert len(calls) == 1 and calls[0][0] == str(python)
        assert components.ready()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer')
def test_install_deduplicates_and_failure_can_retry(monkeypatch):
    monkeypatch.setattr(components.shutil, 'which', lambda name: 'uv.exe')
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(args, timeout):
            calls.append(args)
            entered.set()
            await release.wait()
            raise RuntimeError('private exception')
        monkeypatch.setattr(components, 'execute', execute)
        await components.install()
        await entered.wait()
        first = components.task
        await components.install()
        assert first is components.task
        release.set()
        await first
        assert components.state['status'] == 'failed'
        assert 'private exception' not in str(components.state)
        await components.install()
        await components.task
        assert len(calls) == 2 and '-RuntimeDirectory' in calls[0]
        assert not components.ready()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer')
def test_install_refuses_active_inference(monkeypatch):
    monkeypatch.setattr(runtime.runtime, 'active', {1: 'bekko'})
    async def scenario():
        with pytest.raises(ApiError) as exc:
            await components.install()
        assert exc.value.code == 'MODEL_IN_USE'
    asyncio.run(scenario())


def test_interpreter_keeps_cpu_default_and_respects_explicit_override(monkeypatch):
    monkeypatch.delenv('APP_MODEL_PYTHON', raising=False)
    python = components.ROOT / 'Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    (components.ROOT / 'ready.json').write_text('{}')
    monkeypatch.setattr(runtime, 'configuration', lambda: runtime.RuntimeConfig(device='cpu'))
    assert runtime.interpreter() != python
    # A queued attempt keeps its frozen device even after the saved setting changes.
    assert runtime.interpreter(runtime.RuntimeConfig(device='cuda')) == python
    assert runtime.interpreter(runtime.RuntimeConfig(device='cpu')) != python
    monkeypatch.setenv('APP_MODEL_PYTHON', 'explicit-python.exe')
    assert str(runtime.interpreter()) == 'explicit-python.exe'
