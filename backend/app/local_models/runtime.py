"""Bounded, cancellable model subprocesses with CPU as the default device."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import BACKEND_DIR
from app.database.db import connect
from app.errors import ApiError
from app.local_models.catalog import CATALOG
from app.local_models.manager import model_path, read_state
from app.providers.base import ProviderError


class RuntimeConfig(BaseModel):
    device: Literal["cpu", "cuda"] = "cpu"
    cpu_threads: int = Field(default=2, ge=1, le=32)
    memory_limit_mb: int = Field(default=8192, ge=1024, le=131072)
    gpu_memory_limit_mb: int = Field(default=4096, ge=512, le=65536)
    timeout_seconds: int = Field(default=1800, ge=30, le=14400)
    embedding_model: Literal["bekko", "granite"] = "bekko"
    version: int = Field(default=1, ge=1)


runtime_context = ContextVar("runtime_config", default=None)
runtime_progress = ContextVar("runtime_progress", default=None)


def configuration():
    if runtime_context.get() is not None:
        return runtime_context.get()
    with closing(connect()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS local_runtime_config (id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL)")
        row = conn.execute("SELECT config_json FROM local_runtime_config WHERE id=1").fetchone()
    return RuntimeConfig.model_validate_json(row[0]) if row else RuntimeConfig()


def configure(request):
    from app.database.db import transaction
    configuration()
    with closing(connect()) as conn, transaction(conn):
        row = conn.execute("SELECT config_json FROM local_runtime_config WHERE id=1").fetchone()
        previous = RuntimeConfig.model_validate_json(row[0]) if row else RuntimeConfig()
        if request.version != previous.version:
            raise ApiError(409, "VERSION_CONFLICT", "Local runtime settings changed; reload first.")
        request = request.model_copy(update={"version": request.version + 1})
        conn.execute("INSERT OR REPLACE INTO local_runtime_config VALUES (1,?)", (request.model_dump_json(),))
    return request


def interpreter():
    return Path(os.getenv("APP_MODEL_PYTHON", str(BACKEND_DIR / ".venv-models" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))))


class Runtime:
    def __init__(self):
        self.active = {}
        self.active_files = {}
        self.waiters = []
        self.counter = 0
        self.diagnostics = []

    def in_use(self, key):
        return key in self.active.values()

    def media_in_use(self, path):
        target = str(Path(path).resolve())
        return any(target in paths for paths in self.active_files.values())

    async def infer(self, key, operation, payload, *, priority=10):
        if read_state(key)["status"] != "installed":
            raise ProviderError("LOCAL_MODEL_NOT_INSTALLED", "请先在模型配置中下载本地模型。")
        if not interpreter().is_file():
            raise ProviderError("LOCAL_RUNTIME_NOT_INSTALLED", "请先运行本地模型 CPU/CUDA 安装脚本。")
        config = configuration()
        self.counter += 1
        ticket = (priority, self.counter)
        self.waiters.append(ticket)
        process = None
        attempt = None
        try:
            # One resident model at a time prevents overlapping CPU/GPU allocations.
            while self.active or ticket != min(self.waiters):
                await asyncio.sleep(0.05)
            self.waiters.remove(ticket)
            self.active[ticket] = key
            self.active_files[ticket] = {str(Path(payload[name]).resolve()) for name in ("source", "reference") if payload.get(name)}
            # Deletion may have occurred while this request was queued.
            if read_state(key)["status"] != "installed":
                raise ProviderError("LOCAL_MODEL_NOT_INSTALLED", "模型文件已被删除。")
            from app.services.usage_service import UsageAttempt
            attempt = UsageAttempt("local-models", CATALOG[key].repository, "local", operation, source="local")
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": str(config.cpu_threads),
                   "PYTHONIOENCODING": "utf-8"}
            process = await asyncio.create_subprocess_exec(str(interpreter()), str(Path(__file__).with_name("worker.py")),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=env, limit=16 * 1024 * 1024, **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            request = {"key": key, "operation": operation, "model_path": str(model_path(key).resolve()),
                       "config": config.model_dump(), "payload": payload}
            async def receive():
                process.stdin.write(json.dumps(request).encode())
                await process.stdin.drain()
                process.stdin.close()
                final = None
                while line := await process.stdout.readline():
                    if len(line) > 16 * 1024 * 1024:
                        raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "本地模型输出超限。")
                    message = json.loads(line)
                    if "progress" in message:
                        callback = runtime_progress.get()
                        if callback:
                            callback(message)
                    else:
                        final = message
                await process.wait()
                return final
            try:
                result = await asyncio.wait_for(receive(), config.timeout_seconds)
            except TimeoutError as exc:
                raise ProviderError("LOCAL_MODEL_TIMEOUT", "本地模型处理超时。") from exc
            if process.returncode != 0:
                raise ProviderError("LOCAL_MODEL_PROCESS_FAILED", "本地模型进程退出，请检查依赖与资源预算。")
            if not isinstance(result, dict):
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "本地模型进程未返回有效结果。")
            if "error_code" in result:
                raise ProviderError(result["error_code"], result.get("message", "本地推理失败。"))
            attempt.observe(result)
            attempt.completed = True
            self.diagnostics.append({"model": CATALOG[key].repository, "revision": CATALOG[key].revision,
                                     **result.get("diagnostics", {})})
            self.diagnostics = self.diagnostics[-100:]
            return result["result"]
        finally:
            if ticket in self.waiters:
                self.waiters.remove(ticket)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            self.active.pop(ticket, None)
            self.active_files.pop(ticket, None)
            if attempt:
                attempt.persist()


runtime = Runtime()


class LocalEmbedding:
    dim = 384

    @property
    def model_id(self):
        spec = CATALOG[configuration().embedding_model]
        return f"{spec.repository}@{spec.revision}"

    @property
    def version(self):
        return CATALOG[configuration().embedding_model].revision

    @property
    def available(self):
        return read_state(configuration().embedding_model)["status"] == "installed" and interpreter().is_file()

    async def embed_documents(self, texts):
        return await runtime.infer(configuration().embedding_model, "embedding", {"texts": texts}, priority=0)

    async def embed_query(self, query):
        return (await self.embed_documents([query]))[0]


class LocalSpeech:
    @property
    def available(self):
        return self.available_for("transcription")

    def available_for(self, capability):
        key = "qwen3-asr" if capability == "transcription" else "eres2netv2"
        return read_state(key)["status"] == "installed" and interpreter().is_file()

    async def transcribe(self, source, language):
        from app.providers.routing import RoutedTranscript
        from app.contracts import TranscriptSegment
        result = await runtime.infer("qwen3-asr", "transcription", {"source": str(source.resolve()), "language": language})
        return RoutedTranscript(text=result["text"], source="local",
                                segments=[TranscriptSegment(**s) for s in result["segments"]])

    async def match(self, source, reference):
        result = await runtime.infer("eres2netv2", "speaker_matching",
            {"source": str(source.resolve()), "reference": str(reference.resolve())}, priority=0)
        return result["score"]
