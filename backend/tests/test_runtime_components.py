import asyncio
import json
import os
from pathlib import Path

import pytest

from app.errors import ApiError
from app.local_models import components, runtime


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(components, 'ROOT', tmp_path / 'cuda')
    monkeypatch.setattr(components, 'CPU_ROOT', tmp_path / 'cpu')
    monkeypatch.setattr(components, 'states', {
        device: {'status': 'unchecked', 'stage': '', 'cuda_available': None}
        for device in ('cpu', 'cuda')})
    monkeypatch.setattr(components, 'tasks', {})
    monkeypatch.delenv('APP_MODEL_PYTHON', raising=False)


def make_runtime(device):
    python = components.python_path(device)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    (components.root(device) / 'ready.json').write_text('{}')
    return python


@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_status_checks_without_installing(monkeypatch, device):
    python = make_runtime(device)
    calls = []
    async def execute(args, timeout, device):
        calls.append(args)
        return [json.dumps({'torch': '2.9.1', 'cuda_available': device == 'cuda'})]
    monkeypatch.setattr(components, 'execute', execute)
    async def scenario():
        assert (await components.status(device))['status'] == 'checking'
        await components.tasks[device]
        assert (await components.status(device))['status'] == 'installed'
        assert len(calls) == 1 and calls[0][0] == str(python)
        assert ('assert torch.version.cuda' in calls[0][-1]) == (device == 'cuda')
        assert components.ready(device)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer')
def test_install_deduplicates_and_failure_can_retry_without_breaking_cpu(monkeypatch):
    make_runtime('cpu')
    monkeypatch.setattr(components, 'uv_path', lambda: 'C:/tools/uv.exe')
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(args, timeout, device):
            calls.append(args)
            entered.set()
            await release.wait()
            raise RuntimeError('private exception')
        monkeypatch.setattr(components, 'execute', execute)
        await components.install()
        await entered.wait()
        first = components.tasks['cuda']
        await components.install()
        assert first is components.tasks['cuda']
        with pytest.raises(ApiError, match='另一运行组件'):
            await components.install('cpu')
        release.set()
        await first
        assert components.states['cuda']['status'] == 'failed'
        assert 'private exception' not in str(components.states)
        await components.install()
        await components.tasks['cuda']
        assert len(calls) == 2
        assert '-RuntimeDirectory' in calls[0] and '-UvPath' in calls[0]
        assert '-PythonDirectory' in calls[0] and '-QuietProgress' in calls[0]
        assert Path(calls[0][0]).is_absolute()
        assert not components.ready()
        assert components.ready('cpu')
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer')
def test_install_refuses_active_inference(monkeypatch):
    monkeypatch.setattr(runtime.runtime, 'active', {1: 'bekko'})
    async def scenario():
        with pytest.raises(ApiError) as exc:
            await components.install()
        assert exc.value.code == 'MODEL_IN_USE'
    asyncio.run(scenario())


def test_interpreter_uses_persistent_cpu_and_cuda_with_override(monkeypatch):
    cuda = make_runtime('cuda')
    assert runtime.interpreter(runtime.RuntimeConfig(device='cpu')) == cuda
    assert runtime.interpreter(runtime.RuntimeConfig(device='cuda')) == cuda
    cpu = make_runtime('cpu')
    assert runtime.interpreter(runtime.RuntimeConfig(device='cpu')) == cpu
    assert runtime.interpreter(runtime.RuntimeConfig(device='cuda')) == cuda
    cuda.unlink()
    assert runtime.interpreter(runtime.RuntimeConfig(device='cuda')) == cpu
    monkeypatch.setenv('APP_MODEL_PYTHON', 'explicit-python.exe')
    assert str(runtime.interpreter(runtime.RuntimeConfig())) == 'explicit-python.exe'


def test_missing_marker_or_broken_link_is_not_ready():
    python = make_runtime('cpu')
    (components.root('cpu') / 'ready.json').unlink()
    assert not components.ready('cpu')
    assert components.installed_interpreter('cpu') is None
    (components.root('cpu') / 'ready.json').write_text('{}')
    python.unlink()
    assert not components.ready('cpu')


def test_frozen_interpreter_never_uses_development_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, 'BACKEND_DIR', tmp_path / 'checkout')
    development = runtime.BACKEND_DIR / '.venv-models' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    development.parent.mkdir(parents=True)
    development.touch()
    assert runtime.interpreter(runtime.RuntimeConfig()) == components.python_path('cpu')
    assert not runtime.runtime_installed(runtime.RuntimeConfig())
    make_runtime('cpu')
    assert runtime.runtime_installed(runtime.RuntimeConfig())
    (components.CPU_ROOT / 'ready.json').unlink()
    assert not runtime.runtime_installed(runtime.RuntimeConfig())


def test_windows_installer_is_ascii_and_uses_fixed_native_extensions():
    script = (Path(__file__).parents[1] / 'scripts/install-model-runtime.ps1').read_text(encoding='ascii')
    assert "$env:PATHEXT = '.COM;.EXE;.BAT;.CMD'" in script
    assert '[string[]]$uvOptions' in script
    assert "$env:UV_LINK_MODE = 'copy'" in script


def test_bundled_uv_is_found_without_path(monkeypatch, tmp_path):
    monkeypatch.setattr(components, 'BACKEND_DIR', tmp_path)
    monkeypatch.setattr(components.sys, 'frozen', True, raising=False)
    monkeypatch.delenv('PATH', raising=False)
    installer = tmp_path / 'tools' / ('uv.exe' if os.name == 'nt' else 'uv')
    installer.parent.mkdir()
    installer.touch()
    assert components.uv_path() == str(installer)
    installer.unlink()
    monkeypatch.setattr(components.shutil, 'which', lambda _: 'untrusted-uv')
    with pytest.raises(ApiError) as exc:
        components.uv_path()
    assert exc.value.code == 'RUNTIME_INSTALLER_MISSING'


@pytest.mark.skipif(os.name != 'nt', reason='Windows tools')
def test_system_tools_are_absolute_without_path(monkeypatch):
    monkeypatch.delenv('PATH', raising=False)
    assert Path(components.windows_tool('WindowsPowerShell/v1.0/powershell.exe')).is_file()
    assert Path(components.windows_tool('taskkill.exe')).is_file()


def test_cancel_clears_incomplete_marker_and_retries_check(monkeypatch):
    make_runtime('cpu')
    async def execute(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(components, 'execute', execute)
    async def scenario():
        await components.status('cpu')
        await components.shutdown()
        assert components.states['cpu']['status'] == 'unchecked'
    asyncio.run(scenario())


def test_stale_marker_is_removed_when_python_is_missing():
    components.CPU_ROOT.mkdir()
    (components.CPU_ROOT / 'ready.json').write_text('{}')
    asyncio.run(components.run(False, 'cpu'))
    assert components.states['cpu']['status'] == 'not_installed'
    assert not (components.CPU_ROOT / 'ready.json').exists()
