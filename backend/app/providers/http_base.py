import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.contracts import ModelEvent, ModelEventType, ModelRequest
from app.providers.base import ProviderError, ProviderTurn


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
