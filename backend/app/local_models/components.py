"""用户触发在 Windows 上安装固定的可选 CUDA 运行时。"""
import asyncio
import json
import os
import shutil
import subprocess

from app.config import BACKEND_DIR, get_settings
from app.errors import ApiError
from app.local_models.process import ThreadedProcess

# 模型运行环境会在安装与升级时写入大量文件，必须位于应用数据目录，
# 不能写入受完整性清单保护的 Core 发布目录。
DEFAULT_ROOT = get_settings().data_dir / 'model-runtime'
ROOT = DEFAULT_ROOT
state = {'status': 'unchecked', 'stage': '', 'cuda_available': None}
task = None


def ready():
    return (ROOT / 'ready.json').is_file() and (ROOT / 'Scripts/python.exe').is_file()


async def status():
    global task
    if state['status'] == 'unchecked':
        state.update(status='checking', stage='检查已有 CUDA 组件')
        task = asyncio.create_task(run(False))
    return {**state, 'supported': os.name == 'nt', 'custom_interpreter': bool(os.getenv('APP_MODEL_PYTHON'))}


async def install():
    global task
    from app.local_models.runtime import runtime
    if os.name != 'nt':
        raise ApiError(422, 'PLATFORM_UNSUPPORTED', '此安装入口目前支持 Windows。')
    if task is not None and not task.done():
        return await status()
    if runtime.active or runtime.waiters:
        raise ApiError(409, 'MODEL_IN_USE', '请等待本地模型任务结束后再安装组件。')
    if state['status'] == 'installed':
        return await status()
    if not shutil.which('uv'):
        raise ApiError(422, 'UV_NOT_INSTALLED', '后端未找到 uv，请先安装 uv 并重启后端。')
    state.update(status='installing', stage='准备独立 CUDA 环境', error=None)
    task = asyncio.create_task(run(True))
    return await status()


async def execute(args, timeout):
    process = ThreadedProcess(args, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                              limit=8192, creationflags=0x08000000 if os.name == 'nt' else 0)
    process.stdin.close()
    lines = []
    try:
        async with asyncio.timeout(timeout):
            while line := await process.stdout.readline():
                value = line.decode('utf-8', errors='replace').strip()
                stages = {'COMPONENT:torch': '下载并安装 PyTorch CUDA（约 3 GB）',
                          'COMPONENT:dependencies': '安装模型依赖', 'COMPONENT:verify': '验证运行组件'}
                if value in stages:
                    state['stage'] = stages[value]
                lines = (lines + [value])[-4:]
            await process.wait()
            if process.returncode:
                raise RuntimeError('component command failed')
            return lines
    finally:
        if process.returncode is None:
            if os.name == 'nt':
                await asyncio.to_thread(subprocess.run, ['taskkill', '/PID', str(process.process.pid), '/T', '/F'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=0x08000000)
            else:
                process.kill()
            await process.wait()
        await process.close()


async def run(download):
    marker = ROOT / 'ready.json'
    try:
        if download:
            marker.unlink(missing_ok=True)
            await execute(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
                           str(BACKEND_DIR / 'scripts/install-model-runtime.ps1'), '-Device', 'cuda',
                           '-RuntimeDirectory', str(ROOT), '-QuietProgress'], 7200)
        python = ROOT / 'Scripts/python.exe'
        if not python.is_file():
            state.update(status='not_installed', stage='尚未安装')
            return
        result = await execute([str(python), '-c',
            'import json, torch, torchaudio, sentence_transformers, qwen_asr; '
            'assert torch.version.cuda; '
            'print(json.dumps({"torch":torch.__version__,"cuda_available":torch.cuda.is_available()}))'], 180)
        info = json.loads(result[-1])
        marker.write_text(json.dumps(info), encoding='utf-8')
        state.update(status='installed', stage='组件已安装', error=None, **info)
    except asyncio.CancelledError:
        marker.unlink(missing_ok=True)
        state.update(status='interrupted', stage='安装检查已中断，可重试')
        raise
    except Exception:
        marker.unlink(missing_ok=True)
        state.update(status='failed', stage='组件安装或验证失败',
                     error='请检查网络、磁盘空间和 uv；可以重试。CPU 环境不受影响。')


async def shutdown():
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if state['status'] in {'checking', 'interrupted'}:
        state['status'] = 'unchecked'
