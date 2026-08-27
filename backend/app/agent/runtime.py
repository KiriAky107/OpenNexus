import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.permissions import PermissionManager, PermissionMode
from app.agent.tools import ToolExecutionContext, ToolNotFoundError, ToolRegistry
from app.contracts import (
    AgentEvent,
    AgentEventType,
    AgentRun,
    AgentRunCreateRequest,
    AgentRunStatus,
    Message,
    MessageRole,
    ModelRequest,
    ToolCall,
    ToolResult,
)
from app.providers.registry import ProviderRegistry
from app.providers.base import ProviderError


class AgentRunNotFoundError(LookupError):
    pass


TERMINAL_STATUSES = {
    AgentRunStatus.completed,
    AgentRunStatus.failed,
    AgentRunStatus.cancelled,
}


@dataclass(slots=True)
class RunRecord:
    run: AgentRun
    request: AgentRunCreateRequest
    events: list[AgentEvent] = field(default_factory=list)
    subscribers: set[asyncio.Queue[AgentEvent]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


class AgentRuntime:
    def __init__(
        self,
        providers: ProviderRegistry,
        tools: ToolRegistry,
        permissions: PermissionManager,
    ) -> None:
        self.providers = providers
        self.tools = tools
        self.permissions = permissions
        self._records: dict[str, RunRecord] = {}

    async def create_run(self, request: AgentRunCreateRequest) -> AgentRun:
        self.providers.get(request.provider_id)
        now = datetime.now(timezone.utc)
        run = AgentRun(
            run_id=f"run_{uuid4().hex}",
            status=AgentRunStatus.queued,
            input=request.input,
            provider_id=request.provider_id,
            model=request.model,
            skill_id=request.skill_id,
            max_steps=request.max_steps,
            token_budget=request.token_budget,
            created_at=now,
            updated_at=now,
        )
        record = RunRecord(run=run, request=request)
        self._records[run.run_id] = record
        record.task = asyncio.create_task(self._execute(record), name=run.run_id)
        return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> AgentRun:
        return self._get_record(run_id).run.model_copy(deep=True)

    def list_runs(self, limit: int, offset: int) -> tuple[list[AgentRun], int]:
        records = sorted(
            self._records.values(), key=lambda item: item.run.created_at, reverse=True
        )
        items = [item.run.model_copy(deep=True) for item in records[offset : offset + limit]]
        return items, len(records)

    async def cancel(self, run_id: str) -> AgentRun:
        record = self._get_record(run_id)
        if record.run.status in TERMINAL_STATUSES:
            return record.run.model_copy(deep=True)
        record.run.cancelled = True
        record.run.status = AgentRunStatus.cancelled
        record.run.updated_at = datetime.now(timezone.utc)
        self.permissions.cancel_run(run_id)
        self._publish(record, AgentEventType.run_cancelled, {})
        if record.task and not record.task.done():
            record.task.cancel()
        return record.run.model_copy(deep=True)

    def resolve_permission(self, run_id: str, request_id: str, decision: str) -> bool:
        self._get_record(run_id)
        return self.permissions.resolve(run_id, request_id, decision)

    async def events(self, run_id: str) -> AsyncIterator[AgentEvent]:
        record = self._get_record(run_id)
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        record.subscribers.add(queue)
        history = [event.model_copy(deep=True) for event in record.events]
        try:
            for event in history:
                yield event
            if record.run.status in TERMINAL_STATUSES:
                return
            while True:
                event = await queue.get()
                yield event.model_copy(deep=True)
                if event.event in {
                    AgentEventType.run_completed,
                    AgentEventType.run_failed,
                    AgentEventType.run_cancelled,
                }:
                    return
        finally:
            record.subscribers.discard(queue)

    async def wait(self, run_id: str) -> AgentRun:
        record = self._get_record(run_id)
        if record.task:
            try:
                await asyncio.shield(record.task)
            except asyncio.CancelledError:
                pass
        return record.run.model_copy(deep=True)

    async def _execute(self, record: RunRecord) -> None:
        try:
            async with asyncio.timeout(record.request.run_timeout_seconds):
                await self._run_loop(record)
        except asyncio.CancelledError:
            if record.run.status != AgentRunStatus.cancelled:
                self._finish_cancelled(record)
        except TimeoutError:
            self._fail(record, "AGENT_TIMEOUT", "Agent run exceeded its timeout.")
        except ProviderError as exc:
            self._fail(record, exc.code, exc.message)
        except Exception as exc:
            self._fail(record, "AGENT_FAILED", str(exc))

    async def _run_loop(self, record: RunRecord) -> None:
        record.run.status = AgentRunStatus.running
        record.run.updated_at = datetime.now(timezone.utc)
        self._publish(
            record,
            AgentEventType.run_started,
            {"provider_id": record.request.provider_id, "model": record.request.model},
        )

        messages = [Message(role=MessageRole.user, content=record.request.input)]
        allowed_tools = self.tools.definitions(record.request.allowed_tools)
        provider = self.providers.get(record.request.provider_id).adapter

        for step in range(1, record.request.max_steps + 1):
            record.run.current_step = step
            record.run.updated_at = datetime.now(timezone.utc)
            turn = await provider.complete(
                ModelRequest(
                    provider_id=record.request.provider_id,
                    model=record.request.model,
                    messages=messages,
                    tools=allowed_tools,
                    metadata=record.request.metadata,
                )
            )
            record.run.token_usage += turn.input_tokens + turn.output_tokens
            self._publish(
                record,
                AgentEventType.usage,
                {"token_usage": record.run.token_usage},
            )
            if (
                record.request.token_budget is not None
                and record.run.token_usage > record.request.token_budget
            ):
                self._fail(record, "TOKEN_BUDGET_EXCEEDED", "Agent token budget exceeded.")
                return

            if turn.tool_calls:
                calls = [
                    ToolCall(
                        tool_call_id=item.tool_call_id,
                        name=item.name,
                        arguments=item.arguments,
                    )
                    for item in turn.tool_calls
                ]
                messages.append(
                    Message(role=MessageRole.assistant, content=turn.text or "", tool_calls=calls)
                )
                for call in calls:
                    result = await self._execute_tool(record, call)
                    record.run.tool_results.append(result)
                    messages.append(
                        Message(
                            role=MessageRole.tool,
                            name=call.name,
                            tool_call_id=call.tool_call_id,
                            content=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                        )
                    )
                continue

            if turn.text is not None:
                record.run.output = turn.text
                self._publish(record, AgentEventType.text_delta, {"text": turn.text})
                record.run.status = AgentRunStatus.completed
                record.run.updated_at = datetime.now(timezone.utc)
                self._publish(
                    record,
                    AgentEventType.run_completed,
                    {"output": turn.text, "token_usage": record.run.token_usage},
                )
                return

            self._fail(record, "EMPTY_MODEL_RESPONSE", "Provider returned no text or tool call.")
            return

        self._fail(record, "MAX_STEPS_EXCEEDED", "Agent reached its maximum step count.")

    async def _execute_tool(self, record: RunRecord, call: ToolCall) -> ToolResult:
        self._publish(record, AgentEventType.tool_call, call.model_dump(mode="json"))
        try:
            registered = self.tools.get(call.name)
        except ToolNotFoundError:
            registered = None

        if registered is not None and call.name not in record.request.allowed_tools:
            result = ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_NOT_ALLOWED",
                error_message="Tool is not included in allowed_tools.",
            )
            self._publish(record, AgentEventType.tool_result, result.model_dump(mode="json"))
            return result

        permission = registered.definition.permission if registered else None
        mode = self.permissions.mode_for(permission)
        if mode == PermissionMode.deny:
            result = self._permission_denied(call)
        elif mode == PermissionMode.confirm and permission:
            ticket = self.permissions.create_ticket(record.run.run_id, permission)
            record.run.status = AgentRunStatus.waiting_permission
            self._publish(
                record,
                AgentEventType.permission_required,
                {
                    "request_id": ticket.request_id,
                    "permission": permission,
                    "tool_call": call.model_dump(mode="json"),
                },
            )
            try:
                decision = await self.permissions.wait(
                    ticket, timeout=record.request.tool_timeout_seconds
                )
            except TimeoutError:
                record.run.status = AgentRunStatus.running
                result = ToolResult(
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    success=False,
                    error_code="PERMISSION_TIMEOUT",
                    error_message="Tool permission confirmation timed out.",
                )
                self._publish(
                    record, AgentEventType.tool_result, result.model_dump(mode="json")
                )
                return result
            record.run.status = AgentRunStatus.running
            result = (
                await self._invoke_tool(record, call)
                if decision in {"allow_once", "allow_session"}
                else self._permission_denied(call)
            )
        else:
            result = await self._invoke_tool(record, call)

        self._publish(record, AgentEventType.tool_result, result.model_dump(mode="json"))
        return result

    async def _invoke_tool(self, record: RunRecord, call: ToolCall) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self.tools.execute(call, ToolExecutionContext(run_id=record.run.run_id)),
                timeout=record.request.tool_timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="TOOL_TIMEOUT",
                error_message="Tool execution timed out.",
            )

    @staticmethod
    def _permission_denied(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            success=False,
            error_code="PERMISSION_DENIED",
            error_message="Tool permission was denied.",
        )

    def _finish_cancelled(self, record: RunRecord) -> None:
        record.run.cancelled = True
        record.run.status = AgentRunStatus.cancelled
        record.run.updated_at = datetime.now(timezone.utc)
        self._publish(record, AgentEventType.run_cancelled, {})

    def _fail(self, record: RunRecord, code: str, message: str) -> None:
        if record.run.status in TERMINAL_STATUSES:
            return
        record.run.status = AgentRunStatus.failed
        record.run.error_code = code
        record.run.error_message = message
        record.run.updated_at = datetime.now(timezone.utc)
        self._publish(
            record,
            AgentEventType.run_failed,
            {"code": code, "message": message},
        )

    def _publish(
        self, record: RunRecord, event_type: AgentEventType, data: dict[str, object]
    ) -> None:
        event = AgentEvent(
            event=event_type,
            run_id=record.run.run_id,
            sequence=len(record.events),
            data=data,
            timestamp=datetime.now(timezone.utc),
        )
        record.events.append(event)
        for queue in record.subscribers:
            queue.put_nowait(event)

    def _get_record(self, run_id: str) -> RunRecord:
        try:
            return self._records[run_id]
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc
