import asyncio

import httpx
import pytest

from app.config import get_settings
from app.contracts import CredentialWriteRequest
from app.errors import ApiError
from app.providers.credentials import (
    ChainedCredentialResolver,
    CredentialStoreError,
    EncryptedCredentialStore,
    EnvironmentCredentialResolver,
)
from app.providers.factory import ProviderFactory
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.routes import delete_credential, get_credential_status, put_credential


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


def test_public_credential_api_rejects_plugin_namespace() -> None:
    operations = [
        get_credential_status("plugin.text-tools.api_key"),
        put_credential(
            "plugin.text-tools.api_key",
            CredentialWriteRequest(api_key="must-not-write"),
        ),
        delete_credential("plugin.text-tools.api_key"),
    ]
    for operation in operations:
        with pytest.raises(ApiError) as exc:
            asyncio.run(operation)
        assert exc.value.code == "CREDENTIAL_NAMESPACE_RESERVED"

    assert EncryptedCredentialStore().resolve("plugin.text-tools.api_key") is None


def test_provider_resolver_cannot_read_plugin_secret() -> None:
    store = EncryptedCredentialStore()
    store.put("plugin.text-tools.api_key", "private-plugin-secret")
    resolver = ProviderFactory(store).credentials

    with pytest.raises(CredentialStoreError, match="reserved for Plugin settings"):
        resolver.resolve("plugin.text-tools.api_key")
