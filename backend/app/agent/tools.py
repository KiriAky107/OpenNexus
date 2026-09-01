"""Agent 工具注册与执行边界。"""

import inspect
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from app.contracts import ToolCall, ToolDefinition, ToolResult

ToolExecutor = Callable[[BaseModel, "ToolExecutionContext"], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    run_id: str
    tool_call_id: str | None = None


@dataclass(slots=True)
class RegisteredTool:
    definition: ToolDefinition
    arguments_model: type[BaseModel]
    executor: ToolExecutor


class ToolNotFoundError(LookupError):
    pass


class ToolExecutionError(RuntimeError):
    """Executor 可预期失败，保留领域错误码而不是折叠成通用异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolRegistry:
    """统一校验工具入参并隔离执行异常，避免单个工具击穿 Agent 主循环。"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._lock = threading.RLock()

    def register(
        self,
        definition: ToolDefinition,
        arguments_model: type[BaseModel],
        executor: ToolExecutor,
    ) -> None:
        with self._lock:
            if definition.name in self._tools:
                raise ValueError(f"Tool already registered: {definition.name}")
            self._tools[definition.name] = RegisteredTool(
                definition=definition,
                arguments_model=arguments_model,
                executor=executor,
            )

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def contains(self, name: str) -> bool:
        with self._lock:
            return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        with self._lock:
            try:
                return self._tools[name]
            except KeyError as exc:
                raise ToolNotFoundError(name) from exc

    def definitions(self, allowed: list[str] | None = None) -> list[ToolDefinition]:
        names = set(allowed) if allowed is not None else None
        with self._lock:
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
            # JSON Schema 约束模型可见的协议，Pydantic 再完成运行时类型转换。
            Draft202012Validator(registered.definition.parameters).validate(call.arguments)
            arguments = registered.arguments_model.model_validate(call.arguments)
        except (ValidationError, JsonSchemaValidationError) as exc:
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
        except ToolExecutionError as exc:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code=exc.code,
                error_message=exc.message,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        except Exception as exc:  # 工具失败转换成结构化结果，由模型决定是否降级或重试。
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_EXECUTION_FAILED",
                error_message=str(exc),
                duration_ms=round((perf_counter() - started) * 1000),
            )
