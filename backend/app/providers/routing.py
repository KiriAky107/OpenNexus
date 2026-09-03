"""Capability routing: validated remote results, then an explicit local backend.

Phase E supplies HTTP adapters and injectable local contracts. Hash embeddings are
still a development placeholder; speech models are installed in phase F.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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

CAPABILITIES = ("embedding", "transcription", "speaker_matching")
HTTP_TYPES = {ProviderType.openai_chat, ProviderType.openai_compatible}
MAX_MEDIA_BYTES = 25 * 1024 * 1024
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
        return ModelRoutingResponse(config=self.configuration(), local_backends=[
            LocalBackendStatus(capability="embedding", status="placeholder" if isinstance(self.local_embedding, HashEmbeddingProvider) else "ready",
                               message="当前为 hash-v1 确定性占位向量，真实本地语义模型尚未集成。" if isinstance(self.local_embedding, HashEmbeddingProvider) else "本地 Embedding 模型已就绪。"),
            *[LocalBackendStatus(capability=capability, status="ready" if self.local_speech.available else "not_installed",
                                 message="本地模型已就绪。" if self.local_speech.available else "阶段 F 接入本地模型；当前保留回退接口。")
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

    async def _request(self, binding: ModelBinding, *, remote: tuple[str, dict[str, str]] | None = None, **kwargs) -> tuple[dict, str]:
        url, headers = remote or self._remote(binding)
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
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Model API timed out.") from exc
        except httpx.HTTPStatusError as exc:
            code = {401: "PROVIDER_AUTH_FAILED", 403: "PROVIDER_AUTH_FAILED", 404: "MODEL_NOT_FOUND", 429: "PROVIDER_RATE_LIMITED"}.get(exc.response.status_code, "PROVIDER_UNAVAILABLE")
            raise ProviderError(code, f"Model API returned HTTP {exc.response.status_code}.") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Model API is unavailable.") from exc
        except (ValueError, UnicodeError) as exc:
            raise invalid_response() from exc
        if not isinstance(data, dict) or data.get("error"):
            raise invalid_response()
        return data, url

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        binding = self.configuration().embedding
        reason = None
        if binding and texts:
            try:
                vectors = []
                dimension = binding.dimensions
                # Freeze the origin across batches, even if the user edits the provider.
                remote = self._remote(binding)
                for start in range(0, len(texts), 32):
                    batch = texts[start:start + 32]
                    payload = {"model": binding.model, "input": batch, "encoding_format": "float"}
                    if binding.dimensions is not None:
                        payload["dimensions"] = binding.dimensions
                    data, url = await self._request(binding, remote=remote, json=payload)
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
                identity = json.dumps([url, binding.model, dimension], separators=(",", ":"))
                return EmbeddingResult(vectors=vectors, source="api", dimensions=dimension,
                                       model_id="api-" + hashlib.sha256(identity.encode()).hexdigest())
            except ProviderError as exc:
                reason = exc.code
        vectors = await self.local_embedding.embed_documents(texts)
        return EmbeddingResult(vectors=vectors, source="local", model_id=self.local_embedding.model_id,
                               dimensions=self.local_embedding.dim, fallback_reason=reason)

    @staticmethod
    def _media_file(path: Path):
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise ApiError(404, "ATTACHMENT_NOT_FOUND", "Audio attachment was not found.") from exc
        import os
        if not 0 < os.fstat(handle.fileno()).st_size <= MAX_MEDIA_BYTES:
            handle.close()
            raise ApiError(413, "ATTACHMENT_TOO_LARGE", "Audio attachment must be between 1 byte and 25 MiB.")
        return handle

    async def transcribe(self, source: Path, language: str | None) -> RoutedTranscript:
        binding = self.configuration().transcription
        if binding is None:
            with self._media_file(source):
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
                return RoutedTranscript(text=text, source="api")
            except ProviderError as exc:
                reason = exc.code
        try:
            text = await self.local_speech.transcribe(source, language)
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "Local transcription was empty.")
            return RoutedTranscript(text=text, source="local", fallback_reason=reason)
        except ProviderError as exc:
            raise ApiError(503, exc.code, exc.message, {"fallback_reason": reason}) from exc

    async def match_speakers(self, source: Path, reference: Path) -> SpeakerMatchResult:
        binding = self.configuration().speaker_matching
        if binding is None:
            with self._media_file(source), self._media_file(reference):
                pass
        reason = None
        if binding:
            try:
                # Explicit application contract, not an OpenAI-standard endpoint.
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
        try:
            score = await self.local_speech.match(source, reference)
            if not finite_number(score) or not 0 <= score <= 1:
                raise ProviderError("LOCAL_MODEL_INVALID_RESPONSE", "Local speaker matching was invalid.")
            return SpeakerMatchResult(score=score, source="local", fallback_reason=reason)
        except ProviderError as exc:
            raise ApiError(503, exc.code, exc.message, {"fallback_reason": reason}) from exc
