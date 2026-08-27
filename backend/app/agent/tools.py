import inspect
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.contracts import ToolCall, ToolDefinition, ToolResult

ToolExecutor = Callable[[BaseModel, "ToolExecutionContext"], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    run_id: str


@dataclass(slots=True)
class RegisteredTool:
    definition: ToolDefinition
    arguments_model: type[BaseModel]
    executor: ToolExecutor


class ToolNotFoundError(LookupError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        definition: ToolDefinition,
        arguments_model: type[BaseModel],
        executor: ToolExecutor,
    ) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = RegisteredTool(
            definition=definition,
            arguments_model=arguments_model,
            executor=executor,
        )

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def definitions(self, allowed: list[str] | None = None) -> list[ToolDefinition]:
        names = set(allowed) if allowed is not None else None
        return [
            item.definition.model_copy(deep=True)
            for name, item in self._tools.items()
            if names is None or name in names
        ]

    async def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        started = perf_counter()
        try:
            registered = self.get(call.name)
        except ToolNotFoundError:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_NOT_FOUND",
                error_message=f"Tool is not registered: {call.name}",
            )

        try:
            arguments = registered.arguments_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_ARGUMENT_INVALID",
                error_message=str(exc),
                duration_ms=round((perf_counter() - started) * 1000),
            )

        try:
            output = registered.executor(arguments, context)
            if inspect.isawaitable(output):
                output = await output
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=True,
                output=output,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        except Exception as exc:  # Tool failures are isolated from the Agent loop.
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_EXECUTION_FAILED",
                error_message=str(exc),
                duration_ms=round((perf_counter() - started) * 1000),
            )
