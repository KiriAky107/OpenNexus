"""Agent 运行时：负责模型轮次、工具调用、权限确认与事件发布。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from app.agent.permissions import PermissionManager, PermissionMode
from app.agent.tools import ToolExecutionContext, ToolNotFoundError, ToolRegistry
from app.contracts import (
    AgentEvent,
    AgentEventType,
    AgentRun,
    AgentRunCreateRequest,
    AgentRunStatus,
    Citation,
    Message,
    MessageRole,
    ModelRequest,
    ToolCall,
    ToolResult,
)
from app.providers.registry import ProviderRegistry
from app.providers.base import ProviderError

if TYPE_CHECKING:
    from app.extensions import AgentConfiguration, SkillRuntime


class AgentRunNotFoundError(LookupError):
    pass


class AgentCapacityError(RuntimeError):
    pass


TERMINAL_STATUSES = {
    AgentRunStatus.completed,
    AgentRunStatus.failed,
    AgentRunStatus.cancelled,
}
MAX_RUN_RECORDS = 200
MAX_EVENTS_PER_RUN = 2_000
MAX_TOOL_CALLS_PER_TURN = 50


@dataclass(slots=True)
class RunRecord:
    """单次运行的可变上下文，仅由 AgentRuntime 持有。"""

    run: AgentRun
    request: AgentRunCreateRequest
    skill_config: AgentConfiguration | None = None
    allowed_tools: list[str] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    subscribers: set[asyncio.Queue[AgentEvent]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


class AgentRuntime:
    """进程内 Agent 编排器；对外返回深拷贝，避免调用方修改运行状态。"""

    def __init__(
        self,
        providers: ProviderRegistry,
        tools: ToolRegistry,
        permissions: PermissionManager,
        skills: SkillRuntime | None = None,
    ) -> None:
        self.providers = providers
        self.tools = tools
        self.permissions = permissions
        self.skills = skills
        self._records: dict[str, RunRecord] = {}

    async def create_run(self, request: AgentRunCreateRequest) -> AgentRun:
        self._prune_records()
        provider = self.providers.get(request.provider_id)
        skill_config = None
        if request.skill_id:
            if self.skills is None:
                raise RuntimeError("Skill Runtime is not configured.")
            skill_config = self.skills.build_agent_configuration(
                request.skill_id, provider.config.capabilities
            )
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
        allowed_tools = list(request.allowed_tools)
        if skill_config is not None:
            # 同时指定 Skill 与工具白名单时取交集，避免 Skill 扩大调用权限。
            allowed_tools = (
                [name for name in skill_config.allowed_tools if name in allowed_tools]
                if allowed_tools
                else list(skill_config.allowed_tools)
            )
        record = RunRecord(
            run=run,
            request=request,
            skill_config=skill_config,
            allowed_tools=allowed_tools,
        )
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
        # 先回放快照再订阅实时事件，使晚加入的 SSE 客户端也能恢复界面状态。
        # TODO(agent): 持久化事件并支持 Last-Event-ID，进程重启后仍可续传。
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
        allowed_tools = self.tools.definitions(record.allowed_tools)
        provider = self.providers.get(record.request.provider_id).adapter

        for step in range(1, record.request.max_steps + 1):
            record.run.current_step = step
            record.run.updated_at = datetime.now(timezone.utc)
            turn = await provider.complete(
                ModelRequest(
                    provider_id=record.request.provider_id,
                    model=record.request.model,
                    system=(record.skill_config.system_prompt if record.skill_config else None),
                    messages=messages,
                    tools=allowed_tools,
                    metadata=self._request_metadata(record),
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
                if len(turn.tool_calls) > MAX_TOOL_CALLS_PER_TURN:
                    self._fail(
                        record,
                        "TOO_MANY_TOOL_CALLS",
                        f"Provider requested more than {MAX_TOOL_CALLS_PER_TURN} tools in one turn.",
                    )
                    return
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
                # 工具可以并发执行，但结果按模型原始调用顺序写回上下文，保证轮次可复现。
                semaphore = asyncio.Semaphore(record.request.max_concurrent_tools)

                async def execute(call: ToolCall) -> ToolResult:
                    async with semaphore:
                        return await self._execute_tool(record, call)

                results = await asyncio.gather(*(execute(call) for call in calls))
                for call, result in zip(calls, results):
                    record.run.tool_results.append(result)
                    self._collect_citations(record, result)
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

        if registered is not None and call.name not in record.allowed_tools:
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
        if permission == "network.request" and not record.request.allow_network:
            result = ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                success=False,
                error_code="NETWORK_NOT_ALLOWED",
                error_message="Agent run does not allow network tools.",
            )
            self._publish(record, AgentEventType.tool_result, result.model_dump(mode="json"))
            return result
        mode = self.permissions.mode_for(permission)
        if mode == PermissionMode.deny:
            result = self._permission_denied(call)
        elif mode == PermissionMode.confirm and permission:
            # 运行状态必须在等待期间可见，前端才能展示并处理权限确认卡片。
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
        # 内存事件只保留最近窗口；完整审计轨迹应由后续持久化层承担。
        if len(record.events) > MAX_EVENTS_PER_RUN:
            del record.events[: len(record.events) - MAX_EVENTS_PER_RUN]
        for queue in record.subscribers:
            queue.put_nowait(event)

    @staticmethod
    def _request_metadata(record: RunRecord) -> dict[str, object]:
        metadata = dict(record.request.metadata)
        if record.skill_config is not None:
            metadata["skill_id"] = record.skill_config.skill_id
            metadata["retrieval"] = record.skill_config.retrieval.model_dump(mode="json")
        return metadata

    def _collect_citations(self, record: RunRecord, result: ToolResult) -> None:
        if not result.success or not isinstance(result.output, dict):
            return
        items = result.output.get("items")
        if not isinstance(items, list):
            return
        known = {citation.citation_id for citation in record.run.citations}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("citation"), dict):
                continue
            try:
                citation = Citation.model_validate(item["citation"])
            except ValueError:
                continue
            if citation.citation_id in known:
                continue
            known.add(citation.citation_id)
            record.run.citations.append(citation)
            self._publish(record, AgentEventType.citation, citation.model_dump(mode="json"))

    def _get_record(self, run_id: str) -> RunRecord:
        try:
            return self._records[run_id]
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc

    def _prune_records(self) -> None:
        # 只清理终态记录，绝不为了容量取消仍在执行或等待授权的任务。
        overflow = len(self._records) - MAX_RUN_RECORDS + 1
        if overflow <= 0:
            return
        terminal = sorted(
            (
                record
                for record in self._records.values()
                if record.run.status in TERMINAL_STATUSES
            ),
            key=lambda record: record.run.updated_at,
        )
        for record in terminal[:overflow]:
            self._records.pop(record.run.run_id, None)
        if len(self._records) >= MAX_RUN_RECORDS:
            raise AgentCapacityError("Too many active Agent runs.")
