"""Install optional model runtimes without depending on the developer's PATH."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Literal

from app.config import BACKEND_DIR, get_settings
from app.errors import ApiError
from app.local_models.process import ThreadedProcess

Device = Literal['cpu', 'cuda']
# Keep the existing CUDA location, including user-created junctions.
ROOT = get_settings().data_dir / 'model-runtime'
CPU_ROOT = get_settings().data_dir / 'model-runtime-cpu'
states = {device: {'status': 'unchecked', 'stage': '', 'cuda_available': None}
          for device in ('cpu', 'cuda')}
tasks = {}
observed_signatures = {}


def root(device: Device):
    return CPU_ROOT if device == 'cpu' else ROOT


def python_path(device: Device):
    return root(device) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def ready(device: Device = 'cuda'):
    return (root(device) / 'ready.json').is_file() and python_path(device).is_file()


def runtime_signature(device: Device):
    base = root(device)
    paths = [python_path(device)] + [base / 'Lib/site-packages' / package / '__init__.py'
        for package in ('torch', 'torchaudio', 'sentence_transformers', 'qwen_asr')]
    try:
        return tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
    except OSError:
        return None


def installed_interpreter(device: Device):
    # CUDA wheels also run on CPU. CPU wheels provide the advertised fallback.
    for candidate in (device, 'cpu' if device == 'cuda' else 'cuda'):
        if ready(candidate):
            return python_path(candidate)
    return None


def uv_path():
    bundled = BACKEND_DIR / 'tools' / ('uv.exe' if os.name == 'nt' else 'uv')
    if bundled.is_file():
        return str(bundled)
    # A packaged app must not run an unrelated executable from PATH.
    if not getattr(sys, 'frozen', False):
        found = shutil.which('uv')
        if found:
            return str(Path(found).resolve())
    raise ApiError(422, 'RUNTIME_INSTALLER_MISSING',
                   '运行环境安装器缺失，请重新安装完整的 OpenNexus 安装包。')


def windows_tool(relative):
    system_root = os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    path = Path(system_root) / 'System32' / relative if system_root else None
    if path is None or not path.is_absolute() or not path.is_file():
        raise ApiError(422, 'SYSTEM_TOOL_MISSING', 'Windows 系统工具缺失，无法安装运行环境。')
    return str(path)


async def status(device: Device = 'cuda'):
    state = states[device]
    if state['status'] == 'unchecked':
        state.update(status='checking', stage=f'检查已有 {device.upper()} 组件')
        tasks[device] = asyncio.create_task(run(False, device))
    elif state['status'] in {'failed', 'not_installed'} and not any(
            item['status'] == 'installing' for item in states.values()):
        signature = runtime_signature(device)
        if signature is not None and signature != observed_signatures.get(device):
            observed_signatures[device] = signature
            state.update(status='checking', stage=f'检测新放入的 {device.upper()} 环境', error=None)
            tasks[device] = asyncio.create_task(run(False, device))
    return {**state, 'device': device, 'install_path': str(root(device)), 'supported': os.name == 'nt',
            'ready': ready(device),
            'custom_interpreter': bool(os.getenv('APP_MODEL_PYTHON'))}


async def recheck(device: Device = 'cuda'):
    from app.local_models.runtime import runtime
    if device in tasks and not tasks[device].done():
        return await status(device)
    if any(item['status'] == 'installing' for item in states.values()):
        raise ApiError(409, 'RUNTIME_INSTALL_BUSY', '另一运行组件正在安装，请等待完成。')
    if runtime.active or runtime.waiters:
        raise ApiError(409, 'MODEL_IN_USE', '模型正在运行，请等待任务完成后重新检测。')
    states[device].update(status='checking', stage=f'重新检测 {device.upper()} 组件', error=None)
    tasks[device] = asyncio.create_task(run(False, device))
    return await status(device)


async def install(device: Device = 'cuda', source: Literal['domestic', 'official'] = 'domestic'):
    from app.local_models.runtime import runtime
    if os.name != 'nt':
        raise ApiError(422, 'PLATFORM_UNSUPPORTED', '当前平台暂不支持页面安装运行组件。')
    if device in tasks and not tasks[device].done():
        return await status(device)
    if any(item['status'] == 'installing' for item in states.values()):
        raise ApiError(409, 'RUNTIME_INSTALL_BUSY', '另一运行组件正在安装，请等待完成。')
    if runtime.active or runtime.waiters:
        raise ApiError(409, 'MODEL_IN_USE', '模型正在运行，请等待任务完成后安装。')
    if states[device]['status'] == 'installed' and ready(device):
        return await status(device)
    uv_path()
    windows_tool('WindowsPowerShell/v1.0/powershell.exe')
    states[device].update(status='installing', stage='准备独立模型环境', error=None, source=source)
    tasks[device] = asyncio.create_task(run(True, device, source))
    return await status(device)


async def execute(args, timeout, device: Device = 'cuda'):
    process = ThreadedProcess(args,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, limit=8192,
        creationflags=0x08000000 if os.name == 'nt' else 0)
    process.stdin.close()
    lines = []
    try:
        async with asyncio.timeout(timeout):
            while line := await process.stdout.readline():
                value = line.decode('utf-8', errors='replace').strip()
                stages = {'COMPONENT:python': '下载并安装独立 Python',
                          'COMPONENT:torch': f'下载并安装 PyTorch {device.upper()}',
                          'COMPONENT:dependencies': '安装模型依赖',
                          'COMPONENT:verify': '验证运行组件'}
                if value in stages:
                    states[device]['stage'] = stages[value]
                lines = (lines + [value])[-4:]
            await process.wait()
            if process.returncode:
                raise RuntimeError('component command failed')
            return lines
    finally:
        if process.returncode is None:
            if os.name == 'nt':
                await asyncio.to_thread(subprocess.run,
                    [windows_tool('taskkill.exe'), '/PID', str(process.process.pid), '/T', '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
            else:
                process.kill()
            await process.wait()
        await process.close()


async def run(download, device: Device = 'cuda', source: Literal['domestic', 'official'] = 'domestic'):
    marker = root(device) / 'ready.json'
    state = states[device]
    try:
        if download:
            marker.unlink(missing_ok=True)
            await execute([windows_tool('WindowsPowerShell/v1.0/powershell.exe'),
                '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                str(BACKEND_DIR / 'scripts/install-model-runtime.ps1'),
                '-Device', device, '-Source', source, '-RuntimeDirectory', str(root(device)),
                '-UvPath', uv_path(), '-PythonDirectory',
                str(root(device).parent / 'model-python'), '-QuietProgress'], 7200, device)
        python = python_path(device)
        if not python.is_file():
            marker.unlink(missing_ok=True)
            state.update(status='not_installed', stage='尚未安装', error=None)
            return
        expected_variant = 'cu128' if device == 'cuda' else 'cpu'
        result = await execute([str(python), '-c',
            'import json, torch, torchaudio, sentence_transformers, qwen_asr; '
            + f'assert torch.__version__ == "2.9.1+{expected_variant}"; '
            + f'assert torchaudio.__version__ == "2.9.1+{expected_variant}"; '
            + ('assert torch.version.cuda; ' if device == 'cuda' else '')
            + 'print(json.dumps({"torch":torch.__version__,"cuda_available":torch.cuda.is_available()}))'], 180, device)
        info = json.loads(result[-1])
        marker.write_text(json.dumps(info), encoding='utf-8')
        state.update(status='installed', stage='组件已安装', error=None, **info)
    except asyncio.CancelledError:
        marker.unlink(missing_ok=True)
        state.update(status='interrupted', stage='安装检查已中断，可重试')
        raise
    except Exception:
        marker.unlink(missing_ok=True)
        failed_stage = state['stage']
        hint = ('可切换下载源重试，或手动放入完整环境后点击“重新检测”'
                if download else '请确认该环境包含 torch/torchaudio 2.9.1 对应设备版本、sentence-transformers 和 qwen-asr')
        state.update(status='failed', stage='组件安装或验证失败',
            error=f'{failed_stage}失败。{hint}；另一设备的环境不会被修改。')
    finally:
        observed_signatures[device] = runtime_signature(device)


async def shutdown():
    pending = [task for task in tasks.values() if not task.done()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for state in states.values():
        if state['status'] in {'checking', 'interrupted'}:
            state['status'] = 'unchecked'
