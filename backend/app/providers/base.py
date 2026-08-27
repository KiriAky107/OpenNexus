from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from app.contracts import ModelEvent, ModelInfo, ModelRequest


@dataclass(slots=True)
class ProviderToolCall:
    tool_call_id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderTurn:
    text: str | None = None
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ProviderTurn: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def test_connection(self, model: str | None = None) -> tuple[bool, str]: ...
