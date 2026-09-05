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
        adapter = self._build(config)
        adapter.provider_config = config.model_copy(deep=True)
        from app.services.usage_service import usage_context
        from contextlib import aclosing
        from uuid import uuid4
        from app.providers.context_budget import prepare_context
        from app.providers.base import ProviderError
        from app.contracts import ModelEvent, ModelEventType
        from datetime import datetime, timezone
        complete, stream = adapter.complete, adapter.stream
        async def complete_with_trace(request):
            token = usage_context.set({"request_id": uuid4().hex, "run_id": request.metadata.get("run_id")})
            try:
                request = await prepare_context(request, config, complete)
                return await complete(request)
            finally:
                usage_context.reset(token)
        async def stream_with_trace(request):
            sequence = 0
            token = usage_context.set({"request_id": uuid4().hex, "run_id": request.metadata.get("run_id")})
            try:
                original = request
                request = await prepare_context(request, config, complete, stream=True)
                if request.messages != original.messages:
                    yield ModelEvent(event=ModelEventType.context_status, sequence=sequence, timestamp=datetime.now(timezone.utc), data={"message": "本次请求已压缩旧对话；原始记录保留，摘要生成计入用量。"})
                    sequence += 1
                async with aclosing(stream(request)) as events:
                    async for event in events:
                        yield event.model_copy(update={"sequence": sequence})
                        sequence += 1
            except ProviderError as exc:
                yield ModelEvent(event=ModelEventType.error, sequence=sequence, timestamp=datetime.now(timezone.utc), data={"code": exc.code, "message": exc.message})
                yield ModelEvent(event=ModelEventType.done, timestamp=datetime.now(timezone.utc), sequence=sequence + 1, data={"status": "failed"})
            finally:
                usage_context.reset(token)
        adapter.complete, adapter.stream = complete_with_trace, stream_with_trace
        return adapter

    def _build(self, config: ProviderConfig) -> ModelProvider:
        if config.provider_type == ProviderType.openai_responses:
            from app.providers.openai_responses import OpenAIResponsesProvider
            return OpenAIResponsesProvider(
                base_url=config.base_url or "https://api.openai.com/v1",
                credential_id=config.credential_id, credentials=self.credentials,
            )
        if config.provider_type == ProviderType.anthropic_messages:
            from app.providers.anthropic_messages import AnthropicMessagesProvider
            return AnthropicMessagesProvider(
                base_url=config.base_url or "https://api.anthropic.com/v1",
                credential_id=config.credential_id, credentials=self.credentials,
            )
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
        presets = [
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
        # General API endpoints. Coding-plan endpoints and keys are separate products.
        domestic = [
            ("kimi", "Kimi / 月之暗面", "https://api.moonshot.cn/v1", [], "长上下文对话；模型以账号权限为准。"),
            ("qwen", "阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", [ModelCapability.embedding], "中国内地兼容接口；海外地域需修改地址。"),
            ("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", [ModelCapability.embedding], "通用 API；Coding Plan 请使用其专用地址。"),
            ("volcengine", "火山方舟 / 豆包", "https://ark.cn-beijing.volces.com/api/v3", [ModelCapability.embedding], "按账号填写模型 ID 或推理接入点 ID。"),
            ("siliconflow", "硅基流动", "https://api.siliconflow.cn/v1", [ModelCapability.embedding, ModelCapability.transcription], "支持兼容 Embedding 和音频转写接口。"),
            ("baidu", "百度千帆", "https://qianfan.baidubce.com/v2", [ModelCapability.embedding], "使用千帆 API Key；模型列表取决于账号。"),
            ("hunyuan", "腾讯混元", "https://api.hunyuan.cloud.tencent.com/v1", [], "OpenAI 兼容对话接口。"),
            ("minimax", "MiniMax", "https://api.minimaxi.com/v1", [], "文本对话兼容接口；其他媒体协议需独立适配。"),
            ("stepfun", "阶跃星辰", "https://api.stepfun.com/v1", [], "通用 API；Step Plan 请使用其专用地址。"),
        ]
        for preset_id, name, url, extra, description in domestic:
            presets.append(ProviderPreset(
                preset_id=preset_id, name=name, provider_type=ProviderType.openai_compatible,
                base_url=url, default_credential_id=preset_id, logo_id=preset_id,
                capabilities=[ModelCapability.chat, *extra], description=description,
            ))
        presets.extend([
            ProviderPreset(preset_id="openai-responses", name="OpenAI Responses", provider_type=ProviderType.openai_responses,
                           base_url="https://api.openai.com/v1", default_credential_id="openai", logo_id="openai"),
            ProviderPreset(preset_id="anthropic", name="Anthropic / Claude", provider_type=ProviderType.anthropic_messages,
                           base_url="https://api.anthropic.com/v1", default_credential_id="anthropic", logo_id="anthropic"),
        ])
        for preset in presets:
            if preset.logo_id == "custom":
                preset.logo_id = preset.preset_id
            if not preset.capabilities:
                preset.capabilities = [ModelCapability.chat]
        presets[0].capabilities += [ModelCapability.embedding, ModelCapability.transcription]
        return presets

    @staticmethod
    def capabilities(provider_type: ProviderType) -> list[ModelCapability]:
        if provider_type in {
            ProviderType.openai_chat,
            ProviderType.openai_compatible,
            ProviderType.openai_responses,
            ProviderType.anthropic_messages,
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
