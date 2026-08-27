import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from app.contracts import (
    MessageRole,
    ModelCapability,
    ModelEvent,
    ModelEventType,
    ModelInfo,
    ModelRequest,
)
from app.providers.base import ProviderToolCall, ProviderTurn

_TOOL_PATTERN = re.compile(r"^/tool\s+([\w.-]+)(?:\s+(\{.*\}))?\s*$", re.DOTALL)


class MockProvider:
    """离线开发 Provider，用于验证聊天、Tool Calling 和 Agent Loop。"""

    async def complete(self, request: ModelRequest) -> ProviderTurn:
        if not request.messages:
            return ProviderTurn(text="Mock provider received an empty conversation.")

        last_message = request.messages[-1]
        if last_message.role == MessageRole.tool:
            return ProviderTurn(
                text=f"Tool result received: {last_message.content}",
                input_tokens=len(last_message.content.split()),
                output_tokens=4,
            )

        match = _TOOL_PATTERN.match(last_message.content.strip())
        if match:
            raw_arguments = match.group(2) or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                return ProviderTurn(text="Mock tool arguments must be valid JSON.")
            if not isinstance(arguments, dict):
                return ProviderTurn(text="Mock tool arguments must be a JSON object.")
            return ProviderTurn(
                tool_calls=[
                    ProviderToolCall(
                        tool_call_id=f"call_{uuid4().hex}",
                        name=match.group(1),
                        arguments=arguments,
                    )
                ],
                input_tokens=len(last_message.content.split()),
            )

        text = f"Mock response: {last_message.content}"
        return ProviderTurn(
            text=text,
            input_tokens=len(last_message.content.split()),
            output_tokens=len(text.split()),
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        turn = await self.complete(request)
        sequence = 0
        if turn.tool_calls:
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
        elif turn.text:
            words = turn.text.split(" ")
            for index, word in enumerate(words):
                yield ModelEvent(
                    event=ModelEventType.text_delta,
                    sequence=sequence,
                    data={"text": word + (" " if index < len(words) - 1 else "")},
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

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                model="mock-1",
                display_name="Mock Provider (Development)",
                capabilities=[
                    ModelCapability.chat,
                    ModelCapability.tool_calling,
                    ModelCapability.streaming,
                ],
            )
        ]

    async def test_connection(self, model: str | None = None) -> tuple[bool, str]:
        if model not in (None, "mock-1"):
            return False, f"Unknown mock model: {model}"
        return True, "Mock provider is ready."
