from fastapi import APIRouter
from pydantic import BaseModel
from app.contracts import ProviderCreateRequest, ProviderConfig, ModelRequest, Message, MessageRole
from app.providers.factory import ProviderFactory
from app.request_overrides import apply_overrides

router = APIRouter(prefix="/api/providers", tags=["Providers"])


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
        build = getattr(adapter, "_payload", None) or adapter._chat_payload
        payload = build(model_request, stream=request.stream)
    return {"body": apply_overrides(payload, config.request_overrides, request.capability,
                                    stream=request.stream if request.capability == "chat" else False),
            "contains_credentials": False, "execution": "preview_only"}
