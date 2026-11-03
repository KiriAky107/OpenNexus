import asyncio
import builtins
import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.local_models import runtime as module
from app.local_models import worker
from app.providers.base import ProviderError
from app.services import model_diagnostics


@pytest.mark.parametrize('kind', ['python', 'posix', 'windows'])
def test_system_memory_errors_do_not_reload_torch_or_expose_paths(monkeypatch, kind):
    error = MemoryError('private-media.wav') if kind == 'python' else OSError(
        errno.ENOMEM if kind == 'posix' else 0, 'private-media.wav')
    if kind == 'windows':
        error.winerror = 1455
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert name != 'torch', 'memory exhaustion must not import torch again'
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', checked_import)
    result = worker.inference_failure(error, {'_actual_device': 'cuda:0'})
    assert result['error_code'] == 'LOCAL_SYSTEM_MEMORY_INSUFFICIENT'
    assert 'private-media' not in json.dumps(result)


@pytest.mark.parametrize('kind,expected', [
    ('cuda_init', 'LOCAL_CUDA_INIT_FAILED'),
    ('cuda_oom', 'LOCAL_CUDA_OOM'),
    ('file_missing', 'LOCAL_INFERENCE_FAILED'),
    ('cpu_oom', 'LOCAL_INFERENCE_FAILED'),
])
def test_memory_classification_keeps_other_failure_contracts(monkeypatch, kind, expected):
    class CudaOom(Exception):
        pass

    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=CudaOom)))
    error = (worker.CudaInitializationError() if kind == 'cuda_init' else
             OSError(errno.ENOENT, 'private-path') if kind == 'file_missing' else CudaOom())
    result = worker.inference_failure(error, {'_actual_device': 'cpu' if kind == 'cpu_oom' else 'cuda:0'})
    assert result['error_code'] == expected
    assert 'private-path' not in json.dumps(result)


@pytest.mark.parametrize('threaded', [False, True])
@pytest.mark.parametrize('failure,expected', [
    ('budget', 'LOCAL_MODEL_MEMORY_LIMIT_EXCEEDED'),
    ('unknown', 'LOCAL_MODEL_PROCESS_FAILED'),
    ('memory', 'LOCAL_SYSTEM_MEMORY_INSUFFICIENT'),
    pytest.param('native', 'LOCAL_MODEL_NATIVE_CRASH', marks=pytest.mark.skipif(os.name != 'nt', reason='Windows exit status')),
])
def test_real_worker_failure_is_reaped_without_cpu_retry(monkeypatch, tmp_path, threaded, failure, expected):
    from app.local_models import process as process_module

    script = tmp_path / 'worker.py'
    script.write_text(
        'import json,sys,os\n'
        'request=json.load(sys.stdin)\n'
        'sys.stderr.write("private-media.wav: native detail")\n'
        + (
            f'sys.path.insert(0, {str(Path(worker.__file__).parent)!r})\n'
            'from worker import inference_failure\n'
            'error=OSError(0,"private-media.wav"); error.winerror=1455\n'
            'print(json.dumps(inference_failure(error,request)),flush=True)\n'
            if failure == 'memory' else
            'print(json.dumps({"result":"uncommitted-private-result"}),flush=True)\n'
            + ('import ctypes; ctypes.windll.kernel32.ExitProcess(0xC0000005)\n' if failure == 'native' else
               f'os._exit({75 if failure == "budget" else 1})\n')
        ), encoding='utf-8')
    monkeypatch.setattr(module, 'configuration', lambda: module.RuntimeConfig(device='cuda'))
    monkeypatch.setattr(module, 'read_state', lambda _: {'status': 'installed'})
    monkeypatch.setattr(module, 'interpreter', lambda *_: Path(sys.executable))
    original_async = asyncio.create_subprocess_exec
    original_threaded = process_module.ThreadedProcess
    processes = []

    async def spawn(*args, **kwargs):
        if threaded:
            raise NotImplementedError
        process = await original_async(sys.executable, '-B', str(script), **kwargs)
        processes.append(process)
        return process

    def spawn_threaded(args, **kwargs):
        process = original_threaded((sys.executable, '-B', str(script)), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(process_module, 'ThreadedProcess', spawn_threaded)
    runtime = module.Runtime()
    with pytest.raises(ProviderError) as error:
        asyncio.run(runtime.infer('qwen3-asr', 'transcription', {'source': 'private-media.wav'}))
    assert error.value.code == expected
    assert 'private-media' not in str(error.value)
    assert len(processes) == 1  # System pressure/native crashes must not trigger another model load.
    assert processes[0].returncode is not None
    assert not runtime.active and not runtime.active_files and not runtime.waiters
    if threaded:
        assert processes[0].process.stdin.closed and processes[0].process.stdout.closed
    record = model_diagnostics.recent()[-1]
    assert record['error_code'] == expected and record['status'] == 'failed'
    if failure != 'memory':
        assert record['worker_exit_code'] == str(processes[0].returncode)
    else:
        assert 'worker_exit_code' not in record
    assert 'private' not in json.dumps(record)


def test_diagnostics_keep_exit_code_but_drop_raw_exception_and_payload():
    record = model_diagnostics.record(worker_exit_code='3221225477',
        exception='private-path', stderr='private-content', payload='private-audio')
    assert record['worker_exit_code'] == '3221225477'
    assert set(record) == {'worker_exit_code', 'timestamp'}
