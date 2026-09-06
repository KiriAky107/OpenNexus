import json
from contextlib import aclosing
from uuid import uuid4

import httpx

from app.contracts import MessageRole, ModelCapability, ModelEventType, ModelInfo, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.tool_names import mapped_tool_names
from app.providers.http_base import (
    EventStreamingMixin, HTTPProviderMixin, UsageTracker, decode_tool_arguments,
    invalid_response, list_value, object_value, string_value, truncated_stream,
)


class OllamaProvider(EventStreamingMixin, HTTPProviderMixin):
    stream_path = "/api/chat"
    stream_format = "jsonl"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @mapped_tool_names
    async def complete(self, request: ModelRequest) -> ProviderTurn:
        data = await self._request("POST", self.stream_path, json=self._chat_payload(request, stream=False))
        message = object_value(data.get("message"))
        calls = [self._tool_call(raw) for raw in list_value(message.get("tool_calls", []))]
        content = message.get("content")
        if content is not None:
            content = string_value(content)
        return ProviderTurn(text=content or None, tool_calls=calls,
                            **UsageTracker("prompt_eval_count", "eval_count").update(data))

    @staticmethod
    def _tool_call(raw: object) -> ProviderToolCall:
        call = object_value(raw)
        function = object_value(call.get("function"))
        return ProviderToolCall(
            tool_call_id=string_value(call.get("id") or f"call_{uuid4().hex}"),
            name=string_value(function.get("name"), nonempty=True),
            arguments=decode_tool_arguments(function.get("arguments", {})),
        )

    async def _events(self, request: ModelRequest):
        usage = UsageTracker("prompt_eval_count", "eval_count")
        async with aclosing(self._stream_json(self._chat_payload(request, stream=True))) as chunks:
            async for data in chunks:
                message = object_value(data.get("message", {}))
                if message.get("thinking"):
                    yield ModelEventType.thinking_delta, {"text": string_value(message["thinking"])}
                if message.get("content"):
                    yield ModelEventType.text_delta, {"text": string_value(message["content"])}
                for raw in list_value(message.get("tool_calls", [])):
                    call = self._tool_call(raw)
                    yield ModelEventType.tool_call_start, {"tool_call_id": call.tool_call_id, "name": call.name}
                    yield ModelEventType.tool_call_delta, {
                        "tool_call_id": call.tool_call_id,
                        "arguments_delta": json.dumps(call.arguments, ensure_ascii=False),
                    }
                    yield ModelEventType.tool_call_end, {"tool_call_id": call.tool_call_id}
                if "done" in data and not isinstance(data["done"], bool):
                    raise invalid_response()
                if "prompt_eval_count" in data or "eval_count" in data or data.get("done"):
                    yield ModelEventType.usage, usage.update(data)
                if data.get("done") is True:
                    return
        raise truncated_stream()

    def _chat_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, object]:
        messages = []
        names: dict[str, str] = {}
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            item: dict[str, object] = {"role": message.role.value, "content": message.content}
            if message.images: item["images"] = [uri.split(",",1)[1] for uri in message.images]
            if message.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
                names.update({call.tool_call_id: call.name for call in message.tool_calls})
            if message.role == MessageRole.tool:
                name = message.name or names.get(message.tool_call_id or "")
                if name:
                    item["tool_name"] = name
            messages.append(item)
        payload: dict[str, object] = {
            "model": request.model, "messages": messages, "stream": stream,
        }
        if request.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool.name, "description": tool.description, "parameters": tool.parameters,
                }} for tool in request.tools
            ]
        options = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if options:
            payload["options"] = options
        if request.response_format:
            format_ = request.response_format
            if format_.get("type") == "json_object":
                payload["format"] = "json"
            elif format_.get("type") == "json_schema":
                payload["format"] = object_value(object_value(format_.get("json_schema")).get("schema"))
            else:
                payload["format"] = format_
        return payload

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/api/tags")
        return [
            ModelInfo(
                model=string_value(item["name"]), display_name=item["name"],
                capabilities=([ModelCapability.embedding] if "embed" in item["name"].lower()
                              else [ModelCapability.chat, ModelCapability.streaming]),
            )
            for item in list_value(data.get("models"))
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
        ]

    async def test_connection(self, model: str | None = None) -> tuple[bool, str]:
        try:
            models = await self.list_models()
        except ProviderError as exc:
            return False, exc.message
        if model and model not in {item.model for item in models}:
            return False, f"Model is not installed: {model}"
        return True, f"Connected; discovered {len(models)} local model(s)."
