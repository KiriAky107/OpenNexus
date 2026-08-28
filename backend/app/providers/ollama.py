from uuid import uuid4
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.contracts import ModelCapability, ModelEvent, ModelEventType, ModelInfo, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.http_base import TurnStreamingMixin, decode_tool_arguments


class OllamaProvider(TurnStreamingMixin):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def complete(self, request: ModelRequest) -> ProviderTurn:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            item: dict[str, object] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    }
                    for call in message.tool_calls
                ]
            messages.append(item)
        payload: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        data = await self._request("POST", "/api/chat", json=payload)
        message = data.get("message") or {}
        tool_calls = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            tool_calls.append(
                ProviderToolCall(
                    tool_call_id=raw_call.get("id") or f"call_{uuid4().hex}",
                    name=function.get("name") or "",
                    arguments=decode_tool_arguments(function.get("arguments", {})),
                )
            )
        return ProviderTurn(
            text=message.get("content") or None,
            tool_calls=tool_calls,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/api/tags")
        return [
            ModelInfo(
                model=item["name"],
                display_name=item.get("name", ""),
                capabilities=[ModelCapability.chat, ModelCapability.streaming],
            )
            for item in data.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        payload = self._chat_payload(request, stream=True)
        sequence = 0

        def event(kind: ModelEventType, data: dict | None = None) -> ModelEvent:
            nonlocal sequence
            item = ModelEvent(
                event=kind, sequence=sequence, data=data or {},
                timestamp=datetime.now(timezone.utc),
            )
            sequence += 1
            return item

        try:
            async for data in self._stream_json(payload):
                message = data.get("message") or {}
                if message.get("thinking"):
                    yield event(ModelEventType.thinking_delta, {"text": message["thinking"]})
                if message.get("content"):
                    yield event(ModelEventType.text_delta, {"text": message["content"]})
                for raw_call in message.get("tool_calls") or []:
                    function = raw_call.get("function") or {}
                    call_id = raw_call.get("id") or f"call_{uuid4().hex}"
                    yield event(
                        ModelEventType.tool_call_start,
                        {"tool_call_id": call_id, "name": function.get("name") or ""},
                    )
                    yield event(
                        ModelEventType.tool_call_delta,
                        {
                            "tool_call_id": call_id,
                            "arguments_delta": json.dumps(
                                function.get("arguments") or {}, ensure_ascii=False
                            ),
                        },
                    )
                    yield event(ModelEventType.tool_call_end, {"tool_call_id": call_id})
                if data.get("done"):
                    yield event(
                        ModelEventType.usage,
                        {
                            "input_tokens": int(data.get("prompt_eval_count") or 0),
                            "output_tokens": int(data.get("eval_count") or 0),
                        },
                    )
            yield event(ModelEventType.done)
        except ProviderError as exc:
            yield event(ModelEventType.error, {"code": exc.code, "message": exc.message})
            yield event(ModelEventType.done)

    def _chat_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, object]:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            item: dict[str, object] = {"role": message.role.value, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
            messages.append(item)
        payload: dict[str, object] = {
            "model": request.model, "messages": messages, "stream": stream
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        return payload

    async def _stream_json(self, payload: dict[str, object]) -> AsyncIterator[dict]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ProviderError(
                                "PROVIDER_INVALID_RESPONSE", "Ollama returned invalid JSONL."
                            ) from exc
                        if isinstance(data, dict):
                            yield data
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Ollama request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "MODEL_NOT_FOUND" if exc.response.status_code == 404 else "PROVIDER_UNAVAILABLE",
                f"Ollama returned HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Ollama is unavailable.") from exc

    async def test_connection(self, model: str | None = None) -> tuple[bool, str]:
        try:
            models = await self.list_models()
        except ProviderError as exc:
            return False, exc.message
        if model and model not in {item.model for item in models}:
            return False, f"Model is not installed: {model}"
        return True, f"Connected; discovered {len(models)} local model(s)."

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Ollama request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "MODEL_NOT_FOUND" if exc.response.status_code == 404 else "PROVIDER_UNAVAILABLE",
                f"Ollama returned HTTP {exc.response.status_code}.",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Ollama is unavailable.") from exc
        if not isinstance(data, dict):
            raise ProviderError("PROVIDER_INVALID_RESPONSE", "Ollama returned non-object JSON.")
        return data
