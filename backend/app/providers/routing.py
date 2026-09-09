"""能力路由：先验证远程结果，再显式回退到本地后端。

生产环境注入已安装的 CPU/CUDA 后端；确定性嵌入只供显式注入的测试与协议夹具使用。
"""
from __future__ import annotations

import hashlib
import asyncio
import time
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

import httpx

from app.contracts import (
    EmbeddingResult, LocalBackendStatus, ModelBinding, ModelRoutingConfig,
    ModelRoutingResponse, ProviderType, SpeakerMatchResult,
)
from app.database.db import connect, transaction
from app.errors import ApiError
from app.providers.base import ProviderError
from app.providers.credentials import CredentialResolver, CredentialStoreError
from app.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.retrieval.embedding import EmbeddingProvider, HashEmbeddingProvider
from app.retrieval.provenance import record_embedding

CAPABILITIES = ("embedding", "transcription", "speaker_matching")
HTTP_TYPES = {ProviderType.openai_chat, ProviderType.openai_compatible}
MAX_MEDIA_BYTES = 25 * 1024 * 1024
MAX_LOCAL_MEDIA_BYTES = 128 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class LocalSpeechBackend(Protocol):
    available: bool

    async def transcribe(self, source: Path, language: str | None) -> str: ...

    async def match(self, source: Path, reference: Path) -> float: ...


class PendingSpeechBackend:
    available = False

    async def transcribe(self, source: Path, language: str | None) -> str:
        raise ProviderError("LOCAL_MODEL_NOT_INSTALLED", "本地音频转写模型尚未安装，将在阶段 F 接入。")

    async def match(self, source: Path, reference: Path) -> float:
        raise ProviderError("LOCAL_MODEL_NOT_INSTALLED", "本地声纹模型尚未安装，将在阶段 F 接入。")


@dataclass(frozen=True)
class RoutedTranscript:
    text: str
    source: str
    fallback_reason: str | None = None
    segments: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def invalid_response() -> ProviderError:
    return ProviderError("PROVIDER_INVALID_RESPONSE", "Model API returned an invalid result.")


def finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


class ModelRoutingService:
    def __init__(self, providers: ProviderRegistry, credentials: CredentialResolver, *,
                 local_embedding: EmbeddingProvider | None = None,
                 local_speech: LocalSpeechBackend | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.providers = providers
        self.credentials = credentials
        self.local_embedding = local_embedding or HashEmbeddingProvider()
        self.local_speech = local_speech or PendingSpeechBackend()
        self.transport = transport

    @staticmethod
    def _connection():
        conn = connect()
        conn.execute("CREATE TABLE IF NOT EXISTS model_routing (id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL)")
        return conn

    def snapshot(self):
        from copy import copy
        from app.providers.registry import RegisteredProvider
        frozen = copy(self)
        config = self.configuration().model_copy(deep=True)
        providers = ProviderRegistry()
        for item in self.providers.list_configs():
            original = self.providers.get_any(item.provider_id)
            providers._providers[item.provider_id] = RegisteredProvider(item, original.adapter)
        frozen.providers = providers
        frozen.configuration = lambda: config
        return frozen

    def configuration(self) -> ModelRoutingConfig:
        conn = self._connection()
        try:
            row = conn.execute("SELECT config_json FROM model_routing WHERE id=1").fetchone()
            return ModelRoutingConfig.model_validate_json(row[0]) if row else ModelRoutingConfig()
        except ValueError as exc:
            raise ApiError(500, "MODEL_ROUTING_STORAGE_INVALID", "Saved model routing could not be loaded.") from exc
        finally:
            conn.close()

    def describe(self) -> ModelRoutingResponse:
        is_hash = isinstance(self.local_embedding, HashEmbeddingProvider)
        embedding_available = getattr(self.local_embedding, "available", True)
        def speech_available(capability):
            check = getattr(self.local_speech, "available_for", None)
            return check(capability) if check else self.local_speech.available
        return ModelRoutingResponse(config=self.configuration(), local_backends=[
            LocalBackendStatus(capability="embedding", status="placeholder" if is_hash else ("ready" if embedding_available else "not_installed"),
                               message="测试占位向量。" if is_hash else ("本地 Embedding 文件和运行环境已安装。" if embedding_available else "请安装本地模型运行环境并下载 Embedding 权重。")),
            *[LocalBackendStatus(capability=capability, status="ready" if speech_available(capability) else "not_installed",
                                 message="本地模型文件和运行环境已安装。" if speech_available(capability) else "请安装运行环境并下载对应本地模型。")
              for capability in ("transcription", "speaker_matching")],
        ])

    def update(self, config: ModelRoutingConfig) -> ModelRoutingResponse:
        for capability in CAPABILITIES:
            binding = getattr(config, capability)
            if binding:
                try:
                    provider = self.providers.get_any(binding.provider_id).config
                except ProviderNotFoundError as exc:
                    raise ApiError(422, "PROVIDER_NOT_FOUND", "请选择已保存的提供商。") from exc
                if provider.provider_type not in HTTP_TYPES:
                    raise ApiError(422, "MODEL_ROUTING_PROTOCOL_UNSUPPORTED", "该能力当前需要 OpenAI Compatible HTTP 接口。")
        conn = self._connection()
        try:
            with transaction(conn):
                row = conn.execute("SELECT config_json FROM model_routing WHERE id=1").fetchone()
                current = ModelRoutingConfig.model_validate_json(row[0]) if row else ModelRoutingConfig()
                if current.version != config.version:
                    raise ApiError(409, "MODEL_ROUTING_VERSION_CONFLICT", "配置已更新，请重新加载后再保存。")
                saved = config.model_copy(update={"version": config.version + 1})
                conn.execute("INSERT OR REPLACE INTO model_routing VALUES (1, ?)", (saved.model_dump_json(),))
        finally:
            conn.close()
        return self.describe()

    def uses_provider(self, provider_id: str) -> bool:
        config = self.configuration()
        return any(binding and binding.provider_id == provider_id for binding in
                   (getattr(config, name) for name in CAPABILITIES))

    def _remote(self, binding: ModelBinding) -> tuple[str, dict[str, str]]:
        try:
            provider = self.providers.get(binding.provider_id).config
        except ProviderNotFoundError as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Configured provider is unavailable.") from exc
        if provider.provider_type not in HTTP_TYPES:
            raise ProviderError("PROVIDER_CAPABILITY_UNSUPPORTED", "Provider does not support this HTTP capability.")
        try:
            key = self.credentials.resolve(provider.credential_id)
        except CredentialStoreError as exc:
            raise ProviderError("PROVIDER_CREDENTIAL_UNAVAILABLE", "Provider credential is unavailable.") from exc
        if provider.credential_id and not key:
            raise ProviderError("PROVIDER_CREDENTIAL_MISSING", "Provider credential is not configured.")
        url = (provider.base_url or "https://api.openai.com/v1").rstrip("/") + binding.endpoint
        return url, {"Authorization": f"Bearer {key}"} if key else {}

    async def _request(self, binding: ModelBinding, *, remote: tuple[str, dict[str, str]] | None = None, provider_config=None, **kwargs) -> tuple[dict, str]:
        url, headers = remote or self._remote(binding)
        from app.request_overrides import apply_overrides
        from app.services.usage_service import UsageAttempt
        capability = "embedding" if "json" in kwargs else ("speaker_matching" if "reference_file" in kwargs.get("files", {}) else "transcription")
        provider = provider_config or self.providers.get(binding.provider_id).config
        field = "json" if capability == "embedding" else "data"
        payload = apply_overrides(kwargs.get(field, {}), provider.request_overrides, capability)
        kwargs[field] = payload if field == "json" else {key: json.dumps(value) if isinstance(value, (dict, list, bool)) or value is None else value for key, value in payload.items()}
        attempt = UsageAttempt(binding.provider_id, binding.model, provider.provider_type.value, capability)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
                async with client.stream("POST", url, headers=headers, **kwargs) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise invalid_response()
                    data = json.loads(body)
                    attempt.observe(data)
                    attempt.completed = True
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Model API timed out.") from exc
        except httpx.HTTPStatusError as exc:
            code = {401: "PROVIDER_AUTH_FAILED", 403: "PROVIDER_AUTH_FAILED", 404: "MODEL_NOT_FOUND", 429: "PROVIDER_RATE_LIMITED"}.get(exc.response.status_code, "PROVIDER_UNAVAILABLE")
            raise ProviderError(code, f"Model API returned HTTP {exc.response.status_code}.") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Model API is unavailable.") from exc
        except (ValueError, UnicodeError) as exc:
            raise invalid_response() from exc
        finally:
            attempt.persist()
            from app.services.model_diagnostics import record
            task = asyncio.current_task()
            status = "completed" if attempt.completed else ("cancelled" if task and task.cancelling() else "failed")
            record(model=binding.model, operation=capability, source="api", status=status,
                   attempt_id=attempt.attempt_id, request_id=attempt.request_id, elapsed_seconds=time.monotonic() - started)
        if not isinstance(data, dict) or data.get("error"):
            raise invalid_response()
        return data, url

    async def embed(self, texts: list[str], *, local_only=False) -> EmbeddingResult:
        config = self.configuration()
        binding = None if local_only else config.embedding
        record_embedding(route_version=config.version,
                         requested_route=binding.model_dump() if binding else None)
        reason = None
        if binding and texts:
            try:
                vectors = []
                dimension = binding.dimensions
                # 跨批次冻结源，即使用户编辑提供程序也是如此。
                remote = self._remote(binding)
                provider_config = self.providers.get(binding.provider_id).config.model_copy(deep=True)
                for start in range(0, len(texts), 32):
                    batch = texts[start:start + 32]
                    payload = {"model": binding.model, "input": batch, "encoding_format": "float"}
                    if binding.dimensions is not None:
                        payload["dimensions"] = binding.dimensions
                    data, url = await self._request(binding, remote=remote, provider_config=provider_config, json=payload)
                    items = data.get("data")
                    if not isinstance(items, list) or len(items) != len(batch):
                        raise invalid_response()
                    indexed = {}
                    for item in items:
                        if not isinstance(item, dict):
                            raise invalid_response()
                        index, vector = item.get("index"), item.get("embedding")
                        if type(index) is not int or index in indexed or not 0 <= index < len(batch):
                            raise invalid_response()
                        if not isinstance(vector, list) or not 1 <= len(vector) <= 16384:
                            raise invalid_response()
                        if any(not finite_number(value) for value in vector):
                            raise invalid_response()
                        dimension = dimension or len(vector)
                        norm = math.hypot(*vector)
                        if len(vector) != dimension or not norm or not math.isfinite(norm):
                            raise invalid_response()
                        indexed[index] = [value / norm for value in vector]
                    vectors.extend(indexed[index] for index in range(len(batch)))
                identity_parts = [url, binding.model, dimension]
                extensions = [rule.model_dump() for rule in provider_config.request_overrides
                              if rule.capability == "embedding" and rule.model in (None, binding.model)]
                if extensions:
                    identity_parts.append(extensions)
                identity = json.dumps(identity_parts, separators=(",", ":"))
                return EmbeddingResult(vectors=vectors, source="api", dimensions=dimension,
                                       model_id="api-" + hashlib.sha256(identity.encode()).hexdigest())
            except ProviderError as exc:
                reason = exc.code
                from app.services.model_diagnostics import record
                record(model=binding.model, source="api", status="fallback", error_code=reason,
                       fallback_reason=reason, operation="model_routing")
        from app.local_models.runtime import LocalEmbedding
        local_embedding = self.local_embedding.snapshot() if isinstance(self.local_embedding, LocalEmbedding) else self.local_embedding
        try:
            vectors = await local_embedding.embed_documents(texts)
        except ProviderError as exc:
            raise ApiError(503, exc.code, exc.message, {"fallback_reason": reason}) from exc
        return EmbeddingResult(vectors=vectors, source="local", model_id=local_embedding.model_id,
                               dimensions=local_embedding.dim, fallback_reason=reason)

    @staticmethod
    def _media_file(path: Path, *, local_only: bool = False):
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise ApiError(404, "ATTACHMENT_NOT_FOUND", "Audio attachment was not found.") from exc
        import os
        limit = MAX_LOCAL_MEDIA_BYTES if local_only else MAX_MEDIA_BYTES
        if not 0 < os.fstat(handle.fileno()).st_size <= limit:
            handle.close()
            raise ApiError(413, "ATTACHMENT_TOO_LARGE", f"Audio attachment must be between 1 byte and {limit // (1024 * 1024)} MiB.")
        return handle

    async def transcribe(self, source: Path, language: str | None, *, local_only: bool = False) -> RoutedTranscript:
        binding = None if local_only else self.configuration().transcription
        if binding is None:
            with self._media_file(source, local_only=local_only):
                pass
        reason = None
        if binding:
            try:
                fields = {"model": binding.model}
                if language:
                    fields["language"] = language
                with self._media_file(source) as handle:
                    data, _ = await self._request(binding, data=fields,
                        files={"file": (source.name, handle, "application/octet-stream")})
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise invalid_response()
                segments = []
                raw_segments = data.get("segments", [])
                if not isinstance(raw_segments, list) or len(raw_segments) > 10000:
                    raise invalid_response()
                from app.contracts import TranscriptSegment
                for index, raw in enumerate(raw_segments):
                    if not isinstance(raw, dict):
                        raise invalid_response()
                    start, end = raw.get("start", raw.get("start_time")), raw.get("end", raw.get("end_time"))
                    if not finite_number(start) or not finite_number(end) or not isinstance(raw.get("text"), str):
                        raise invalid_response()
                    try:
                        segments.append(TranscriptSegment(segment_id=f"segment_{index + 1}", start_time=start,
                            end_time=end, text=raw["text"], speaker=raw.get("speaker")))
                    except ValueError as exc:
                        raise invalid_response() from exc
                if segments != sorted(segments, key=lambda segment: segment.start_time):
                    raise invalid_response()
                return RoutedTranscript(text=text, source="api", segments=segments)
            except ProviderError as exc:
                reason = exc.code
                from app.services.model_diagnostics import record
                record(model=binding.model, source="api", status="fallback", error_code=reason,
                       fallback_reason=reason, operation="model_routing")
        try:
            text = await self.local_speech.transcribe(source, language)
            if isinstance(text, RoutedTranscript):
                if not text.text.strip():
                    raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "Local transcription was empty.")
                return replace(text, source="local", fallback_reason=reason)
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "Local transcription was empty.")
            return RoutedTranscript(text=text, source="local", fallback_reason=reason)
        except ProviderError as exc:
            raise ApiError(503, exc.code, exc.message, {"fallback_reason": reason}) from exc

    async def match_speakers(self, source: Path, reference: Path, *, local_only: bool = False) -> SpeakerMatchResult:
        binding = None if local_only else self.configuration().speaker_matching
        if binding is None:
            with self._media_file(source, local_only=local_only), self._media_file(reference, local_only=local_only):
                pass
        reason = None
        if binding:
            try:
                # 这是应用自身定义的接口约定，并非 OpenAI 标准端点。
                with self._media_file(source) as audio, self._media_file(reference) as sample:
                    data, _ = await self._request(binding, data={"model": binding.model}, files={
                        "file": (source.name, audio, "application/octet-stream"),
                        "reference_file": (reference.name, sample, "application/octet-stream"),
                    })
                score = data.get("score")
                if not finite_number(score) or not 0 <= score <= 1:
                    raise invalid_response()
                return SpeakerMatchResult(score=score, source="api")
            except ProviderError as exc:
                reason = exc.code
                from app.services.model_diagnostics import record
                record(model=binding.model, source="api", status="fallback", error_code=reason,
                       fallback_reason=reason, operation="model_routing")
        try:
            score = await self.local_speech.match(source, reference)
            if not finite_number(score) or not 0 <= score <= 1:
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "Local speaker matching was invalid.")
            return SpeakerMatchResult(score=score, source="local", fallback_reason=reason)
        except ProviderError as exc:
            raise ApiError(503, exc.code, exc.message, {"fallback_reason": reason}) from exc
