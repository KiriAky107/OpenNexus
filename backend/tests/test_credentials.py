import asyncio

import httpx

from app.config import get_settings
from app.contracts import CredentialWriteRequest
from app.providers.credentials import (
    ChainedCredentialResolver,
    EncryptedCredentialStore,
    EnvironmentCredentialResolver,
)
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.routes import get_credential_status, put_credential


def test_encrypted_credential_store_round_trip_without_plaintext_on_disk() -> None:
    store = EncryptedCredentialStore()
    secret = "sk-test-sensitive-value"

    store.put("deepseek", secret)

    store_path = get_settings().data_dir / "credentials" / "credentials.json"
    key_path = get_settings().data_dir / "credentials" / "master.key"
    assert store_path.exists()
    assert key_path.exists()
    assert secret not in store_path.read_text(encoding="utf-8")
    assert secret not in key_path.read_text(encoding="ascii")
    assert store.resolve("deepseek") == secret
    assert store.has("deepseek") is True
    assert store.delete("deepseek") is True
    assert store.resolve("deepseek") is None


def test_credential_api_never_returns_secret() -> None:
    written = asyncio.run(
        put_credential(
            "deepseek",
            CredentialWriteRequest(api_key="sk-test-sensitive-value"),
        )
    )
    status = asyncio.run(get_credential_status("deepseek"))

    assert written.model_dump() == {"credential_id": "deepseek", "configured": True}
    assert status.configured is True
    assert "sk-test-sensitive-value" not in written.model_dump_json()


def test_provider_reads_decrypted_api_key_from_encrypted_store() -> None:
    store = EncryptedCredentialStore()
    store.put("deepseek", "sk-test-sensitive-value")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test-sensitive-value"
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})

    provider = OpenAICompatibleProvider(
        base_url="https://api.deepseek.test",
        credential_id="deepseek",
        credentials=store,
        transport=httpx.MockTransport(handler),
    )

    models = asyncio.run(provider.list_models())

    assert [model.model for model in models] == ["deepseek-chat"]


def test_saved_credential_takes_precedence_over_environment_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    store = EncryptedCredentialStore()
    store.put("deepseek", "saved-key")
    resolver = ChainedCredentialResolver(store, EnvironmentCredentialResolver())

    assert resolver.resolve("deepseek") == "saved-key"
