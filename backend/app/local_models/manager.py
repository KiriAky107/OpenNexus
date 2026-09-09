"""由用户显式触发、支持断点续传的下载；推理过程本身绝不下载权重。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.errors import ApiError
from app.local_models.catalog import CATALOG

_downloads: dict[tuple[str, str], asyncio.Task] = {}


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
    if state["status"] == "downloading" and task_key(key) not in _downloads:
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
    return {"items": [{**spec.public(), **read_state(key), "disk_bytes": disk_bytes(key)} for key, spec in CATALOG.items()]}


async def download(key):
    model_path(key)
    if task_key(key) not in _downloads and read_state(key)["status"] != "installed":
        write_state(key, {"status": "downloading", "downloaded_bytes": 0, "total_bytes": None})
        task = asyncio.create_task(_download(key))
        _downloads[task_key(key)] = task
        task.add_done_callback(lambda done: _downloads.pop(task_key(key), None))
    return read_state(key)


async def cancel_download(key):
    task = _downloads.get(task_key(key))
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    state = read_state(key)
    if state["status"] == "downloading":
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
    if spec.source == "huggingface":
        response = await client.get(f"https://huggingface.co/api/models/{spec.repository}/revision/{spec.revision}?blobs=true")
        response.raise_for_status()
        files = []
        for item in response.json()["siblings"]:
            name = item["rfilename"]
            if name.startswith(("onnx/", "openvino/", ".")) or not name.endswith((".json", ".txt", ".safetensors", ".md")):
                continue
            lfs = item.get("lfs") or {}
            files.append({"path": name, "size": item["size"], "hash": lfs.get("sha256") or item["blobId"],
                          "algorithm": "sha256" if lfs else "git-blob",
                          "url": f"https://huggingface.co/{spec.repository}/resolve/{spec.revision}/{quote(name)}"})
        return files
    response = await client.get(f"https://modelscope.cn/api/v1/models/{spec.repository}/repo/files",
                                params={"Revision": spec.revision, "Recursive": "true"})
    response.raise_for_status()
    return [{"path": f["Path"], "size": f["Size"], "hash": f["Sha256"], "algorithm": "sha256",
             "url": f"https://modelscope.cn/api/v1/models/{spec.repository}/repo?Revision={spec.revision}&FilePath={quote(f['Path'])}"}
            for f in response.json()["Data"]["Files"]
            if f["Path"] in {"configuration.json", "pretrained_eres2netv2.ckpt", "README.md"}]


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


async def _download(key):
    spec, root = CATALOG[key], model_path(key).resolve()
    state = {"status": "downloading", "downloaded_bytes": 0, "total_bytes": None}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            manifest = await _manifest(client, spec)
            if not manifest or not any(f["path"].endswith((".safetensors", ".ckpt")) for f in manifest):
                raise ValueError("Missing weights in model manifest")
            state["total_bytes"] = sum(f["size"] for f in manifest)
            root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(root).free < state["total_bytes"] + 100 * 1024 * 1024:
                raise ApiError(507, "MODEL_DISK_FULL", "Insufficient free disk space.")
            complete = 0
            for entry in manifest:
                path = (root / entry["path"]).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("Invalid model manifest path")
                path.parent.mkdir(parents=True, exist_ok=True)
                if await asyncio.to_thread(valid_file, path, entry):
                    complete += entry["size"]
                    continue
                partial = path.with_suffix(path.suffix + ".partial")
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= entry["size"]:
                    partial.unlink()
                    offset = 0
                async with client.stream("GET", entry["url"], headers={"Range": f"bytes={offset}-"} if offset else {}) as response:
                    response.raise_for_status()
                    if offset and response.status_code != 206:
                        offset = 0
                    if response.status_code == 206 and not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
                        raise ValueError("Invalid download range")
                    with partial.open("ab" if offset else "wb") as stream:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            offset += len(chunk)
                            if offset > entry["size"]:
                                raise ValueError("Download exceeds manifest size")
                            stream.write(chunk)
                            state["downloaded_bytes"] = complete + offset
                            write_state(key, state)
                if not await asyncio.to_thread(valid_file, partial, entry):
                    partial.unlink(missing_ok=True)
                    raise ApiError(422, "MODEL_CHECKSUM_FAILED", "Model file checksum did not match.")
                partial.replace(path)
                complete += entry["size"]
            (root / "verified-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            state.update(status="installed", downloaded_bytes=complete)
    except asyncio.CancelledError:
        state.update(status="interrupted", error_code="DOWNLOAD_CANCELLED")
    except Exception as exc:
        state.update(status="failed", error_code=exc.code if isinstance(exc, ApiError) else "MODEL_DOWNLOAD_FAILED")
    write_state(key, state)
