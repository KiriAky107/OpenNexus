"""离线Agent/运行时和任务API负载测试；所有状态都位于临时目录中。"""
from __future__ import annotations

import argparse
import asyncio
import json
import inspect
import math
import os
import pathlib
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def stats(values):
    values = sorted(values)
    return {"count": len(values), "median_ms": round(values[len(values)//2], 2),
            "p95_ms": round(values[min(len(values)-1, math.ceil(len(values)*.95)-1)], 2),
            "max_ms": round(values[-1], 2)} if values else {"count": 0}


async def main(output):
    # 在导入任何应用程序模块之前设置：容器具有导入时初始化。
    with tempfile.TemporaryDirectory(prefix="notes-agent-task-stress-") as directory:
        root = pathlib.Path(directory)
        os.environ.update(APP_DATA_DIR=str(root / 'data'), APP_DB_PATH=str(root / 'app.db'),
                          APP_VAULT_PATH=str(root / 'vault'))
        from app.container import container
        from app.contracts import AgentRunCreateRequest, AgentRunStatus
        from app.agent.runtime import AgentRuntime, AgentCapacityError
        from app.agent.permissions import PermissionMode
        from app.main import app
        import httpx

        report = {"python": platform.python_version(), "platform": platform.platform(),
                  "provider": "mock with 50 ms injected delay per model turn; no network", "results": []}

        def save(name, value):
            report['results'].append({"scenario": name, **value})
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report['results'][-1], ensure_ascii=False), flush=True)

        async def measured(operation):
            delays = []
            async def heartbeat():
                while True:
                    start = time.perf_counter()
                    await asyncio.sleep(.01)
                    delays.append(max(0, (time.perf_counter()-start)*1000-10))
            pulse = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            start = time.perf_counter()
            try:
                result = await operation()
                await asyncio.sleep(.02)
                return {**result, "elapsed_ms": round((time.perf_counter()-start)*1000, 2),
                        "event_loop_lag": stats(delays)}
            finally:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)

        adapter = container.providers.get('mock').adapter
        original = adapter.complete
        async def delayed(request):
            await asyncio.sleep(.05)
            return await original(request)
        adapter.complete = delayed
        runtime = container.agent

        for concurrency in (1, 10, 50, 200):
            async def batch():
                durations, sequences, statuses = [], [], []
                semaphore = asyncio.Semaphore(concurrency)
                async def one(index):
                    async with semaphore:
                        start = time.perf_counter()
                        created = await runtime.create_run(AgentRunCreateRequest(
                            input=f'/tool system.echo {{"text":"pressure-{index}"}}',
                            provider_id='mock', model='mock-1', allowed_tools=['system.echo']))
                        events = [event async for event in runtime.events(created.run_id)]
                        done = await runtime.wait(created.run_id)
                        durations.append((time.perf_counter()-start)*1000)
                        statuses.append(done.status.value)
                        sequence = [event.sequence for event in events]
                        sequences.append(sequence == list(range(len(sequence))))
                        assert done.status == AgentRunStatus.completed
                        assert done.tool_results[0].output == {"text": f"pressure-{index}"}
                        replay = [event.sequence async for event in runtime.events(created.run_id, after_sequence=2)]
                        assert replay == sequence[3:]
                        return created.run_id
                ids = await asyncio.gather(*(one(i) for i in range(max(20, concurrency))))
                assert all(sequences)
                recovered = AgentRuntime(container.providers, container.tools, container.permissions,
                                         trace_repository=runtime.trace_repository)
                recovery_started = time.perf_counter()
                assert await asyncio.to_thread(lambda: all(recovered.get_run(run_id).status == AgentRunStatus.completed for run_id in ids))
                recovery_read_ms = (time.perf_counter() - recovery_started) * 1000
                assert not any(record.subscribers for record in runtime._records.values())
                return {"concurrency": concurrency, "runs": len(ids), "latency": stats(durations),
                        "completed": statuses.count('completed'), "ordered_events_and_replay": True,
                        "terminal_recovery": True, "recovery_read_ms": round(recovery_read_ms,2), "retained_records": len(runtime._records)}
            save('agent_tool_runs', await measured(batch))

        # 保留模型调用，以便在测试准入时所有 200 条记录保持活动状态。
        gate = asyncio.Event()
        async def blocked(request):
            await gate.wait()
            return await original(request)
        adapter.complete = blocked
        async def capacity():
            ids = [(await runtime.create_run(AgentRunCreateRequest(input='capacity', provider_id='mock', model='mock-1'))).run_id for _ in range(200)]
            rejected = False
            try:
                await runtime.create_run(AgentRunCreateRequest(input='overflow', provider_id='mock', model='mock-1'))
            except AgentCapacityError:
                rejected = True
            await asyncio.sleep(0)
            latencies = []
            for run_id in ids:
                start = time.perf_counter()
                await runtime.cancel(run_id)
                latencies.append((time.perf_counter()-start)*1000)
            states = await asyncio.gather(*(runtime.wait(run_id) for run_id in ids))
            assert rejected and all(run.status == AgentRunStatus.cancelled for run in states)
            assert all(not record.task or record.task.done() for record in runtime._records.values())
            return {"active_limit": 200, "overflow_rejected": rejected, "cancelled": len(states), "cancel_latency": stats(latencies)}
        save('capacity_and_cancel', await measured(capacity))
        adapter.complete = delayed

        async def permissions():
            tool = container.tools.get('system.echo')
            previous = tool.definition.permission
            tool.definition.permission = 'stress.confirm'
            container.permissions.policy.set_rule('stress.confirm', PermissionMode.confirm)
            async def one(index):
                created = await runtime.create_run(AgentRunCreateRequest(input='/tool system.echo {"text":"permission"}',
                    provider_id='mock', model='mock-1', allowed_tools=['system.echo'], tool_timeout_seconds=30))
                stream = runtime.events(created.run_id)
                try:
                    async for event in stream:
                        if event.event.value == 'PermissionRequired':
                            if index % 2: await runtime.cancel(created.run_id)
                            else: assert await runtime.resolve_permission(created.run_id, str(event.data['request_id']), 'allow')
                            break
                finally:
                    await stream.aclose()
                return (await runtime.wait(created.run_id)).status.value
            try:
                states = await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(20))), 60)
                assert states.count('completed') == states.count('cancelled') == 10
                assert not any(record.subscribers for record in runtime._records.values())
                return {"runs": 20, "approved_completed": 10, "cancelled_waiting_permission": 10, "subscribers_released": True}
            finally:
                tool.definition.permission = previous
        save('permission_wait', await measured(permissions))

        async def failures():
            active = 0
            async def injected(request):
                text = request.messages[-1].content
                if text == 'inject-provider-error': raise RuntimeError('injected offline provider failure')
                if text == 'inject-model-timeout': await asyncio.sleep(60)
                return await delayed(request)
            tool = container.tools.get('system.echo')
            executor = tool.executor
            async def slow_tool(arguments, context):
                nonlocal active
                active += 1
                try:
                    if arguments.text == 'slow': await asyncio.sleep(60)
                    value = executor(arguments, context)
                    return await value if inspect.isawaitable(value) else value
                finally: active -= 1
            adapter.complete = injected
            tool.executor = slow_tool
            async def one(index):
                mode = index % 4
                text = ['normal', 'inject-provider-error', 'inject-model-timeout', '/tool system.echo {"text":"slow"}'][mode]
                created = await runtime.create_run(AgentRunCreateRequest(input=text, provider_id='mock', model='mock-1',
                    allowed_tools=['system.echo'], run_timeout_seconds=1 if mode == 2 else 30, tool_timeout_seconds=1))
                result = await runtime.wait(created.run_id)
                if mode == 0: assert result.status == AgentRunStatus.completed
                elif mode == 1: assert result.status == AgentRunStatus.failed and result.error_code == 'AGENT_FAILED'
                elif mode == 2: assert result.status == AgentRunStatus.failed and result.error_code == 'AGENT_TIMEOUT'
                else: assert result.tool_results[0].error_code == 'TOOL_TIMEOUT'
            try:
                await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(20))), 45)
                assert active == 0
                return {"runs": 20, "success": 5, "provider_errors": 5, "model_timeouts": 5,
                        "tool_timeouts": 5, "remaining_tool_executors": active}
            finally:
                adapter.complete = delayed
                tool.executor = executor
        save('failure_and_timeout_isolation', await measured(failures))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://stress.local') as client:
            for count in (100, 1000):
                async def tasks():
                    timings = {name: [] for name in ('create', 'update', 'list', 'delete')}
                    async def call(method, path, **kwargs):
                        start = time.perf_counter()
                        response = await client.request(method, path, **kwargs)
                        response.raise_for_status()
                        return response.json(), (time.perf_counter()-start)*1000
                    semaphore = asyncio.Semaphore(20)
                    async def create(index):
                        async with semaphore:
                            body, duration = await call('POST', '/api/tasks', json={'title': f'压测任务 {index}', 'description': '独立测试数据'})
                            timings['create'].append(duration)
                            return body['task_id']
                    ids = await asyncio.gather(*(create(i) for i in range(count)))
                    first, _ = await call('GET', '/api/tasks')
                    seen = []
                    for offset in range(0, count, 100):
                        body, duration = await call('GET', f'/api/tasks?limit=100&offset={offset}')
                        timings['list'].append(duration)
                        seen.extend(item['task_id'] for item in body['items'])
                    assert len(set(seen)) == count and set(seen) == set(ids)
                    async def change(run_id):
                        async with semaphore:
                            body, duration = await call('PATCH', f'/api/tasks/{run_id}', json={'status': 'done'})
                            assert body['status'] == 'done'
                            timings['update'].append(duration)
                            _, duration = await call('DELETE', f'/api/tasks/{run_id}')
                            timings['delete'].append(duration)
                    await asyncio.gather(*(change(run_id) for run_id in ids))
                    final, _ = await call('GET', '/api/tasks')
                    assert final['page']['total'] == 0
                    return {"tasks": count, "client_concurrency": 20, "latencies": {key: stats(value) for key, value in timings.items()},
                            "default_page_count": len(first['items']), "default_total": first['page']['total'],
                            "pagination_complete": True, "final_total": 0}
                save('task_api_crud', await measured(tasks))

        report['database_bytes'] = (root / 'app.db').stat().st_size
        report['complete'] = True
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        container.plugins.shutdown()
        container.mcp_servers.shutdown()
        from app.operation_logs import shutdown_logging
        await asyncio.to_thread(shutdown_logging)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.output))
