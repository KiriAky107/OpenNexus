import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime, timezone

import httpx

from app.contracts import ModelEvent, ModelEventType, ModelRequest
from app.providers.base import ProviderError, ProviderTurn
from app.providers.tool_names import prepare_tool_names


class TurnStreamingMixin:
    async def complete(self, request: ModelRequest) -> ProviderTurn:
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        try:
            turn = await self.complete(request)
            sequence = 0
            if turn.text:
                yield ModelEvent(
                    event=ModelEventType.text_delta,
                    sequence=sequence,
                    data={"text": turn.text},
                    timestamp=datetime.now(timezone.utc),
                )
                sequence += 1
            for call in turn.tool_calls:
                yield ModelEvent(
                    event=ModelEventType.tool_call_start,
                    sequence=sequence,
                    data={
                        "tool_call_id": call.tool_call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                    timestamp=datetime.now(timezone.utc),
                )
                sequence += 1
                yield ModelEvent(
                    event=ModelEventType.tool_call_end,
                    sequence=sequence,
                    data={"tool_call_id": call.tool_call_id},
                    timestamp=datetime.now(timezone.utc),
                )
                sequence += 1
            yield ModelEvent(
                event=ModelEventType.usage,
                sequence=sequence,
                data={
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                },
                timestamp=datetime.now(timezone.utc),
            )
            yield ModelEvent(
                event=ModelEventType.done,
                sequence=sequence + 1,
                timestamp=datetime.now(timezone.utc),
            )
        except ProviderError as exc:
            yield ModelEvent(
                event=ModelEventType.error,
                data={"code": exc.code, "message": exc.message},
                timestamp=datetime.now(timezone.utc),
            )
            yield ModelEvent(
                event=ModelEventType.done,
                sequence=1,
                timestamp=datetime.now(timezone.utc),
            )


def decode_tool_arguments(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ProviderError("PROVIDER_INVALID_RESPONSE", "Tool arguments are not JSON.")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProviderError("PROVIDER_INVALID_RESPONSE", "Tool arguments are invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ProviderError("PROVIDER_INVALID_RESPONSE", "Tool arguments must be an object.")
    return decoded


def invalid_response() -> ProviderError:
    return ProviderError("PROVIDER_INVALID_RESPONSE", "Provider returned an invalid response.")


def truncated_stream() -> ProviderError:
    return ProviderError("PROVIDER_STREAM_TRUNCATED", "Provider stream ended before completion.")


def object_value(value: object) -> dict:
    if not isinstance(value, dict):
        raise invalid_response()
    return value


def list_value(value: object) -> list:
    if not isinstance(value, list):
        raise invalid_response()
    return value


def string_value(value: object, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise invalid_response()
    return value


def token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise invalid_response()
    return value


def remote_error(value: object) -> ProviderError:
    # Never reflect upstream messages, URLs, request bodies or credentials.
    error = value if isinstance(value, dict) else {}
    code = error.get("code") or error.get("type")
    mapping = {
        "authentication_error": "PROVIDER_AUTH_FAILED",
        "invalid_api_key": "PROVIDER_AUTH_FAILED",
        "permission_error": "PROVIDER_AUTH_FAILED",
        "rate_limit_error": "PROVIDER_RATE_LIMITED",
        "rate_limit_exceeded": "PROVIDER_RATE_LIMITED",
        "insufficient_quota": "PROVIDER_RATE_LIMITED",
        "not_found_error": "MODEL_NOT_FOUND",
        "model_not_found": "MODEL_NOT_FOUND",
        "invalid_request_error": "PROVIDER_INVALID_REQUEST",
        "context_length_exceeded": "PROVIDER_INVALID_REQUEST",
    }
    mapped = mapping.get(code, "PROVIDER_UNAVAILABLE") if isinstance(code, str) else "PROVIDER_UNAVAILABLE"
    return ProviderError(mapped, "Provider could not complete the request.")


def check_error(data: dict) -> None:
    if data.get("error") is not None or data.get("type") == "error":
        raise remote_error(data.get("error") or data)


class UsageTracker:
    """Merge cumulative snapshots, including partial usage updates."""

    def __init__(self, input_key: str = "input_tokens", output_key: str = "output_tokens",
                 *, cache_tokens: bool = False) -> None:
        self.input_key = input_key
        self.output_key = output_key
        self.cache_tokens = cache_tokens
        self.counts: dict[str, int] = {}

    def update(self, value: object) -> dict[str, int]:
        usage = object_value(value)
        keys = [self.input_key, self.output_key]
        if self.cache_tokens:
            keys += ["cache_creation_input_tokens", "cache_read_input_tokens"]
        for key in keys:
            if key in usage:
                self.counts[key] = max(self.counts.get(key, 0), token_count(usage[key]))
        inputs = self.counts.get(self.input_key, 0)
        if self.cache_tokens:
            inputs += sum(self.counts.get(key, 0) for key in keys[2:])
        return {"input_tokens": inputs, "output_tokens": self.counts.get(self.output_key, 0)}


class EventStreamingMixin:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        sequence = 0
        status = "completed"
        try:
            request, originals = prepare_tool_names(request)
            # Closing the public iterator must synchronously close every nested iterator.
            async with aclosing(self._events(request)) as events:
                async for kind, data in events:
                    if kind == ModelEventType.tool_call_start and "name" in data:
                        data = {**data, "name": originals.get(data["name"], data["name"])}
                    if kind == ModelEventType.usage:
                        data = {**data, "total_tokens": data["input_tokens"] + data["output_tokens"]}
                    yield ModelEvent(event=kind, data=data, sequence=sequence,
                                     timestamp=datetime.now(timezone.utc))
                    sequence += 1
        except ProviderError as exc:
            status = "failed"
            yield ModelEvent(event=ModelEventType.error, sequence=sequence,
                             data={"code": exc.code, "message": exc.message},
                             timestamp=datetime.now(timezone.utc))
            sequence += 1
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
            status = "failed"
            error = invalid_response()
            yield ModelEvent(event=ModelEventType.error, sequence=sequence,
                             data={"code": error.code, "message": error.message},
                             timestamp=datetime.now(timezone.utc))
            sequence += 1
        # CancelledError and GeneratorExit deliberately propagate without a Done event.
        yield ModelEvent(event=ModelEventType.done, sequence=sequence,
                         data={"status": status},
                         timestamp=datetime.now(timezone.utc))


async def sse_objects(response: httpx.Response) -> AsyncIterator[dict]:
    """Read SSE frames, accepting the adjacent data lines used by some gateways."""
    parts: list[str] = []
    event_name = ""

    def decode() -> dict:
        value = "\n".join(parts)
        if value.strip() == "[DONE]":
            return {"type": "[DONE]"}
        try:
            data = object_value(json.loads(value))
        except (ValueError, TypeError) as exc:
            raise invalid_response() from exc
        if event_name and "type" not in data:
            data["type"] = event_name
        check_error(data)
        return data

    async for line in response.aiter_lines():
        if not line:
            if parts:
                yield decode()
            parts = []
            event_name = ""
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            if parts:
                yield decode()
                parts = []
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            if parts:
                # Legacy compatible endpoints sometimes omit blank separators.
                try:
                    json.loads("\n".join(parts))
                except ValueError:
                    pass
                else:
                    yield decode()
                    parts = []
                    event_name = ""
            parts.append(line[5:].removeprefix(" "))
    if parts:
        yield decode()


class HTTPProviderMixin:
    stream_path = "/chat/completions"
    stream_format = "sse"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    @staticmethod
    def _status_error(exc: httpx.HTTPStatusError) -> ProviderError:
        status = exc.response.status_code
        code = {400: "PROVIDER_INVALID_REQUEST", 401: "PROVIDER_AUTH_FAILED",
                403: "PROVIDER_AUTH_FAILED", 404: "MODEL_NOT_FOUND",
                408: "PROVIDER_TIMEOUT", 413: "PROVIDER_INVALID_REQUEST",
                422: "PROVIDER_INVALID_REQUEST", 429: "PROVIDER_RATE_LIMITED"}.get(
                    status, "PROVIDER_UNAVAILABLE")
        return ProviderError(code, f"Provider returned HTTP {status}.")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
                response.raise_for_status()
                data = object_value(response.json())
                check_error(data)
                return data
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Provider request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Provider is unavailable.") from exc
        except (ValueError, TypeError) as exc:
            raise invalid_response() from exc

    async def _stream_json(self, payload: dict[str, object]) -> AsyncIterator[dict]:
        headers = self._headers()
        headers["Accept"] = "text/event-stream" if self.stream_format == "sse" else "application/x-ndjson"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}{self.stream_path}",
                                         headers=headers, json=payload) as response:
                    response.raise_for_status()
                    if self.stream_format == "sse":
                        async with aclosing(sse_objects(response)) as objects:
                            async for data in objects:
                                yield data
                    else:
                        async for line in response.aiter_lines():
                            if line.strip():
                                data = object_value(json.loads(line))
                                check_error(data)
                                yield data
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Provider request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Provider is unavailable.") from exc
        except (ValueError, TypeError) as exc:
            raise invalid_response() from exc
