from app.contracts import ModelCapability, ProviderConfig, ProviderPreset, ProviderType
from app.providers.base import ModelProvider
from app.providers.credentials import CredentialResolver, ProviderCredentialResolver
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


class UnsupportedProviderError(ValueError):
    pass


class ProviderFactory:
    def __init__(self, credentials: CredentialResolver) -> None:
        # ProviderFactory 是所有可配置 Provider 的创建边界，在此统一禁止
        # Provider 借用 Plugin Secret 引用，避免调用方漏包安全 Resolver。
        self.credentials = ProviderCredentialResolver(credentials)

    def build(self, config: ProviderConfig) -> ModelProvider:
        if config.provider_type in {
            ProviderType.openai_chat,
            ProviderType.openai_compatible,
        }:
            return OpenAICompatibleProvider(
                base_url=config.base_url or "https://api.openai.com/v1",
                credential_id=config.credential_id,
                credentials=self.credentials,
            )
        if config.provider_type == ProviderType.ollama:
            return OllamaProvider(config.base_url or "http://127.0.0.1:11434")
        raise UnsupportedProviderError(config.provider_type.value)

    @staticmethod
    def presets() -> list[ProviderPreset]:
        return [
            ProviderPreset(
                preset_id="openai",
                name="OpenAI",
                provider_type=ProviderType.openai_chat,
                base_url="https://api.openai.com/v1",
                default_credential_id="openai",
            ),
            ProviderPreset(
                preset_id="deepseek",
                name="DeepSeek",
                provider_type=ProviderType.openai_compatible,
                base_url="https://api.deepseek.com",
                default_credential_id="deepseek",
            ),
            ProviderPreset(
                preset_id="ollama",
                name="Ollama",
                provider_type=ProviderType.ollama,
                base_url="http://127.0.0.1:11434",
                requires_credential=False,
            ),
        ]

    @staticmethod
    def capabilities(provider_type: ProviderType) -> list[ModelCapability]:
        if provider_type in {
            ProviderType.openai_chat,
            ProviderType.openai_compatible,
        }:
            return [
                ModelCapability.chat,
                ModelCapability.tool_calling,
                ModelCapability.streaming,
                ModelCapability.structured_output,
            ]
        if provider_type == ProviderType.ollama:
            return [
                ModelCapability.chat,
                ModelCapability.tool_calling,
                ModelCapability.streaming,
            ]
        return []
