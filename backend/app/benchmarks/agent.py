"""通过真实 AgentRuntime 执行标准任务评测，不使用脚本化替代运行器。"""
import asyncio
from time import perf_counter
from uuid import uuid4
from app.contracts import (AgentBenchmarkRequest, AgentCaseResult, AgentRunCreateRequest,
    BenchmarkRun, BenchmarkReport, BenchmarkKind, BenchmarkStatus, BenchmarkEvent, BenchmarkEventType)
from app.benchmarks import datasets, service
from app.errors import ApiError

INVALID = {'TOOL_NOT_FOUND', 'TOOL_NOT_ALLOWED', 'TOOL_ARGUMENT_INVALID', 'TOOL_VALIDATION_ERROR'}

def score(case, run, events, latency, repeat):
    """按工具选择、参数、结果、输出和引用要求评定单个样本。"""
    calls = [e.data for e in events if e.event.value == 'ToolCall']
    # 使用最大二分匹配，避免宽松的参数子集占用唯一能满足更严格预期的调用；
    # 每个实际调用最多匹配一个预期调用。
    matched = {}
    def assign(expected_index, visited):
        expected = case.expected_tools[expected_index]
        for call_index, call in enumerate(calls):
            if call_index in visited or call.get('name') != expected.name:
                continue
            arguments = call.get('arguments', {})
            if not all(key in arguments and arguments[key] == value for key, value in expected.arguments.items()):
                continue
            visited.add(call_index)
            if call_index not in matched or assign(matched[call_index], visited):
                matched[call_index] = expected_index
                return True
        return False
    accurate = sum(assign(index, set()) for index in range(len(case.expected_tools)))
    from collections import Counter
    actual_names = Counter(call.get('name') for call in calls)
    expected_names = Counter(tool.name for tool in case.expected_tools)
    selected = sum(min(count, actual_names[name]) for name, count in expected_names.items())
    results = run.tool_results
    checks = {
        'completed': run.status.value == 'completed',
        'tools_selected': selected == len(case.expected_tools),
        'tool_arguments': accurate == len(case.expected_tools),
        'no_extra_calls': len(calls) <= len(case.expected_tools),
        'tool_results': all(r.success for r in results),
        'output': all(text.casefold() in (run.output or '').casefold() for text in case.output_contains),
        'citation': not case.citation_required or bool(run.citations),
        'tasks_created': case.tasks_created is None or sum(r.success and r.name == 'tasks.create' for r in results) == case.tasks_created,
    }
    return AgentCaseResult(case_id=case.case_id, repeat=repeat, agent_run_id=run.run_id,
        success=all(checks.values()), tool_calls=len(calls), expected_calls=len(case.expected_tools),
        selected_calls=selected, accurate_calls=accurate, invalid_calls=sum(r.error_code in INVALID for r in results),
        steps=run.current_step, latency_ms=latency, token_usage=run.token_usage, checks=checks, error_code=run.error_code)

def aggregate(cases, planned_total=None):
    """汇总已执行样本，并让取消后的未执行样本继续计入计划总数。"""
    total = len(cases) if planned_total is None else planned_total
    calls = sum(c.tool_calls for c in cases)
    expected = sum(c.expected_calls for c in cases)
    # 微平均同时惩罚遗漏和多余调用；完全没有调用要求时准确率记为不适用。
    denominator = max(calls, expected)
    return {'total_cases': total, 'evaluated_cases': len(cases), 'task_success_rate': sum(c.success for c in cases)/total if total else 0,
        'tool_selection_accuracy': sum(c.selected_calls for c in cases)/denominator if denominator else None,
        'tool_argument_accuracy': sum(c.accurate_calls for c in cases)/denominator if denominator else None,
        'invalid_tool_call_rate': sum(c.invalid_calls for c in cases)/calls if calls else None,
        'average_steps': sum(c.steps for c in cases)/total if total else 0,
        'average_latency_ms': sum(c.latency_ms for c in cases)/total if total else 0,
        'token_usage': sum(c.token_usage for c in cases), 'tool_calls': calls, 'expected_calls': expected}

async def create_run(request: AgentBenchmarkRequest):
    """冻结数据集与运行配置，并把评测交给后台真实 Agent Runtime。"""
    from app.container import container
    from app.providers.registry import ProviderNotFoundError
    try:
        provider = container.providers.get(request.provider_id)
    except ProviderNotFoundError as exc:
        raise ApiError(404, 'PROVIDER_NOT_FOUND', 'Provider not found or disabled.') from exc
    is_mock = provider.config.provider_type.value == 'mock'
    if request.offline and not is_mock:
        raise ApiError(422, 'BENCHMARK_OFFLINE_PROVIDER_REQUIRED', 'Offline regression only accepts a mock provider.')
    if is_mock and not request.offline:
        raise ApiError(422, 'BENCHMARK_REAL_PROVIDER_REQUIRED', 'Select a real provider or explicitly mark offline regression.')
    dataset = datasets.load_dataset(request.dataset_id, BenchmarkKind.agent)
    if not service._evict_terminal():
        raise ApiError(429, 'BENCHMARK_CAPACITY_EXCEEDED', 'Benchmark capacity exceeded.')
    run_id = 'benchmark_' + uuid4().hex[:12]
    snapshot = {**request.model_dump(), 'dataset_hash': dataset.content_hash,
        'dataset_version': dataset.version, 'execution': 'offline' if request.offline else 'real_agent_runtime',
        'provider_type': provider.config.provider_type, 'scoring_version': '1.0', 'permission_policy': 'runtime_user_decision'}
    run = BenchmarkRun(run_id=run_id, kind=BenchmarkKind.agent, dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash, status=BenchmarkStatus.queued, created_at=service._now(), config_snapshot=snapshot)
    service._runs[run_id] = run
    service._events[run_id] = []
    service._subscribers[run_id] = []
    service._cancel_flags[run_id] = asyncio.Event()
    service._tasks[run_id] = asyncio.create_task(execute(run_id, request, dataset, container.agent))
    return run

async def execute(run_id, request, dataset, runtime):
    """顺序执行样本，传播取消信号，并持续发布可订阅的运行事件。"""
    flag = service._cancel_flags[run_id]
    results = []; active = None
    def emit(kind, data):
        event = BenchmarkEvent(event=kind, run_id=run_id, sequence=len(service._events[run_id]), data=data, timestamp=service._now())
        service._events[run_id].append(event)
        for queue in service._subscribers.get(run_id, []): queue.put_nowait(event)
    status = BenchmarkStatus.completed
    error = None
    try:
        service._runs[run_id] = service._runs[run_id].model_copy(update={'status': BenchmarkStatus.running, 'started_at': service._now()})
        emit(BenchmarkEventType.run_started, {'dataset_id': dataset.dataset_id})
        for case in dataset.cases:
            for repeat in range(request.repeat):
                if flag.is_set():
                    status = BenchmarkStatus.cancelled; break
                started = perf_counter()
                active = await runtime.create_run(AgentRunCreateRequest(input=case.prompt, provider_id=request.provider_id,
                    model=request.model, allowed_tools=case.allowed_tools, max_steps=request.max_steps,
                    token_budget=request.token_budget, run_timeout_seconds=request.timeout_seconds,
                    tool_timeout_seconds=min(30, request.timeout_seconds), allow_network=request.allow_network,
                    metadata={'benchmark_run_id': run_id, 'case_id': case.case_id}))
                # 样本仍在运行时就暴露真实 Trace 与权限入口，便于界面处理待决授权。
                service._runs[run_id].config_snapshot['active_agent_run_id'] = active.run_id
                wait = asyncio.create_task(runtime.wait(active.run_id))
                cancel = asyncio.create_task(flag.wait())
                try:
                    done, _ = await asyncio.wait([wait, cancel], return_when=asyncio.FIRST_COMPLETED)
                    if cancel in done:
                        await runtime.cancel(active.run_id)
                        status = BenchmarkStatus.cancelled
                    finished = await wait
                finally:
                    cancel.cancel(); await asyncio.gather(cancel, return_exceptions=True)
                events = [event async for event in runtime.events(active.run_id)]
                result = score(case, finished, events, (perf_counter()-started)*1000, repeat)
                results.append(result); active = None
                service._runs[run_id].progress = len(results)/(len(dataset.cases)*request.repeat)
                emit(BenchmarkEventType.case_completed, result.model_dump(mode='json'))
            if status == BenchmarkStatus.cancelled: break
    except asyncio.CancelledError:
        status = BenchmarkStatus.cancelled
    except Exception:
        status = BenchmarkStatus.failed; error = 'BENCHMARK_RUN_FAILED'
    finally:
        if active:
            await runtime.cancel(active.run_id)
            await runtime.wait(active.run_id)
        metrics = aggregate(results, len(dataset.cases)*request.repeat)
        run = service._runs[run_id]
        service._runs[run_id] = run.model_copy(update={'status':status, 'metrics':metrics, 'completed_at':service._now(), 'error_code':error})
        service._reports[run_id] = BenchmarkReport(run_id=run_id, kind=BenchmarkKind.agent,
            dataset_id=dataset.dataset_id, dataset_hash=dataset.content_hash, status=status,
            config_snapshot=run.config_snapshot, cases=results, metrics=metrics, error_code=error)
        emit({BenchmarkStatus.completed: BenchmarkEventType.run_completed, BenchmarkStatus.failed: BenchmarkEventType.run_failed,
            BenchmarkStatus.cancelled: BenchmarkEventType.run_cancelled}[status], {'metrics':metrics, 'error_code':error})
        service._cancel_flags.pop(run_id, None); service._subscribers.pop(run_id, None)
