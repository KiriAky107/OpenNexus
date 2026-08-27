from uuid import uuid4

import httpx

from app.contracts import ModelCapability, ModelInfo, ModelRequest
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
