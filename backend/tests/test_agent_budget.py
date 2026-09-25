import asyncio

import pytest
from pydantic import ValidationError

from app.container import build_container
from app.contracts import AgentRunCreateRequest, BudgetDecisionRequest
from app.providers.base import ProviderTurn, ProviderToolCall


async def paused_run(container, **kwargs):
    run = await container.agent.create_run(AgentRunCreateRequest(
        input='/tool system.echo {"text":"once"}', provider_id='mock', model='mock-1',
        allowed_tools=['system.echo'], token_budget=1, **kwargs,
    ))
    async with asyncio.timeout(3):
        async for event in container.agent.events(run.run_id):
            if event.event.value == 'BudgetRequired':
                return run.run_id, str(event.data['request_id'])
    raise AssertionError('No budget request')


def test_budget_pause_resume_is_bounded_and_idempotent():
    async def scenario():
        container = build_container()
        try:
            run_id, request_id = await paused_run(container)
            run = container.agent.get_run(run_id)
            assert run.status.value == 'waiting_budget'
            assert not run.tool_results
            assert not await container.agent.extend_budget(run_id, 'stale', 100)
            assert not await container.agent.extend_budget('other-run', request_id, 100)
            results = await asyncio.gather(*(
                container.agent.extend_budget(run_id, request_id, 100) for _ in range(2)
            ))
            assert results == [True, True]
            assert not await container.agent.extend_budget(run_id, request_id, 200)
            completed = await asyncio.wait_for(container.agent.wait(run_id), 3)
            assert completed.status.value == 'completed'
            assert completed.token_budget == run.token_usage + 100
            assert len(completed.tool_results) == 1
            events = [event async for event in container.agent.events(run_id)]
            assert sum(event.event.value == 'BudgetResolved' for event in events) == 1
            assert sum(event.event.value == 'ModelCallStarted' for event in events) == 2
        finally:
            await container.agent.shutdown()
    asyncio.run(scenario())


def test_budget_wait_does_not_consume_execution_timeout_and_can_cancel():
    async def scenario():
        container = build_container()
        try:
            run_id, request_id = await paused_run(container, run_timeout_seconds=1)
            await asyncio.sleep(1.1)
            assert container.agent.get_run(run_id).status.value == 'waiting_budget'
            cancelled = await container.agent.cancel(run_id)
            assert cancelled.status.value == 'cancelled'
            assert not cancelled.tool_results
            assert not await container.agent.extend_budget(run_id, request_id, 100)
        finally:
            await container.agent.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('amount', [0, -1, 1000001, 1.5, True, '100'])
def test_invalid_additional_budget(amount):
    with pytest.raises(ValidationError):
        BudgetDecisionRequest(additional_tokens=amount)


def test_repeated_budget_pauses_preserve_previous_tool_results(monkeypatch):
    async def scenario():
        container = build_container()
        turns = iter([
            ProviderTurn(tool_calls=[ProviderToolCall(tool_call_id=f'call_{index}', name='system.echo', arguments={'text': str(index)})], input_tokens=10)
            for index in range(3)
        ] + [ProviderTurn(text='done', output_tokens=10)])
        calls = []

        async def complete(request):
            calls.append(request)
            return next(turns)

        monkeypatch.setattr(container.providers.get('mock').adapter, 'complete', complete)
        try:
            run = await container.agent.create_run(AgentRunCreateRequest(
                input='repeat', provider_id='mock', model='mock-1', allowed_tools=['system.echo'], token_budget=20,
            ))
            pauses = 0
            async with asyncio.timeout(3):
                async for event in container.agent.events(run.run_id):
                    if event.event.value == 'BudgetRequired':
                        pauses += 1
                        current = container.agent.get_run(run.run_id)
                        assert len(current.tool_results) == pauses
                        assert await container.agent.extend_budget(run.run_id, str(event.data['request_id']), 10)
            completed = container.agent.get_run(run.run_id)
            assert pauses == 2  # >= budget, not only strictly over it
            assert completed.status.value == 'completed'
            assert [result.tool_call_id for result in completed.tool_results] == ['call_0', 'call_1', 'call_2']
            assert len(calls) == 4
        finally:
            await container.agent.shutdown()
    asyncio.run(scenario())


def test_restart_restores_pending_turn_without_repeating_model_or_tools(monkeypatch):
    async def scenario():
        first = build_container()
        run_id, request_id = await paused_run(first)
        await first.agent.shutdown()
        second = build_container()
        original = second.providers.get('mock').adapter.complete
        calls = []
        async def complete(request):
            calls.append(request)
            assert request.messages[-1].role.value == 'tool'
            return await original(request)
        monkeypatch.setattr(second.providers.get('mock').adapter, 'complete', complete)
        try:
            assert second.agent.get_run(run_id).status.value == 'waiting_budget'
            assert second.agent.list_runs(20, 0)[0][0].status.value == 'waiting_budget'
            assert await second.agent.extend_budget(run_id, request_id, 100)
            assert await second.agent.extend_budget(run_id, request_id, 100)
            result = await asyncio.wait_for(second.agent.wait(run_id), 3)
            assert result.status.value == 'completed'
            assert len(calls) == len(result.tool_results) == 1
            assert second.agent.trace_repository.get_checkpoint(run_id) is None
        finally:
            await second.agent.shutdown()
        third = build_container()
        try:
            assert await third.agent.extend_budget(run_id, request_id, 100)
            assert not await third.agent.extend_budget(run_id, request_id, 101)
            assert third.agent.get_run(run_id).status.value == 'completed'
        finally:
            await third.agent.shutdown()
    asyncio.run(scenario())


def test_cancel_persisted_pause_does_not_resume_it():
    async def scenario():
        first = build_container()
        run_id, request_id = await paused_run(first)
        await first.agent.shutdown()
        second = build_container()
        try:
            assert (await second.agent.cancel(run_id)).status.value == 'cancelled'
            assert not await second.agent.extend_budget(run_id, request_id, 100)
            assert second.agent.trace_repository.get_checkpoint(run_id) is None
        finally:
            await second.agent.shutdown()
    asyncio.run(scenario())
