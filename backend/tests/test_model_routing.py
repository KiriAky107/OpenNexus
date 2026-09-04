"""Offline model-routing contracts, HTTP validation, media lifetimes and persistence.

All HTTP uses MockTransport (or the in-process API). Credentials, models and
attachments are fakes, and conftest redirects all storage to temporary paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.contracts import ModelBinding, ModelRoutingConfig, ProviderConfig, ProviderType
from app.errors import ApiError
from app.providers import MockProvider
from app.providers.credentials import CredentialStoreError
from app.providers.registry import ProviderRegistry
from app.providers.routing import ModelRoutingService, PendingSpeechBackend
from app.retrieval.embedding import HashEmbeddingProvider


def run(awaitable):
    return asyncio.run(awaitable)


def response(data, status=200):
    # Raw JSON intentionally permits NaN/Infinity to exercise hostile API output.
    return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json"})


class FakeCredentials:
    def __init__(self):
        self.value = "unit-test-placeholder"
        self.error = None
        self.calls = []

    def resolve(self, credential_id):
        self.calls.append(credential_id)
        if self.error:
            raise self.error
        return self.value if credential_id else None


class FakeEmbedding:
    model_id = "fake-local-model"
    dim = 3

    def __init__(self):
        self.calls = []
        self.error = None

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return [[0.6, 0.8, 0.0] for _ in texts]


class FakeSpeech:
    available = True

    def __init__(self):
        self.calls = []
        self.text = "local transcript"
        self.score = 0.25
        self.error = None

    async def transcribe(self, source, language):
        self.calls.append(("transcribe", source, language))
        if self.error:
            raise self.error
        return self.text

    async def match(self, source, reference):
        self.calls.append(("match", source, reference))
        if self.error:
            raise self.error
        return self.score


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    async def reject_async(*args, **kwargs):
        pytest.fail("Real HTTP transport is forbidden in model-routing tests")

    def reject_sync(*args, **kwargs):
        pytest.fail("Real HTTP transport is forbidden in model-routing tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_sync)


@pytest.fixture
def rig():
    requests = []

    def unexpected(request):
        pytest.fail(f"Unexpected model HTTP request: {request.url}")

    state = SimpleNamespace(handler=unexpected)

    async def dispatch(request):
        requests.append(request)
        result = state.handler(request)
        return await result if hasattr(result, "__await__") else result

    providers = ProviderRegistry()
    config = ProviderConfig(
        provider_id="test-provider", provider_type=ProviderType.openai_compatible,
        name="Fake provider", base_url="https://models.invalid/v1/", credential_id="test-credential",
    )
    providers.register(config, MockProvider())
    credentials, embedding, speech = FakeCredentials(), FakeEmbedding(), FakeSpeech()
    service = ModelRoutingService(
        providers, credentials, local_embedding=embedding, local_speech=speech,
        transport=httpx.MockTransport(dispatch),
    )
    return SimpleNamespace(
        service=service, providers=providers, credentials=credentials,
        embedding=embedding, speech=speech, requests=requests, http=state,
    )


def bind(rig, capability="embedding", **overrides):
    endpoints = {
        "embedding": "/embeddings", "transcription": "/audio/transcriptions",
        "speaker_matching": "/audio/speaker-matches",
    }
    binding = ModelBinding(**{
        "provider_id": "test-provider", "model": "test-model",
        "endpoint": endpoints[capability], **overrides,
    })
    current = rig.service.configuration()
    return rig.service.update(current.model_copy(update={capability: binding}))


def assert_local(rig, result, texts, reason):
    assert result.source == "local"
    assert result.model_id == rig.embedding.model_id
    assert result.dimensions == 3
    assert result.vectors == [[0.6, 0.8, 0.0] for _ in texts]
    assert result.fallback_reason == reason
    assert rig.embedding.calls == [texts]


def test_embedding_observation_keeps_request_binding_when_config_changes(rig):
    from app.retrieval.provenance import capture_embedding
    initial = bind(rig, model="original-model")

    def handler(request):
        assert json.loads(request.content)["model"] == "original-model"
        bind(rig, model="next-model")
        return response({"data": [{"index": 0, "embedding": [1, 0, 0]}]})

    rig.http.handler = handler
    with capture_embedding() as observation:
        result = run(rig.service.embed(["query"]))
    assert result.source == "api"
    assert observation["route_version"] == initial.config.version
    assert observation["requested_route"]["model"] == "original-model"
    assert observation["requested_route"]["provider_id"] == "test-provider"
    assert rig.service.configuration().embedding.model == "next-model"
    assert rig.credentials.value not in json.dumps(observation)
    assert "credential_id" not in json.dumps(observation)


@pytest.fixture
def audio(tmp_path):
    source, reference = tmp_path / "audio.wav", tmp_path / "reference.wav"
    source.write_bytes(b"fake-audio-content")
    reference.write_bytes(b"fake-reference-content")
    return source, reference


def media_call(rig, capability, audio):
    if capability == "transcription":
        return rig.service.transcribe(audio[0], "zh")
    return rig.service.match_speakers(*audio)


def track_media_handles(rig, monkeypatch):
    handles = []
    original = rig.service._media_file

    def tracked(path):
        handle = original(path)
        handles.append(handle)
        return handle

    monkeypatch.setattr(rig.service, "_media_file", tracked)
    return handles


def test_absent_binding_uses_hash_without_network(rig):
    rig.service.local_embedding = HashEmbeddingProvider()
    texts = ["hello retrieval", "向量检索"]
    result = run(rig.service.embed(texts))
    assert result.source == "local"
    assert result.model_id == "hash-v1"
    assert result.dimensions == 128
    assert result.vectors == run(HashEmbeddingProvider().embed_documents(texts))
    assert result.fallback_reason is None
    assert rig.requests == rig.credentials.calls == []
    statuses = {item.capability: item.status for item in rig.service.describe().local_backends}
    assert statuses == {"embedding": "placeholder", "transcription": "ready", "speaker_matching": "ready"}


def test_empty_embedding_input_does_not_call_remote(rig):
    bind(rig)
    result = run(rig.service.embed([]))
    assert result.vectors == [] and result.source == "local"
    assert rig.requests == []


def test_remote_embedding_restores_batch_order_normalizes_and_sends_auth(rig):
    bind(rig, dimensions=2)
    texts = [str(index) for index in range(35)]

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == "https://models.invalid/v1/embeddings"
        assert request.headers["authorization"] == "Bearer unit-test-placeholder"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["dimensions"] == 2
        assert payload["encoding_format"] == "float"
        return response({"data": [
            {"index": index, "embedding": [float(int(text) + 1), 1.0]}
            for index, text in reversed(list(enumerate(payload["input"])))
        ]})

    rig.http.handler = handler
    result = run(rig.service.embed(texts))
    assert result.source == "api" and result.fallback_reason is None
    assert result.dimensions == 2 and len(result.vectors) == 35
    for index, vector in enumerate(result.vectors):
        assert sum(value * value for value in vector) == pytest.approx(1.0)
        assert vector[0] / vector[1] == pytest.approx(index + 1)
    assert [json.loads(req.content)["input"] for req in rig.requests] == [texts[:32], texts[32:]]
    assert rig.embedding.calls == []


def test_space_id_is_stable_and_includes_full_url_model_and_inferred_dimensions(rig):
    dimensions = 2

    def handler(request):
        assert "dimensions" not in json.loads(request.content)
        return response({"data": [{"index": 0, "embedding": [1.0] * dimensions}]})

    rig.http.handler = handler
    bind(rig, model="  trimmed-model  ")

    def check(url, model, dimension):
        result = run(rig.service.embed(["hello"]))
        digest = hashlib.sha256(json.dumps([url, model, dimension], separators=(",", ":")).encode()).hexdigest()
        assert result.model_id == "api-" + digest
        assert result.source == "api"
        return result.model_id

    first = check("https://models.invalid/v1/embeddings", "trimmed-model", 2)
    assert first == check("https://models.invalid/v1/embeddings", "trimmed-model", 2)
    config = rig.providers.get_any("test-provider").config.model_copy(update={"base_url": "https://models.invalid/v1"})
    rig.providers.replace(config, MockProvider())
    assert first == check("https://models.invalid/v1/embeddings", "trimmed-model", 2)
    bind(rig, model="trimmed-model", endpoint="/custom/embeddings")
    endpoint_id = check("https://models.invalid/v1/custom/embeddings", "trimmed-model", 2)
    bind(rig, model="another-model", endpoint="/custom/embeddings")
    model_id = check("https://models.invalid/v1/custom/embeddings", "another-model", 2)
    dimensions = 3
    dim_id = check("https://models.invalid/v1/custom/embeddings", "another-model", 3)
    config = config.model_copy(update={"base_url": "https://other.invalid/v1"})
    rig.providers.replace(config, MockProvider())
    provider_id = check("https://other.invalid/v1/custom/embeddings", "another-model", 3)
    assert len({first, endpoint_id, model_id, dim_id, provider_id}) == 5


@pytest.mark.parametrize("data", [
    {"data": []},
    {"data": [{"index": 0, "embedding": [1, 0]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 2, "embedding": [0, 1]}]},
    {"data": [{"index": False, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1, 0]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [float("nan"), 1]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [float("inf"), 1]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [True, 1]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 0]}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": []}]},
    {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": ["1", 0]}]},
    {"data": [None, None]},
    {"error": {"message": "in-band failure"}, "data": []},
    [],
], ids=["empty", "count", "duplicate-index", "out-of-range-index", "bool-index", "dimensions", "nan", "infinity", "bool", "zero", "empty-vector", "string", "invalid-items", "in-band-error", "non-object"])
def test_invalid_remote_embeddings_fall_back_as_a_whole(rig, data):
    bind(rig)
    rig.http.handler = lambda request: response(data)
    texts = ["first", "second"]
    assert_local(rig, run(rig.service.embed(texts)), texts, "PROVIDER_INVALID_RESPONSE")


def test_explicit_embedding_dimension_mismatch_falls_back(rig):
    bind(rig, dimensions=3)
    rig.http.handler = lambda request: response({"data": [{"index": 0, "embedding": [1, 0]}]})
    assert_local(rig, run(rig.service.embed(["text"])), ["text"], "PROVIDER_INVALID_RESPONSE")


def test_later_batch_dimension_mismatch_discards_earlier_remote_vectors(rig):
    bind(rig)

    def handler(request):
        batch = json.loads(request.content)["input"]
        dimension = 2 if len(rig.requests) == 1 else 3
        return response({"data": [{"index": i, "embedding": [1] * dimension} for i in range(len(batch))]})

    rig.http.handler = handler
    texts = [str(i) for i in range(33)]
    assert_local(rig, run(rig.service.embed(texts)), texts, "PROVIDER_INVALID_RESPONSE")
    assert len(rig.requests) == 2


@pytest.mark.parametrize("failure, reason", [
    (401, "PROVIDER_AUTH_FAILED"), (403, "PROVIDER_AUTH_FAILED"),
    (404, "MODEL_NOT_FOUND"), (429, "PROVIDER_RATE_LIMITED"), (500, "PROVIDER_UNAVAILABLE"),
    ("timeout", "PROVIDER_TIMEOUT"), ("connect", "PROVIDER_UNAVAILABLE"),
    ("json", "PROVIDER_INVALID_RESPONSE"),
])
def test_embedding_http_failures_use_injected_local(rig, failure, reason):
    bind(rig)

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if failure == "connect":
            raise httpx.ConnectError("simulated connection failure", request=request)
        if failure == "json":
            return httpx.Response(200, content=b"not JSON")
        return response({"error": "failed"}, failure)

    rig.http.handler = handler
    assert_local(rig, run(rig.service.embed(["text"])), ["text"], reason)


@pytest.mark.parametrize("failure, reason", [
    ("missing-key", "PROVIDER_CREDENTIAL_MISSING"),
    ("unreadable-key", "PROVIDER_CREDENTIAL_UNAVAILABLE"),
    ("disabled-provider", "PROVIDER_UNAVAILABLE"),
])
def test_unavailable_remote_configuration_falls_back_without_http(rig, failure, reason):
    bind(rig)
    if failure == "missing-key":
        rig.credentials.value = None
    elif failure == "unreadable-key":
        rig.credentials.error = CredentialStoreError("fake unavailable store")
    else:
        config = rig.providers.get_any("test-provider").config.model_copy(update={"enabled": False})
        rig.providers.replace(config, MockProvider())
    assert_local(rig, run(rig.service.embed(["text"])), ["text"], reason)
    assert rig.requests == []


@pytest.mark.parametrize("capability", ["transcription", "speaker_matching"])
def test_media_success_sends_expected_multipart_and_closes_files(rig, audio, monkeypatch, capability):
    bind(rig, capability)
    handles = track_media_handles(rig, monkeypatch)

    def handler(request):
        assert request.headers["authorization"] == "Bearer unit-test-placeholder"
        assert str(request.url).endswith("/audio/transcriptions" if capability == "transcription" else "/audio/speaker-matches")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + request.headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content,
        )
        parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert parts["model"].get_payload(decode=True) == b"test-model"
        assert parts["file"].get_filename() == audio[0].name
        assert parts["file"].get_payload(decode=True) == audio[0].read_bytes()
        if capability == "transcription":
            assert set(parts) == {"model", "language", "file"}
            assert parts["language"].get_payload(decode=True) == b"zh"
            return response({"text": "remote transcript"})
        assert set(parts) == {"model", "file", "reference_file"}
        assert parts["reference_file"].get_filename() == audio[1].name
        assert parts["reference_file"].get_payload(decode=True) == audio[1].read_bytes()
        return response({"score": 0.875})

    rig.http.handler = handler
    result = run(media_call(rig, capability, audio))
    assert result.source == "api" and result.fallback_reason is None
    assert result.text == "remote transcript" if capability == "transcription" else result.score == 0.875
    assert len(handles) == (1 if capability == "transcription" else 2)
    assert all(handle.closed for handle in handles)
    assert rig.speech.calls == []


@pytest.mark.parametrize("capability, data", [
    ("transcription", {}), ("transcription", {"text": "  "}), ("transcription", {"text": False}),
    ("transcription", {"error": "in-band", "text": "must not use"}),
    ("speaker_matching", {}), ("speaker_matching", {"score": -0.1}),
    ("speaker_matching", {"score": 1.1}), ("speaker_matching", {"score": True}),
    ("speaker_matching", {"score": float("nan")}), ("speaker_matching", {"score": "0.5"}),
    ("speaker_matching", {"error": "in-band", "score": 0.9}),
])
def test_invalid_remote_media_falls_back_to_injected_local(rig, audio, monkeypatch, capability, data):
    bind(rig, capability)
    handles = track_media_handles(rig, monkeypatch)
    rig.http.handler = lambda request: response(data)
    result = run(media_call(rig, capability, audio))
    assert result.source == "local" and result.fallback_reason == "PROVIDER_INVALID_RESPONSE"
    assert result.text == "local transcript" if capability == "transcription" else result.score == 0.25
    assert rig.speech.calls == [
        ("transcribe", audio[0], "zh") if capability == "transcription" else ("match", *audio)
    ]
    assert handles and all(handle.closed for handle in handles)


@pytest.mark.parametrize("capability", ["transcription", "speaker_matching"])
@pytest.mark.parametrize("configured", [False, True])
def test_pending_local_backend_has_explicit_503_and_fallback_details(rig, audio, capability, configured):
    rig.service.local_speech = PendingSpeechBackend()
    if configured:
        bind(rig, capability)
        rig.http.handler = lambda request: response({"error": "unauthorized"}, 401)
    with pytest.raises(ApiError) as caught:
        run(media_call(rig, capability, audio))
    assert caught.value.status_code == 503
    assert caught.value.code == "LOCAL_MODEL_NOT_INSTALLED"
    assert caught.value.details == {"fallback_reason": "PROVIDER_AUTH_FAILED" if configured else None}
    statuses = {item.capability: item.status for item in rig.service.describe().local_backends}
    assert statuses["transcription"] == statuses["speaker_matching"] == "not_installed"
    assert len(rig.requests) == int(configured)


@pytest.mark.parametrize("capability", ["transcription", "speaker_matching"])
def test_invalid_local_speech_returns_explicit_503(rig, audio, capability):
    rig.speech.text = ""
    rig.speech.score = True
    with pytest.raises(ApiError) as caught:
        run(media_call(rig, capability, audio))
    assert (caught.value.status_code, caught.value.code) == (503, "LOCAL_MODEL_INVALID_RESPONSE")
    assert caught.value.details == {"fallback_reason": None}


@pytest.mark.parametrize("capability", ["embedding", "transcription", "speaker_matching"])
@pytest.mark.parametrize("stage", ["remote", "local"])
def test_cancellation_propagates_and_upload_handles_close(rig, audio, monkeypatch, capability, stage):
    bind(rig, capability)
    handles = track_media_handles(rig, monkeypatch)

    async def cancelled(request):
        raise asyncio.CancelledError()

    if stage == "remote":
        rig.http.handler = cancelled
    else:
        rig.http.handler = lambda request: response({"error": "fallback"}, 500)
        rig.embedding.error = rig.speech.error = asyncio.CancelledError()
    operation = rig.service.embed(["text"]) if capability == "embedding" else media_call(rig, capability, audio)
    with pytest.raises(asyncio.CancelledError):
        run(operation)
    assert len(handles) == {"embedding": 0, "transcription": 1, "speaker_matching": 2}[capability]
    assert all(handle.closed for handle in handles)
    if stage == "remote":
        assert rig.embedding.calls == rig.speech.calls == []


def test_missing_reference_closes_already_open_source(rig, audio, monkeypatch):
    bind(rig, "speaker_matching")
    handles = track_media_handles(rig, monkeypatch)
    audio[1].unlink()
    with pytest.raises(ApiError) as caught:
        run(rig.service.match_speakers(*audio))
    assert caught.value.status_code == 404
    assert len(handles) == 1 and handles[0].closed
    assert rig.requests == []


def test_config_optimistic_conflict_preserves_saved_bindings(rig):
    assert rig.service.configuration().version == 0
    saved = bind(rig).config
    assert saved.version == 1
    with pytest.raises(ApiError) as caught:
        rig.service.update(ModelRoutingConfig(version=0))
    assert (caught.value.status_code, caught.value.code) == (409, "MODEL_ROUTING_VERSION_CONFLICT")
    assert rig.service.configuration() == saved
    assert rig.service.uses_provider("test-provider")
    assert not rig.service.uses_provider("not-a-provider")
    cleared = rig.service.update(ModelRoutingConfig(version=1)).config
    assert cleared.version == 2 and cleared.embedding is None
    assert not rig.service.uses_provider("test-provider")


@pytest.mark.parametrize("capability", ["embedding", "transcription", "speaker_matching"])
@pytest.mark.parametrize("provider_id, code", [
    ("missing", "PROVIDER_NOT_FOUND"), ("unsupported", "MODEL_ROUTING_PROTOCOL_UNSUPPORTED"),
])
def test_config_references_require_existing_supported_providers(rig, capability, provider_id, code):
    rig.providers.register(
        ProviderConfig(provider_id="unsupported", provider_type=ProviderType.ollama, name="unsupported"), MockProvider(),
    )
    with pytest.raises(ApiError) as caught:
        bind(rig, capability, provider_id=provider_id)
    assert (caught.value.status_code, caught.value.code) == (422, code)
    assert rig.service.configuration() == ModelRoutingConfig()
    assert rig.requests == []


@pytest.fixture
def api(monkeypatch, no_real_http, _isolate_data_dir):
    # Import the production container only after temporary storage is configured.
    from app import container as container_module, routes
    from app.main import app

    containers = []

    def restart():
        container = container_module.build_container()
        container.model_routing.credentials = FakeCredentials()

        def unexpected(request):
            pytest.fail(f"Unexpected API-side provider HTTP: {request.url}")

        container.model_routing.transport = httpx.MockTransport(unexpected)
        monkeypatch.setattr(container_module, "container", container)
        monkeypatch.setattr(routes, "container", container)
        containers.append(container)
        return container

    container = restart()
    client = TestClient(app)
    yield SimpleNamespace(client=client, container=container, restart=restart)
    client.close()
    for container in containers:
        container.plugins.shutdown()
        container.mcp_servers.shutdown()


def create_api_provider(api):
    result = api.client.post("/api/providers", json={
        "provider_type": "openai_compatible", "name": "Persisted fake",
        "base_url": "https://persist.invalid/v1", "default_model": "fake-model",
    })
    assert result.status_code == 200, result.text
    return result.json()


def test_api_config_conflict_reference_delete_and_restart_persistence(api):
    provider = create_api_provider(api)
    provider_id = provider["provider_id"]
    assert api.client.get("/api/model-routing").json()["config"]["version"] == 0
    config = {"version": 0, "embedding": {"provider_id": provider_id, "model": "embed-model", "endpoint": "/embeddings"}}
    saved = api.client.put("/api/model-routing", json=config)
    assert saved.status_code == 200
    assert saved.json()["config"]["version"] == 1
    conflict = api.client.put("/api/model-routing", json=config)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "MODEL_ROUTING_VERSION_CONFLICT"
    blocked = api.client.delete(f"/api/providers/{provider_id}")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "PROVIDER_IN_USE"
    restarted = api.restart()
    assert restarted.providers.get_any(provider_id).config.model_dump(mode="json") == provider
    assert api.client.get("/api/model-routing").json()["config"] == saved.json()["config"]
    assert {item["provider_id"] for item in api.client.get("/api/providers").json()["items"]} == {"mock", provider_id}
    cleared = api.client.put("/api/model-routing", json={"version": 1})
    assert cleared.status_code == 200
    assert api.client.delete(f"/api/providers/{provider_id}").status_code == 200
    api.restart()
    assert api.client.get(f"/api/providers/{provider_id}").status_code == 404
    assert api.client.get("/api/model-routing").json()["config"]["version"] == 2


def test_api_provider_type_patch_rebuilds_adapter_and_persists(api):
    from app.providers.anthropic_messages import AnthropicMessagesProvider

    provider = create_api_provider(api)
    provider_id = provider["provider_id"]
    changed = api.client.patch(f"/api/providers/{provider_id}", json={
        "provider_type": "anthropic_messages", "base_url": "https://anthropic.invalid/v1",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["provider_type"] == "anthropic_messages"
    assert changed.json()["name"] == provider["name"]
    assert isinstance(api.container.providers.get_any(provider_id).adapter, AnthropicMessagesProvider)
    restarted = api.restart()
    assert isinstance(restarted.providers.get_any(provider_id).adapter, AnthropicMessagesProvider)
    assert api.client.get(f"/api/providers/{provider_id}").json() == changed.json()
    for invalid_type in (None, "mock", "nonexistent-type"):
        rejected = api.client.patch(f"/api/providers/{provider_id}", json={"provider_type": invalid_type})
        assert rejected.status_code == 422
        assert api.client.get(f"/api/providers/{provider_id}").json() == changed.json()


@pytest.mark.parametrize("endpoint", ["https://elsewhere.invalid/embed", "//elsewhere.invalid/embed", "relative", "/../embed", "/embed?key=test"])
def test_api_config_rejects_non_provider_endpoint_paths(api, endpoint):
    provider = create_api_provider(api)
    result = api.client.put("/api/model-routing", json={
        "embedding": {"provider_id": provider["provider_id"], "model": "embed", "endpoint": endpoint},
    })
    assert result.status_code == 422
    assert api.client.get("/api/model-routing").json()["config"]["version"] == 0


def test_api_embedding_reports_remote_and_fallback_sources(api):
    provider = create_api_provider(api)
    assert api.client.put("/api/model-routing", json={
        "embedding": {"provider_id": provider["provider_id"], "model": "embed", "endpoint": "/embeddings"},
    }).status_code == 200
    api.container.model_routing.transport = httpx.MockTransport(
        lambda request: response({"data": [{"index": 0, "embedding": [3, 4]}]}),
    )
    result = api.client.post("/api/models/embeddings", json={"texts": ["hello"]})
    assert result.status_code == 200
    assert result.json()["source"] == "api" and result.json()["vectors"][0] == pytest.approx([0.6, 0.8])
    api.container.model_routing.transport = httpx.MockTransport(lambda request: response({"error": "denied"}, 401))
    result = api.client.post("/api/models/embeddings", json={"texts": ["hello"]})
    assert result.status_code == 200
    assert result.json()["source"] == "local" and result.json()["model_id"] == "hash-v1"
    assert result.json()["fallback_reason"] == "PROVIDER_AUTH_FAILED"
    assert api.client.post("/api/models/embeddings", json={"texts": []}).status_code == 422


def test_api_speech_failure_reports_reason_in_503_and_transcription_job(api):
    from app.services.attachment_service import attachment_path

    source, reference = attachment_path("audio.wav"), attachment_path("reference.wav")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"test audio")
    reference.write_bytes(b"test reference")
    provider = create_api_provider(api)
    assert api.client.put("/api/model-routing", json={
        "transcription": {"provider_id": provider["provider_id"], "model": "asr", "endpoint": "/audio/transcriptions"},
        "speaker_matching": {"provider_id": provider["provider_id"], "model": "voice", "endpoint": "/audio/speaker-matches"},
    }).status_code == 200
    api.container.model_routing.transport = httpx.MockTransport(lambda request: response({"error": "offline"}, 500))
    match = api.client.post("/api/media/speaker-matches", json={"attachment_id": source.name, "reference_attachment_id": reference.name})
    assert match.status_code == 503
    assert match.json()["error"]["code"] == "LOCAL_MODEL_NOT_INSTALLED"
    assert match.json()["error"]["details"] == {"fallback_reason": "PROVIDER_UNAVAILABLE"}
    with api.client:
        transcript = api.client.post("/api/media/transcriptions", json={"attachment_id": source.name, "language": "zh"})
        assert transcript.status_code == 202
        job = transcript.json()
        assert job["status"] == "queued"
        stream = api.client.get(f"/api/media/transcriptions/{job['job_id']}/events")
        assert "event: Failed" in stream.text
        job = api.client.get(f"/api/media/transcriptions/{job['job_id']}").json()
    assert job["status"] == "failed" and job["error_code"] == "LOCAL_MODEL_NOT_INSTALLED"
    assert job["fallback_reason"] == "PROVIDER_UNAVAILABLE"
    assert api.client.get(f"/api/media/transcriptions/{job['job_id']}").json() == job


@pytest.mark.parametrize("capability", ["embedding", "speaker_matching"])
def test_out_of_float_range_json_number_is_invalid_remote_and_falls_back(rig, audio, capability):
    """JSON integers may be finite but too large to convert to a Python float."""
    bind(rig, capability)
    data = {"data": [{"index": 0, "embedding": [10 ** 400, 1]}]} if capability == "embedding" else {"score": 10 ** 400}
    rig.http.handler = lambda request: response(data)
    if capability == "embedding":
        assert_local(rig, run(rig.service.embed(["text"])), ["text"], "PROVIDER_INVALID_RESPONSE")
    else:
        result = run(media_call(rig, capability, audio))
        assert result.source == "local" and result.score == rig.speech.score
        assert result.fallback_reason == "PROVIDER_INVALID_RESPONSE"


def test_remote_segments_are_validated_and_local_only_skips_api(rig, audio):
    bind(rig, "transcription")
    rig.http.handler = lambda request: response({"text":"内容", "segments":[{"start":0,"end":1.5,"text":"内容"}]})
    result = run(rig.service.transcribe(audio[0], "zh"))
    assert result.source == "api" and result.segments[0].end_time == 1.5
    rig.http.handler = lambda request: response({"text":"内容", "segments":[{"start":2,"end":1,"text":"内容"}]})
    assert run(rig.service.transcribe(audio[0], "zh")).fallback_reason == "PROVIDER_INVALID_RESPONSE"
    count = len(rig.requests)
    result = run(rig.service.transcribe(audio[0], "zh", local_only=True))
    assert result.source == "local" and len(rig.requests) == count
