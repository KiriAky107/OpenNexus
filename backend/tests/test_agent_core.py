import asyncio

from app.agent.permissions import PermissionMode
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import (
    AgentEventType,
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
        assert container.agent.resolve_permission(
            created.run_id, request_id, "allow_once"
        )
        completed = await container.agent.wait(created.run_id)
        assert completed.status == AgentRunStatus.completed
        assert completed.tool_results[0].success is True

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
            while container.agent.get_run(created.run_id).status != AgentRunStatus.waiting_permission:
                await asyncio.sleep(0)

        cancelled = await container.agent.cancel(created.run_id)
        await container.agent.wait(created.run_id)
        assert cancelled.status == AgentRunStatus.cancelled
        assert container.agent.get_run(created.run_id).cancelled is True

    run(scenario())
