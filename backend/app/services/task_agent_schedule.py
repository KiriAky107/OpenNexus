"""任务 Agent 调度的原子认领与执行。

计时由桌面前端在 Vault 打开期间触发；Core 负责校验到期时间、串行认领、创建真实
AgentRun，并推进一次性或 Cron 调度，避免多个窗口重复启动同一轮任务。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app import host_bridge
from app.container import container
from app.contracts import AgentRunCreateRequest, Task, TaskAgentSchedule, TaskAgentScheduleInput, TaskAgentScheduleStatus
from app.errors import ApiError
from app.services import task_service

_locks: dict[str, asyncio.Lock] = {}


async def _write(task_id: str, schedule: TaskAgentSchedule) -> Task:
    # 桌面 Host 将 operation_id 用作幂等键；一次调度包含“认领”和“完成”两次独立写入。
    token = host_bridge.operation_id.set(str(uuid4()))
    try:
        return await task_service.write_in_background(
            task_service.update_task, task_id, {"agent_schedule": schedule}
        )
    finally:
        host_bridge.operation_id.reset(token)


def _next(schedule: TaskAgentSchedule, *, run_id: str | None, error: str | None) -> TaskAgentSchedule:
    now = datetime.now(timezone.utc)
    if schedule.schedule_type == "once":
        return schedule.model_copy(update={
            "enabled": False,
            "status": TaskAgentScheduleStatus.completed if run_id else TaskAgentScheduleStatus.failed,
            "next_run_at": None,
            "last_run_at": now,
            "last_run_id": run_id,
            "error": error,
        })
    refreshed = task_service._normalize_schedule(TaskAgentScheduleInput(
        schedule_type="cron",
        cron=schedule.cron,
        timezone=schedule.timezone,
        enabled=schedule.enabled,
        provider_id=schedule.provider_id,
        model=schedule.model,
        skill_id=schedule.skill_id,
        max_steps=schedule.max_steps,
        allow_network=schedule.allow_network,
    ))
    assert refreshed is not None
    return refreshed.model_copy(update={
        "status": TaskAgentScheduleStatus.pending,
        "last_run_at": now,
        "last_run_id": run_id,
        "error": error,
    })


async def run_due(task_id: str) -> Task:
    async with _locks.setdefault(task_id, asyncio.Lock()):
        task = await asyncio.to_thread(task_service.get_task, task_id)
        if task is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id})
        schedule = task.agent_schedule
        if schedule is None or not schedule.enabled:
            raise ApiError(409, "TASK_SCHEDULE_DISABLED", "Task Agent schedule is disabled.")
        if task.status.value in {"done", "cancelled"}:
            raise ApiError(409, "TASK_NOT_ACTIONABLE", "Completed or cancelled tasks cannot start Agent runs.")
        if schedule.status == TaskAgentScheduleStatus.running:
            raise ApiError(409, "TASK_SCHEDULE_RUNNING", "Task Agent schedule is already running.")
        now = datetime.now(timezone.utc)
        if schedule.next_run_at is None or schedule.next_run_at > now:
            raise ApiError(409, "TASK_SCHEDULE_NOT_DUE", "Task Agent schedule is not due yet.", {
                "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
            })

        running = schedule.model_copy(update={"status": TaskAgentScheduleStatus.running, "error": None})
        await _write(task_id, running)
        prompt = f"# {task.title}\n\n{task.description}" if task.description.strip() else task.title
        try:
            run = await container.agent.create_run(AgentRunCreateRequest(
                input=prompt,
                provider_id=schedule.provider_id,
                model=schedule.model,
                skill_id=schedule.skill_id,
                max_steps=schedule.max_steps,
                allow_network=schedule.allow_network,
                metadata={"scheduled_task_id": task_id, "schedule_type": schedule.schedule_type},
            ))
        except Exception as exc:
            await _write(task_id, _next(schedule, run_id=None, error=type(exc).__name__))
            raise ApiError(409, "TASK_AGENT_START_FAILED", "Scheduled Agent run could not be started.", {
                "task_id": task_id,
                "reason": type(exc).__name__,
            }) from exc
        return await _write(task_id, _next(schedule, run_id=run.run_id, error=None))
