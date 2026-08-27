from dataclasses import dataclass
from time import perf_counter

from app.contracts import ModelInfo, ProviderConfig, ProviderTestResponse
from app.providers.base import ModelProvider


class ProviderNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class RegisteredProvider:
    config: ProviderConfig
    adapter: ModelProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    def register(self, config: ProviderConfig, adapter: ModelProvider) -> None:
        if config.provider_id in self._providers:
            raise ValueError(f"Provider already registered: {config.provider_id}")
        self._providers[config.provider_id] = RegisteredProvider(config=config, adapter=adapter)

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def replace(self, config: ProviderConfig, adapter: ModelProvider) -> None:
        if config.provider_id not in self._providers:
            raise ProviderNotFoundError(config.provider_id)
        self._providers[config.provider_id] = RegisteredProvider(config=config, adapter=adapter)

    def get(self, provider_id: str) -> RegisteredProvider:
        provider = self.get_any(provider_id)
        if not provider.config.enabled:
            raise ProviderNotFoundError(provider_id)
        return provider

    def get_any(self, provider_id: str) -> RegisteredProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderNotFoundError(provider_id) from exc

    def list_configs(self) -> list[ProviderConfig]:
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
