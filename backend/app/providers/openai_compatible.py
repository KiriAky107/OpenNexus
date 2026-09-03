import json
from contextlib import aclosing
from uuid import uuid4

import httpx

from app.contracts import MessageRole, ModelCapability, ModelEventType, ModelInfo, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.credentials import CredentialResolver, CredentialStoreError
from app.providers.tool_names import mapped_tool_names
from app.providers.http_base import (
    EventStreamingMixin, HTTPProviderMixin, UsageTracker, decode_tool_arguments,
    invalid_response, list_value, object_value, string_value, token_count, truncated_stream,
)


class OpenAICompatibleProvider(EventStreamingMixin, HTTPProviderMixin):
    def __init__(
        self,
        base_url: str,
        credential_id: str | None,
        credentials: CredentialResolver,
        timeout_seconds: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential_id = credential_id
        self.credentials = credentials
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @mapped_tool_names
    async def complete(self, request: ModelRequest) -> ProviderTurn:
        data = await self._request("POST", self.stream_path, json=self._payload(request, stream=False))
        choices = list_value(data.get("choices"))
        if not choices:
            raise invalid_response()
        message = object_value(object_value(choices[0]).get("message"))
        calls = []
        for raw in list_value(message.get("tool_calls", [])):
            raw = object_value(raw)
            function = object_value(raw.get("function"))
            calls.append(ProviderToolCall(
                tool_call_id=string_value(raw.get("id") or f"call_{uuid4().hex}"),
                name=string_value(function.get("name"), nonempty=True),
                arguments=decode_tool_arguments(function.get("arguments", "{}")),
            ))
        text = message.get("content")
        if text is not None:
            text = string_value(text)
        usage = UsageTracker("prompt_tokens", "completion_tokens").update(data.get("usage") or {})
        return ProviderTurn(text=text, tool_calls=calls, **usage)

    def _payload(self, request: ModelRequest, *, stream: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model, "messages": self._messages(request), "stream": stream,
        }
        if request.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool.name, "description": tool.description, "parameters": tool.parameters,
                }} for tool in request.tools
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _events(self, request: ModelRequest):
        calls: dict[int, dict] = {}
        usage = UsageTracker("prompt_tokens", "completion_tokens")
        finished = False
        seen = False
        async with aclosing(self._stream_json(self._payload(request, stream=True))) as chunks:
            async for data in chunks:
                if data.get("type") == "[DONE]":
                    if not seen:
                        raise invalid_response()
                    finished = True
                    break
                if data.get("usage") is not None:
                    yield ModelEventType.usage, usage.update(data["usage"])
                choices = list_value(data.get("choices", []))
                if not choices:
                    continue
                seen = True
                choice = object_value(choices[0])
                delta = object_value(choice.get("delta") or {})
                if delta.get("reasoning_content"):
                    yield ModelEventType.thinking_delta, {"text": string_value(delta["reasoning_content"])}
                if delta.get("content"):
                    yield ModelEventType.text_delta, {"text": string_value(delta["content"])}
                for raw in list_value(delta.get("tool_calls", [])):
                    raw = object_value(raw)
                    index = token_count(raw.get("index", 0))
                    function = object_value(raw.get("function") or {})
                    call = calls.setdefault(index, {"id": "", "name": "", "arguments": "", "started": False})
                    if raw.get("id"):
                        call["id"] = string_value(raw["id"])
                    if function.get("name"):
                        call["name"] += string_value(function["name"])
                    fragment = string_value(function.get("arguments", ""))
                    call["arguments"] += fragment
                    if not call["started"] and call["name"]:
                        call["id"] = call["id"] or f"call_{uuid4().hex}"
                        call["started"] = True
                        yield ModelEventType.tool_call_start, {"tool_call_id": call["id"], "name": call["name"]}
                        fragment = call["arguments"]
                    if call["started"] and fragment:
                        yield ModelEventType.tool_call_delta, {"tool_call_id": call["id"], "arguments_delta": fragment}
                if choice.get("finish_reason"):
                    finished = True
        if not finished:
            raise truncated_stream()
        for call in calls.values():
            if not call["started"]:
                raise invalid_response()
            decode_tool_arguments(call["arguments"] or "{}")
            yield ModelEventType.tool_call_end, {"tool_call_id": call["id"]}

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/models")
        return [ModelInfo(model=string_value(item["id"]), display_name=item["id"],
                          capabilities=self._model_capabilities(string_value(item["id"])))
                for item in list_value(data.get("data"))
                if isinstance(item, dict) and item.get("id")]

    @staticmethod
    def _model_capabilities(model: str) -> list[ModelCapability]:
        # /models does not advertise capabilities. Avoid known non-chat families;
        # these are discovery hints, not a guarantee of support by a gateway.
        name = model.lower()
        if "embed" in name or name.startswith(("bge-", "bge/")):
            return [ModelCapability.embedding]
        if any(marker in name for marker in (
            "whisper", "tts", "transcri", "audio", "realtime", "dall-e", "image", "moderation", "rerank",
        )):
            return []
        return [ModelCapability.chat]

    async def test_connection(self, model: str | None = None) -> tuple[bool, str]:
        try:
            models = await self.list_models()
        except ProviderError as exc:
            return False, exc.message
        if model and model not in {item.model for item in models}:
            return False, f"Model is not available: {model}"
        return True, f"Connected; discovered {len(models)} model(s)."

    def _messages(self, request: ModelRequest) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        if request.system:
            result.append({"role": "system", "content": request.system})
        for message in request.messages:
            item: dict[str, object] = {"role": message.role.value, "content": message.content}
            if message.name:
                item["name"] = message.name
            if message.role == MessageRole.tool and message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {"id": call.tool_call_id, "type": "function", "function": {
                        "name": call.name, "arguments": json.dumps(call.arguments),
                    }} for call in message.tool_calls
                ]
            result.append(item)
        return result

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        try:
            api_key = self.credentials.resolve(self.credential_id)
        except CredentialStoreError as exc:
            raise ProviderError("PROVIDER_CREDENTIAL_UNAVAILABLE",
                                "Credential could not be decrypted by the AI Core.") from exc
        if self.credential_id and not api_key:
            raise ProviderError("PROVIDER_CREDENTIAL_MISSING",
                                "Credential is not available in the AI Core process.")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
