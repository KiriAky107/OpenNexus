import asyncio
from datetime import datetime, timezone

import pytest

from app.agent.trace_repository import AgentTraceRepository
from app.agent.permissions import PermissionMode
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.database.db import connect
from app.errors import ApiError
from app.routes import agent_events
from app.contracts import (
    AgentEventType,
    AgentRun,
    AgentRunCreateRequest,
    AgentRunStatus,
    ToolCall,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_mock_provider_completes_agent_run() -> None:
    async def scenario() -> None:
        container = build_container()
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input="hello",
                provider_id="mock",
                model="mock-1",
            )
        )

        completed = await container.agent.wait(created.run_id)
        events = [event async for event in container.agent.events(created.run_id)]

        assert completed.status == AgentRunStatus.completed
        assert completed.output == "Mock response: hello"
        assert events[0].event == AgentEventType.run_started
        assert events[-1].event == AgentEventType.run_completed

    run(scenario())


def test_agent_calls_registered_tool_and_records_result() -> None:
    async def scenario() -> None:
        container = build_container()
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool system.echo {"text":"hello tool"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
            )
        )

        completed = await container.agent.wait(created.run_id)

        assert completed.status == AgentRunStatus.completed
        assert completed.current_step == 2
        assert completed.tool_results[0].success is True
        assert completed.tool_results[0].output == {"text": "hello tool"}
        assert completed.output is not None
        assert "Tool result received" in completed.output

    run(scenario())


def test_tool_arguments_are_validated() -> None:
    async def scenario() -> None:
        container = build_container()
        result = await container.tools.execute(
            ToolCall(
                tool_call_id="call_invalid",
                name="math.add",
                arguments={"left": 1},
            ),
            ToolExecutionContext(run_id="run_test"),
        )

        assert result.success is False
        assert result.error_code == "TOOL_ARGUMENT_INVALID"

    run(scenario())


def test_permission_confirmation_resumes_agent() -> None:
    async def scenario() -> None:
        container = build_container()
        protected_tool = container.tools.get("system.echo")
        protected_tool.definition.permission = "notes.write"
        container.permissions.policy.set_rule("notes.write", PermissionMode.confirm)

        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool system.echo {"text":"approved"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
                tool_timeout_seconds=2,
            )
        )

        request_id = None
        async with asyncio.timeout(2):
            async for event in container.agent.events(created.run_id):
                if event.event == AgentEventType.permission_required:
                    request_id = str(event.data["request_id"])
                    break

        assert request_id is not None
        assert await container.agent.resolve_permission(
            created.run_id, request_id, "allow_once"
        )
        completed = await container.agent.wait(created.run_id)
        events = [event async for event in container.agent.events(created.run_id)]
        assert completed.status == AgentRunStatus.completed
        assert completed.tool_results[0].success is True
        assert AgentEventType.permission_resolved in {event.event for event in events}

    run(scenario())


def test_agent_trace_persists_and_replays_from_sequence() -> None:
    async def scenario() -> None:
        first = build_container()
        created = await first.agent.create_run(
            AgentRunCreateRequest(
                input="persistent trace",
                provider_id="mock",
                model="mock-1",
                metadata={"suite": "agent-benchmark-v1"},
            )
        )
        completed = await first.agent.wait(created.run_id)

        restarted = build_container()
        restored = restarted.agent.get_run(created.run_id)
        first_page = restarted.agent.get_trace(
            created.run_id, after_sequence=-1, limit=2
        )
        second_page = restarted.agent.get_trace(
            created.run_id,
            after_sequence=first_page.next_sequence,
            limit=100,
        )
        replay = [
            event
            async for event in restarted.agent.events(
                created.run_id, after_sequence=first_page.next_sequence
            )
        ]

        assert completed.status == restored.status == AgentRunStatus.completed
        assert first_page.has_more is True
        assert [item.sequence for item in first_page.items] == [0, 1]
        assert second_page.items[0].sequence == 2
        assert replay == second_page.items
        assert first_page.summary.model_calls == 1
        assert first_page.summary.token_usage == completed.token_usage
        assert first_page.config_snapshot["metadata"] == {
            "suite": "agent-benchmark-v1"
        }
        assert second_page.items[-1].event == AgentEventType.run_completed

    run(scenario())


def test_interrupted_persisted_run_is_closed_after_restart() -> None:
    now = datetime.now(timezone.utc)
    request = AgentRunCreateRequest(
        input="interrupted",
        provider_id="mock",
        model="mock-1",
    )
    persisted = AgentRun(
        run_id="run_interrupted",
        status=AgentRunStatus.running,
        input=request.input,
        provider_id=request.provider_id,
        model=request.model,
        max_steps=request.max_steps,
        created_at=now,
        updated_at=now,
    )
    AgentTraceRepository().create_run(persisted, request, {"model": "mock-1"})

    restarted = build_container()
    recovered = restarted.agent.get_run(persisted.run_id)
    events = run(
        _collect_events(restarted.agent.events(persisted.run_id, after_sequence=-1))
    )

    assert recovered.status == AgentRunStatus.failed
    assert recovered.error_code == "AGENT_PROCESS_RESTARTED"
    assert events[-1].event == AgentEventType.run_failed
    assert events[-1].sequence == 0


def test_trace_redacts_secrets_and_truncates_large_values() -> None:
    async def scenario() -> None:
        container = build_container()
        secret = "sk-should-not-be-stored"
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input=f'/tool system.echo {{"text":"{"x" * 4200}","api_key":"{secret}"}}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
                metadata={"authorization": secret},
            )
        )
        await container.agent.wait(created.run_id)
        trace = container.agent.get_trace(
            created.run_id, after_sequence=-1, limit=100
        )
        tool_call = next(
            item for item in trace.items if item.event == AgentEventType.tool_call
        )

        assert tool_call.data["arguments"]["api_key"] == "[REDACTED]"
        assert str(tool_call.data["arguments"]["text"]).endswith("...[TRUNCATED]")
        assert trace.config_snapshot["metadata"]["authorization"] == "[REDACTED]"
        assert secret not in trace.model_dump_json()
        conn = connect()
        try:
            stored_row = conn.execute(
                """
                SELECT run_json, request_json, config_snapshot_json
                FROM agent_runs WHERE run_id = ?
                """,
                (created.run_id,),
            ).fetchone()
            stored = "\n".join(str(value) for value in stored_row)
        finally:
            conn.close()
        assert secret not in stored

    run(scenario())


def test_persisted_agent_run_preserves_long_input_and_output() -> None:
    """审计事件可以限长，但重启后读取的 AgentRun 不能丢失正文。"""

    now = datetime.now(timezone.utc)
    long_input = "输入" * 2_500
    long_output = "输出" * 2_500
    request = AgentRunCreateRequest(
        input=long_input,
        provider_id="mock",
        model="mock-1",
    )
    persisted = AgentRun(
        run_id="run_long_content",
        status=AgentRunStatus.completed,
        input=long_input,
        output=long_output,
        provider_id=request.provider_id,
        model=request.model,
        max_steps=request.max_steps,
        created_at=now,
        updated_at=now,
    )
    repository = AgentTraceRepository()
    repository.create_run(persisted, request, {"model": request.model})

    restored = repository.get_run(persisted.run_id)

    assert restored is not None
    assert restored.input == long_input
    assert restored.output == long_output


async def _collect_events(iterator):
    return [event async for event in iterator]


def test_agent_sse_uses_last_event_id_and_emits_event_ids(monkeypatch) -> None:
    async def scenario() -> None:
        test_container = build_container()
        monkeypatch.setattr("app.routes.container", test_container)
        created = await test_container.agent.create_run(
            AgentRunCreateRequest(
                input="resume sse",
                provider_id="mock",
                model="mock-1",
            )
        )
        await test_container.agent.wait(created.run_id)

        response = await agent_events(
            created.run_id, after_sequence=None, last_event_id="1"
        )
        chunks = [chunk async for chunk in response.body_iterator]
        body = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in chunks
        )

        assert "id: 0\n" not in body
        assert "id: 1\n" not in body
        assert "id: 2\n" in body
        assert "event: RunCompleted" in body

        with pytest.raises(ApiError) as error:
            await agent_events(
                created.run_id, after_sequence=None, last_event_id="invalid"
            )
        assert error.value.code == "TRACE_CURSOR_INVALID"

    run(scenario())


def test_step_limit_stops_repeated_agent_loop() -> None:
    async def scenario() -> None:
        container = build_container()
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool system.echo {"text":"one step"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
                max_steps=1,
            )
        )

        completed = await container.agent.wait(created.run_id)
        assert completed.status == AgentRunStatus.failed
        assert completed.error_code == "MAX_STEPS_EXCEEDED"
        assert len(completed.tool_results) == 1

    run(scenario())


def test_token_budget_stops_agent_run() -> None:
    async def scenario() -> None:
        container = build_container()
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input="hello budget",
                provider_id="mock",
                model="mock-1",
                token_budget=1,
            )
        )

        completed = await container.agent.wait(created.run_id)
        assert completed.status == AgentRunStatus.failed
        assert completed.error_code == "TOKEN_BUDGET_EXCEEDED"

    run(scenario())


def test_cancelling_permission_wait_cancels_run() -> None:
    async def scenario() -> None:
        container = build_container()
        protected_tool = container.tools.get("system.echo")
        protected_tool.definition.permission = "notes.write"
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool system.echo {"text":"cancel"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
                tool_timeout_seconds=10,
            )
        )

        async with asyncio.timeout(2):
            async for event in container.agent.events(created.run_id):
                if event.event == AgentEventType.permission_required:
                    break

        cancelled = await container.agent.cancel(created.run_id)
        await container.agent.wait(created.run_id)
        assert cancelled.status == AgentRunStatus.cancelled
        assert container.agent.get_run(created.run_id).cancelled is True

    run(scenario())
