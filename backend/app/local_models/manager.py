"""由用户显式触发、支持断点续传的下载；推理过程本身绝不下载权重。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import quote
from typing import Literal

import httpx

from app.config import get_settings
from app.errors import ApiError
from app.local_models.catalog import CATALOG
from app.local_models.pinned_manifests import PINNED_MANIFESTS

_downloads: dict[tuple[str, str], asyncio.Task] = {}
_observed_import_signatures: dict[tuple[str, str], tuple] = {}
DownloadSource = Literal['auto', 'domestic', 'official']


def model_path(key: str) -> Path:
    if key not in CATALOG:
        raise ApiError(404, "MODEL_NOT_FOUND", "Unknown local model.")
    return get_settings().data_dir / "models" / key / CATALOG[key].revision


def state_path(key):
    return model_path(key) / "install-state.json"


def read_state(key):
    try:
        state = json.loads(state_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"status": "not_installed", "downloaded_bytes": 0, "total_bytes": None}
    if state["status"] in {"downloading", "verifying"} and task_key(key) not in _downloads:
        state.update(status="interrupted", error_code="DOWNLOAD_INTERRUPTED")
    return state


def write_state(key, state):
    path = state_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def task_key(key):
    return str(model_path(key)), key


def disk_bytes(key):
    total = 0
    try:
        root = model_path(key).resolve()
        for path in root.rglob("*"):
            if not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root):
                total += path.stat().st_size
    except OSError:
        return None
    return total


def describe():
    return {"items": [{**spec.public(), **read_state(key), "disk_bytes": disk_bytes(key),
                       "install_path": str(model_path(key)),
                       "download_sources": available_sources(spec)} for key, spec in CATALOG.items()]}


def available_sources(spec):
    return ['domestic', 'official'] if spec.mirror_repository else (
        ['domestic'] if spec.source == 'modelscope' else ['official'])


def chosen_source(spec, requested: DownloadSource):
    source = available_sources(spec)[0] if requested == 'auto' else requested
    if source not in available_sources(spec):
        raise ApiError(422, 'MODEL_SOURCE_UNAVAILABLE',
                       '该模型没有经过校验的对应下载源；可选择其他来源或复制文件后使用本地校验。')
    return source


async def download(key, source: DownloadSource = 'auto'):
    model_path(key)
    chosen_source(CATALOG[key], source)
    if task_key(key) not in _downloads and read_state(key)["status"] != "installed":
        write_state(key, {"status": "downloading", "downloaded_bytes": 0, "total_bytes": None})
        task = asyncio.create_task(_download(key, source))
        _downloads[task_key(key)] = task
        task.add_done_callback(lambda done: _downloads.pop(task_key(key), None))
    return read_state(key)


async def verify(key):
    from app.local_models.runtime import runtime
    model_path(key)
    if runtime.in_use(key):
        raise ApiError(409, 'MODEL_IN_USE', '模型正在使用中，请等待任务结束后校验。')
    if task_key(key) not in _downloads:
        write_state(key, {"status": "verifying", "downloaded_bytes": 0, "total_bytes": None})
        task = asyncio.create_task(_verify(key))
        _downloads[task_key(key)] = task
        task.add_done_callback(lambda done: _downloads.pop(task_key(key), None))
    return read_state(key)


def local_import_signature(key):
    root = model_path(key).resolve()
    signature = []
    try:
        for entry in PINNED_MANIFESTS[key]:
            path = safe_model_file(root, entry['path'])
            stat = path.stat()
            if not path.is_file() or stat.st_size != entry['size']:
                return None
            signature.append((entry['path'], stat.st_size, stat.st_mtime_ns))
    except (OSError, ApiError):
        return None
    return tuple(signature)


async def detect_manual_models():
    """Auto-verify complete-looking local imports once per file signature."""
    from app.local_models.runtime import runtime
    for key in CATALOG:
        if task_key(key) in _downloads or runtime.in_use(key):
            continue
        if read_state(key)['status'] == 'installed':
            continue
        signature = local_import_signature(key)
        if signature is None or _observed_import_signatures.get(task_key(key)) == signature:
            continue
        _observed_import_signatures[task_key(key)] = signature
        await verify(key)


async def cancel_download(key):
    task = _downloads.get(task_key(key))
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    state = read_state(key)
    if state["status"] in {"downloading", "verifying"}:
        state["status"] = "interrupted"
        write_state(key, state)
    return state


async def delete(key):
    from app.local_models.runtime import runtime
    if runtime.in_use(key):
        raise ApiError(409, "MODEL_IN_USE", "Model is serving an active request.")
    await cancel_download(key)
    path = model_path(key).resolve()
    root = (get_settings().data_dir / "models").resolve()
    if not path.is_relative_to(root) or path == root:
        raise ApiError(400, "INVALID_MODEL_PATH", "Model path escapes storage.")
    if path.exists():
        shutil.rmtree(path)
    return read_state(key)


async def _manifest(client, spec):
    # The reviewed revision's hashes ship with the app. Fetching a manifest
    # must not require access to Hugging Face, including during local import.
    return [dict(entry) for entry in PINNED_MANIFESTS[spec.key]]


def file_url(spec, entry, source):
    if 'url' in entry:  # Test fixtures and reviewed legacy manifests.
        return entry['url']
    name = quote(entry['path'])
    if source == 'domestic':
        repository = spec.mirror_repository or spec.repository
        if spec.mirror_source == 'hf-mirror':
            return f'https://hf-mirror.net/{repository}/resolve/{spec.revision}/{name}'
        revision = spec.revision if spec.source == 'modelscope' else 'master'
        return (f'https://modelscope.cn/api/v1/models/{repository}/repo?'
                f'Revision={revision}&FilePath={name}')
    return f'https://huggingface.co/{spec.repository}/resolve/{spec.revision}/{name}'


def valid_file(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    digest = hashlib.sha256() if entry["algorithm"] == "sha256" else hashlib.sha1()
    if entry["algorithm"] == "git-blob":
        digest.update(f"blob {entry['size']}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == entry["hash"]


def safe_model_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ApiError(422, 'INVALID_MODEL_PATH', 'Model manifest path escapes storage.')
    return path


async def _verify(key):
    root = model_path(key).resolve()
    manifest = PINNED_MANIFESTS[key]
    state = {'status': 'verifying', 'downloaded_bytes': 0,
             'total_bytes': sum(entry['size'] for entry in manifest)}
    try:
        complete = 0
        missing = 0
        for entry in manifest:
            if await asyncio.to_thread(valid_file, safe_model_file(root, entry['path']), entry):
                complete += entry['size']
            else:
                missing += 1
            state['downloaded_bytes'] = complete
            write_state(key, state)
        if missing:
            state.update(status='failed', error_code='MODEL_FILES_INCOMPLETE',
                         error_detail=f'{missing} 个文件缺失或校验不通过；请放入固定版本文件后重新检测。')
        else:
            (root / 'verified-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            state.update(status='installed', downloaded_bytes=complete)
    except asyncio.CancelledError:
        state.update(status='interrupted', error_code='VERIFY_CANCELLED')
    except Exception:
        state.update(status='failed', error_code='MODEL_VERIFY_FAILED')
    write_state(key, state)


def failure_details(exc):
    if isinstance(exc, ApiError):
        return exc.code, exc.message
    if isinstance(exc, httpx.HTTPStatusError):
        return 'MODEL_SOURCE_HTTP_ERROR', f'下载源返回 HTTP {exc.response.status_code}。'
    if isinstance(exc, httpx.TimeoutException):
        return 'MODEL_SOURCE_TIMEOUT', '下载源连接或传输超时。'
    if isinstance(exc, httpx.TransportError):
        return 'MODEL_SOURCE_NETWORK_ERROR', '无法连接下载源或传输中断。'
    return 'MODEL_DOWNLOAD_FAILED', '下载未完成，请查看失败阶段或尝试另一来源。'


async def _download(key, requested_source: DownloadSource = 'auto'):
    spec, root = CATALOG[key], model_path(key).resolve()
    source = chosen_source(spec, requested_source)
    state = {"status": "downloading", "downloaded_bytes": 0, "total_bytes": None,
             "source": source, "stage": "校验已有文件"}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            manifest = await _manifest(client, spec)
            if not manifest or not any(f["path"].endswith((".safetensors", ".ckpt")) for f in manifest):
                raise ValueError("Missing weights in pinned model manifest")
            state["total_bytes"] = sum(f["size"] for f in manifest)
            root.mkdir(parents=True, exist_ok=True)
            complete = 0
            missing = []
            for entry in manifest:
                path = safe_model_file(root, entry['path'])
                if await asyncio.to_thread(valid_file, path, entry):
                    complete += entry['size']
                else:
                    missing.append((entry, path))
            state['downloaded_bytes'] = complete
            write_state(key, state)
            remaining = 0
            for entry, path in missing:
                partial = path.with_suffix(path.suffix + '.partial')
                offset = partial.stat().st_size if partial.exists() else 0
                reusable = 0 < offset < entry['size'] and not (
                    source == 'domestic' and entry['size'] < 32 * 1024 * 1024)
                remaining += entry['size'] - offset if reusable else entry['size']
            if remaining and shutil.disk_usage(root).free < remaining + 100 * 1024 * 1024:
                raise ApiError(507, 'MODEL_DISK_FULL', '模型目录磁盘空间不足。')
            for entry, path in missing:
                state['stage'] = f'下载 {entry["path"]}'
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(path.suffix + '.partial')
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= entry['size'] or (source == 'domestic' and entry['size'] < 32 * 1024 * 1024):
                    partial.unlink(missing_ok=True)
                    offset = 0
                url = file_url(spec, entry, source)
                async with client.stream('GET', url, headers={'Range': f'bytes={offset}-'} if offset else {}) as response:
                    response.raise_for_status()
                    if offset and response.status_code != 206:
                        # Some mirrors ignore Range. A full response can still be used.
                        offset = 0
                    if response.status_code == 206 and not response.headers.get('content-range', '').startswith(f'bytes {offset}-'):
                        raise ApiError(502, 'MODEL_RANGE_INVALID', '下载源返回了错误的续传范围。')
                    with partial.open('ab' if offset else 'wb') as stream:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            offset += len(chunk)
                            if offset > entry['size']:
                                raise ApiError(502, 'MODEL_SIZE_INVALID', '下载源返回的文件超过固定版本大小。')
                            stream.write(chunk)
                            state['downloaded_bytes'] = complete + offset
                            write_state(key, state)
                state['stage'] = f'校验 {entry["path"]}'
                if not await asyncio.to_thread(valid_file, partial, entry):
                    partial.unlink(missing_ok=True)
                    raise ApiError(422, 'MODEL_CHECKSUM_FAILED', '文件校验未通过，已丢弃不匹配内容。')
                partial.replace(path)
                complete += entry['size']
            (root / 'verified-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            state.update(status='installed', downloaded_bytes=complete, stage='全部文件已校验')
    except asyncio.CancelledError:
        state.update(status='interrupted', error_code='DOWNLOAD_CANCELLED')
    except Exception as exc:
        code, detail = failure_details(exc)
        state.update(status='failed', error_code=code, error_detail=detail)
    write_state(key, state)
