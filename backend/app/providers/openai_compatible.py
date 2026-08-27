import json
from uuid import uuid4

import httpx

from app.contracts import MessageRole, ModelCapability, ModelInfo, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.credentials import CredentialResolver
from app.providers.http_base import TurnStreamingMixin, decode_tool_arguments


class OpenAICompatibleProvider(TurnStreamingMixin):
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

    async def complete(self, request: ModelRequest) -> ProviderTurn:
        payload: dict[str, object] = {
            "model": request.model,
            "messages": self._messages(request),
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
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format

        data = await self._request("POST", "/chat/completions", json=payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("PROVIDER_INVALID_RESPONSE", "Missing completion message.") from exc

        tool_calls = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            tool_calls.append(
                ProviderToolCall(
                    tool_call_id=raw_call.get("id") or f"call_{uuid4().hex}",
                    name=function.get("name") or "",
                    arguments=decode_tool_arguments(function.get("arguments", "{}")),
                )
            )
        usage = data.get("usage") or {}
        return ProviderTurn(
            text=message.get("content"),
            tool_calls=tool_calls,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/models")
        return [
            ModelInfo(
                model=item["id"],
                display_name=item["id"],
                capabilities=[
                    ModelCapability.chat,
                    ModelCapability.tool_calling,
                    ModelCapability.streaming,
                ],
            )
            for item in data.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]

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
            item: dict[str, object] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.name:
                item["name"] = message.name
            if message.role == MessageRole.tool and message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            result.append(item)
        return result

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Content-Type": "application/json"}
        api_key = self.credentials.resolve(self.credential_id)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", headers=headers, **kwargs
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Provider request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            code = {
                401: "PROVIDER_AUTH_FAILED",
                404: "MODEL_NOT_FOUND",
                429: "PROVIDER_RATE_LIMITED",
            }.get(exc.response.status_code, "PROVIDER_UNAVAILABLE")
            raise ProviderError(code, f"Provider returned HTTP {exc.response.status_code}.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Provider is unavailable.") from exc
        if not isinstance(data, dict):
            raise ProviderError("PROVIDER_INVALID_RESPONSE", "Provider returned non-object JSON.")
        return data
