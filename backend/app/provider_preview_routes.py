from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.contracts import ProviderCreateRequest, ProviderConfig, ModelRequest, Message, MessageRole
from app.providers.factory import ProviderFactory
from app.request_overrides import RequestOverride, apply_overrides

router = APIRouter(prefix="/api/providers", tags=["Providers"])


class RulesTransfer(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    request_overrides: list[RequestOverride] = Field(max_length=100)


@router.post("/request-rules/validate")
async def validate_rules(request: RulesTransfer):
    return request


class ProbeRequest(BaseModel):
    provider: ProviderCreateRequest
    stream: bool = True


@router.post("/request-probe")
async def probe(request: ProbeRequest):
    """显式用户触发的推理；没有库上下文、工具或媒体上传。"""
    import asyncio
    from contextlib import aclosing
    from app.container import container
    from app.errors import ApiError
    from app.providers.base import ProviderError
    from app.providers.factory import UnsupportedProviderError
    config = ProviderConfig(provider_id="request-probe", **request.provider.model_dump())
    if not config.default_model:
        raise ApiError(422, "MODEL_REQUIRED", "请填写要验证的模型 ID。")
    try:
        adapter = container.provider_factory.build(config)
        model_request = ModelRequest(provider_id=config.provider_id, model=config.default_model,
            messages=[Message(role=MessageRole.user, content="Reply with OK.")], max_tokens=32)
        received = False
        async with asyncio.timeout(45):
            if request.stream:
                async with aclosing(adapter.stream(model_request)) as events:
                    async for event in events:
                        if event.event.value in {"TextDelta", "ThinkingDelta"}:
                            received = received or bool(str(event.data.get("text") or "").strip())
                        if event.event.value == "Error":
                            raise ProviderError("PROVIDER_PROBE_FAILED", "模型返回了错误事件。")
            else:
                response = await adapter.complete(model_request)
                received = bool(response.text and response.text.strip())
        if not received:
            raise ApiError(422, "PROVIDER_EMPTY_RESPONSE", "请求未返回有效文本，不能标记验证通过。")
    except ProviderError as exc:
        raise ApiError(502, exc.code, "推理验证失败，请检查模型、凭据和自定义参数。") from exc
    except TimeoutError as exc:
        raise ApiError(504, "PROVIDER_TIMEOUT", "推理验证超时。") from exc
    except UnsupportedProviderError as exc:
        raise ApiError(422, "PROVIDER_TYPE_UNSUPPORTED", "该协议不支持推理验证。") from exc
    return {"success": True, "stream": request.stream, "model": config.default_model,
            "message": "当前请求配置已通过实际推理验证。"}


class PreviewRequest(BaseModel):
    provider: ProviderCreateRequest
    stream: bool = True
    capability: str = "chat"


@router.post("/request-preview")
async def preview(request: PreviewRequest):
    class NoCredentials:
        def resolve(self, key):
            return None
    config = ProviderConfig(provider_id="preview", **request.provider.model_dump())
    if request.capability != "chat":
        from app.errors import ApiError
        if request.capability not in {"embedding", "transcription", "speaker_matching"}:
            raise ApiError(422, "INVALID_CAPABILITY", "Unknown capability.")
        payload = {"model": config.default_model or "<模型 ID>"}
        payload["input" if request.capability == "embedding" else "file"] = "<运行时输入，不包含正文或文件>"
        if request.capability == "speaker_matching":
            payload["reference_file"] = "<声纹参考附件>"
    else:
        from app.providers.factory import UnsupportedProviderError
        from app.errors import ApiError
        try:
            adapter = ProviderFactory(NoCredentials()).build(config)
        except UnsupportedProviderError as exc:
            raise ApiError(422, "PROVIDER_TYPE_UNSUPPORTED", "该协议不支持请求预览。") from exc
        model_request = ModelRequest(provider_id="preview", model=config.default_model or "<模型 ID>",
            messages=[Message(role=MessageRole.user, content="<运行时消息，已隐藏>")])
        policy = next((p for p in config.context_policies if p.model == model_request.model), None)
        if policy:
            model_request.max_tokens = policy.output_reserve
        build = getattr(adapter, "_payload", None) or adapter._chat_payload
        payload = build(model_request, stream=request.stream)
    return {"body": apply_overrides(payload, config.request_overrides, request.capability,
                                    stream=request.stream if request.capability == "chat" else False),
            "contains_credentials": False, "execution": "preview_only"}
