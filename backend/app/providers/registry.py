from dataclasses import dataclass
from time import perf_counter
from pathlib import Path

from app.config import get_settings
from app.database.db import connect
from app.errors import ApiError

from app.contracts import ModelInfo, ProviderConfig, ProviderTestResponse
from app.providers.base import ModelProvider


class ProviderNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class RegisteredProvider:
    config: ProviderConfig
    adapter: ModelProvider


class ProviderRegistry:
    def __init__(self, factory=None) -> None:
        self._providers: dict[str, RegisteredProvider] = {}
        self._factory = factory
        self._loaded_path: Path | None = None

    def _restore(self) -> None:
        if self._factory is None or self._loaded_path == get_settings().db_path:
            return
        conn = connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS provider_configs (provider_id TEXT PRIMARY KEY, config_json TEXT NOT NULL)")
            restored = {}
            for row in conn.execute("SELECT config_json FROM provider_configs"):
                config = ProviderConfig.model_validate_json(row["config_json"])
                if config.provider_id == "mock":
                    raise ValueError("reserved provider")
                restored[config.provider_id] = RegisteredProvider(config, self._factory.build(config))
            if "mock" in self._providers:
                restored["mock"] = self._providers["mock"]
            self._providers = restored
            self._loaded_path = get_settings().db_path
        except (ValueError, TypeError) as exc:
            raise ApiError(500, "PROVIDER_STORAGE_INVALID", "Saved provider configuration could not be loaded.") from exc
        finally:
            conn.close()

    def _save(self, config: ProviderConfig) -> None:
        if self._factory is None or config.provider_id == "mock":
            return
        conn = connect()
        try:
            conn.execute("INSERT OR REPLACE INTO provider_configs VALUES (?, ?)", (config.provider_id, config.model_dump_json()))
        finally:
            conn.close()

    def register(self, config: ProviderConfig, adapter: ModelProvider) -> None:
        if config.provider_id != "mock":
            self._restore()
        if config.provider_id in self._providers:
            raise ValueError(f"Provider already registered: {config.provider_id}")
        self._save(config)
        self._providers[config.provider_id] = RegisteredProvider(config=config, adapter=adapter)

    def unregister(self, provider_id: str) -> None:
        self._restore()
        if self._factory is not None:
            conn = connect()
            try:
                conn.execute("DELETE FROM provider_configs WHERE provider_id = ?", (provider_id,))
            finally:
                conn.close()
        self._providers.pop(provider_id, None)

    def replace(self, config: ProviderConfig, adapter: ModelProvider) -> None:
        self._restore()
        if config.provider_id not in self._providers:
            raise ProviderNotFoundError(config.provider_id)
        self._save(config)
        self._providers[config.provider_id] = RegisteredProvider(config=config, adapter=adapter)

    def get(self, provider_id: str) -> RegisteredProvider:
        provider = self.get_any(provider_id)
        if not provider.config.enabled:
            raise ProviderNotFoundError(provider_id)
        return provider

    def get_any(self, provider_id: str) -> RegisteredProvider:
        self._restore()
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderNotFoundError(provider_id) from exc

    def list_configs(self) -> list[ProviderConfig]:
        self._restore()
        return [item.config.model_copy(deep=True) for item in self._providers.values()]

    async def list_models(self, provider_id: str) -> list[ModelInfo]:
        return await self.get(provider_id).adapter.list_models()

    async def test(self, provider_id: str, model: str | None = None) -> ProviderTestResponse:
        provider = self.get(provider_id)
        started = perf_counter()
        success, message = await provider.adapter.test_connection(model)
        latency_ms = round((perf_counter() - started) * 1000)
        return ProviderTestResponse(
            provider_id=provider_id,
            success=success,
            latency_ms=latency_ms,
            message=message,
        )
