import asyncio
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.container import build_container
from app.contracts import AgentDatasetCase, AgentBenchmarkRequest, BenchmarkRun
from app.benchmarks import agent, service, datasets


def test_collaboration_cases_score_actual_traces_and_snapshots():
    async def scenario():
        runtime = build_container().agent
        content = json.loads((Path(__file__).parent / 'fixtures/agent-collaboration.json').read_text(encoding='utf-8'))
        request = AgentBenchmarkRequest(dataset_id=content['dataset_id'], provider_id='mock', model='mock-1', offline=True)
        identifier = 'benchmark_collaboration_test'
        service._runs[identifier] = BenchmarkRun(run_id=identifier, kind='agent', dataset_id=content['dataset_id'], dataset_hash='test', status='running', created_at=service._now())
        try:
            for raw in content['cases']:
                case = AgentDatasetCase.model_validate(raw)
                result = await agent.execute_collaboration_case(identifier, request, case, runtime, asyncio.Event(), 0)
                assert result.success, result
                assert len(result.member_run_ids) == 2
                assert result.collaboration_id
                assert result.tool_calls == result.expected_calls
                assert all(runtime.get_run(run_id).definition_snapshot for run_id in result.member_run_ids)
        finally:
            await runtime.shutdown()
            service._runs.pop(identifier, None)
    asyncio.run(scenario())


def test_member_benchmark_rejects_recursive_delegation_and_cycles():
    for member in [
        {'member_id': 'a', 'prompt': 'work', 'allowed_tools': ['agent.create'], 'output_contains': ['done']},
        {'member_id': 'a', 'prompt': 'work', 'depends_on': ['a'], 'output_contains': ['done']},
        {'member_id': 'a', 'prompt': 'work'},
    ]:
        with pytest.raises(ValidationError):
            AgentDatasetCase(case_id='unsafe', prompt='test', members=[member])
