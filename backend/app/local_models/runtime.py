"""有界、可取消的模型子流程，以 CPU 作为默认设备。"""
from __future__ import annotations

import asyncio
import json
import os
import time
import hashlib
from collections import OrderedDict
from contextlib import closing
from contextvars import ContextVar
from functools import wraps
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
embedding_priority = ContextVar("embedding_priority", default=0)


def background_embeddings(operation):
    @wraps(operation)
    async def wrapped(*args, **kwargs):
        token = embedding_priority.set(20)
        try:
            return await operation(*args, **kwargs)
        finally:
            embedding_priority.reset(token)
    return wrapped


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


def interpreter(config=None):
    from app.local_models import components
    requested_device = (config or configuration()).device
    if not os.getenv("APP_MODEL_PYTHON") and requested_device == "cuda" and components.ready():
        return components.ROOT / "Scripts/python.exe"
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
        from app.services import model_diagnostics
        config = configuration().model_copy(deep=True)
        self.counter += 1
        ticket = (priority, self.counter)
        self.waiters.append(ticket)
        queued_at = time.monotonic()
        reason = None
        from app.services.usage_service import usage_context
        from uuid import uuid4
        context = dict(usage_context.get() or {})
        context.setdefault("request_id", uuid4().hex)
        usage_token = usage_context.set(context)
        try:
            while self.active or ticket != min(self.waiters):
                await asyncio.sleep(0.05)
            self.waiters.remove(ticket)
            self.active[ticket] = key
            self.active_files[ticket] = {str(Path(payload[name]).resolve()) for name in ("source", "reference") if payload.get(name)}
            queue_seconds = time.monotonic() - queued_at
            # 用 CPU 进程替换失败的 CUDA 进程时，继续占用原有资源配额。
            for device in (["cuda", "cpu"] if config.device == "cuda" else ["cpu"]):
                started = time.monotonic()
                diagnostics = dict(model=CATALOG[key].repository, revision=CATALOG[key].revision,
                    operation=operation, source="local", requested_device=config.device,
                    attempted_device=device, queue_seconds=queue_seconds, fallback_reason=reason, request_id=context["request_id"])
                try:
                    result = await self._execute(key, operation, payload, config.model_copy(update={"device": device}), diagnostics)
                    diagnostics.update(result.get("diagnostics", {}))
                    diagnostics.update(requested_device=config.device, status="completed")
                    if reason:
                        diagnostics["fallback_reason"] = reason
                    return result["result"]
                except asyncio.CancelledError:
                    diagnostics.update(status="cancelled", error_code="LOCAL_MODEL_CANCELLED")
                    raise
                except ProviderError as exc:
                    diagnostics.update(status="failed", error_code=exc.code)
                    if device == "cuda" and exc.code in {"LOCAL_CUDA_INIT_FAILED", "LOCAL_CUDA_OOM"}:
                        reason = exc.code
                        callback = runtime_progress.get()
                        if callback:
                            callback({"reset": True, "progress": 0})
                        continue
                    raise
                except Exception:
                    diagnostics.update(status="failed", error_code="LOCAL_MODEL_INVALID_RESPONSE")
                    raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "本地模型返回无效数据。") from None
                finally:
                    diagnostics["requested_device"] = config.device
                    diagnostics["elapsed_seconds"] = time.monotonic() - started
                    self.diagnostics.append(model_diagnostics.record(**diagnostics))
                    self.diagnostics = self.diagnostics[-100:]
        except asyncio.CancelledError:
            if ticket not in self.active:
                model_diagnostics.record(model=CATALOG[key].repository, operation=operation,
                    source="local", status="cancelled", error_code="LOCAL_QUEUE_CANCELLED",
                    requested_device=config.device, queue_seconds=time.monotonic() - queued_at)
            raise
        finally:
            if ticket in self.waiters:
                self.waiters.remove(ticket)
            self.active.pop(ticket, None)
            self.active_files.pop(ticket, None)
            usage_context.reset(usage_token)

    async def _execute(self, key, operation, payload, config, diagnostics):
        if read_state(key)["status"] != "installed":
            raise ProviderError("LOCAL_MODEL_NOT_INSTALLED", "请先下载本地模型。")
        executable = interpreter(config)
        if not executable.is_file():
            raise ProviderError("LOCAL_RUNTIME_NOT_INSTALLED", "请先安装本地模型运行环境。")
        from app.services.usage_service import UsageAttempt
        attempt = UsageAttempt("local-models", CATALOG[key].repository, "local", operation, source="local")
        diagnostics.update(attempt_id=attempt.attempt_id, request_id=attempt.request_id)
        process = None
        try:
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": str(config.cpu_threads),
                   "PYTHONIOENCODING": "utf-8"}
            args = (str(executable), str(Path(__file__).with_name("worker.py")))
            options = {"env": env, "limit": 16 * 1024 * 1024,
                       **({"creationflags": 0x08000000} if os.name == "nt" else {})}
            try:
                process = await asyncio.create_subprocess_exec(*args,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, **options)
            except NotImplementedError:
                from app.local_models.process import ThreadedProcess
                process = ThreadedProcess(args, **options)
            request = {"key": key, "operation": operation, "model_path": str(model_path(key).resolve()),
                       "config": config.model_dump(), "payload": payload}
            async def receive():
                process.stdin.write(json.dumps(request).encode())
                await process.stdin.drain()
                process.stdin.close()
                final = None
                vectors = []
                while line := await process.stdout.readline():
                    message = json.loads(line)
                    if "embedding_chunk" in message:
                        chunk = message['embedding_chunk']
                        if (operation != 'embedding' or not isinstance(chunk, list)
                                or message.get('embedding_offset') != len(vectors)
                                or len(vectors) + len(chunk) > len(payload.get('texts', []))):
                            raise ProviderError('LOCAL_MODEL_INVALID_RESPONSE', '本地向量传输顺序或数量无效。')
                        vectors.extend(chunk)
                    elif "progress" in message:
                        callback = runtime_progress.get()
                        if callback:
                            callback(message)
                    else:
                        final = message
                await process.wait()
                if isinstance(final, dict) and 'embedding_count' in final:
                    if (final['embedding_count'] != len(vectors)
                            or len(vectors) != len(payload.get('texts', []))):
                        raise ProviderError('LOCAL_MODEL_INVALID_RESPONSE', '本地向量传输不完整。')
                    final['result'] = vectors
                elif vectors:
                    raise ProviderError('LOCAL_MODEL_INVALID_RESPONSE', '本地向量传输缺少结束标记。')
                return final
            try:
                result = await asyncio.wait_for(receive(), config.timeout_seconds)
            except TimeoutError as exc:
                raise ProviderError("LOCAL_MODEL_TIMEOUT", "本地模型处理超时。") from exc
            if process.returncode != 0:
                raise ProviderError("LOCAL_MODEL_PROCESS_FAILED", "本地模型进程退出，请检查依赖与资源预算。")
            if not isinstance(result, dict):
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "本地模型进程未返回有效结果。")
            diagnostics.update(result.get("diagnostics", {}))
            if "error_code" in result:
                raise ProviderError(result["error_code"], result.get("message", "本地推理失败。"))
            attempt.observe(result)
            attempt.completed = True
            return result
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            if process is not None and hasattr(process, "close"):
                await process.close()
            attempt.persist()


runtime = Runtime()

# 对确定性的单文本本地向量做有界内存复用。键包含模型目录、不可变版本和冻结运行配置；
# 远程 API 响应以及模型不可用时的回退结果都不进入缓存。
_embedding_cache = OrderedDict()
_EMBEDDING_CACHE_TTL = 600


class LocalEmbedding:
    dim = 384

    def __init__(self, config=None):
        self._config = config

    def snapshot(self):
        return LocalEmbedding((self._config or configuration()).model_copy(deep=True))

    @property
    def model_id(self):
        spec = CATALOG[(self._config or configuration()).embedding_model]
        return f"{spec.repository}@{spec.revision}"

    @property
    def version(self):
        return CATALOG[(self._config or configuration()).embedding_model].revision

    @property
    def available(self):
        return read_state(configuration().embedding_model)["status"] == "installed" and interpreter().is_file()

    async def embed_documents(self, texts):
        config = (self._config or configuration()).model_copy(deep=True)
        from app.retrieval.provenance import record_embedding
        cache_key = None
        if len(texts) == 1 and read_state(config.embedding_model)['status'] == 'installed' and interpreter(config).is_file():
            cache_key = (str(model_path(config.embedding_model).resolve()), config.model_dump_json(),
                         hashlib.sha256(texts[0].encode()).hexdigest())
            cached = _embedding_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < _EMBEDDING_CACHE_TTL:
                _embedding_cache.move_to_end(cache_key)
                record_embedding(query_embedding_cache='hit')
                return [list(cached[1])]
        record_embedding(query_embedding_cache='miss')
        token = runtime_context.set(config)
        try:
            vectors = await runtime.infer(config.embedding_model, "embedding", {"texts": texts}, priority=embedding_priority.get())
            if cache_key and len(vectors) == 1:
                _embedding_cache[cache_key] = (time.monotonic(), tuple(vectors[0]))
                while len(_embedding_cache) > 128: _embedding_cache.popitem(last=False)
            return vectors
        finally:
            runtime_context.reset(token)

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
                                segments=[TranscriptSegment(**s) for s in result["segments"]], warnings=result.get("warnings", []))

    async def match(self, source, reference):
        result = await runtime.infer("eres2netv2", "speaker_matching",
            {"source": str(source.resolve()), "reference": str(reference.resolve())}, priority=0)
        return result["score"]
