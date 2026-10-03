"""Real SQLite execution: linear writes, committed events and bounded workers."""
import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event, get_ident
from time import perf_counter
from uuid import uuid4

import pytest

from app.benchmarks import agent, datasets, service, storage
from app.contracts import (
    AgentBenchmarkRequest, BenchmarkEvent, BenchmarkReport, BenchmarkRun,
    BenchmarkStatus, RAGCaseResult, RAGDatasetCase, RAGRunRequest, SearchResponse,
)
from app.database.db import connect_knowledge


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for name in ('_runs', '_events', '_reports', '_tasks', '_subscribers', '_cancel_flags'):
        monkeypatch.setattr(service, name, {})


def run_record(identifier='incremental', status='queued'):
    return BenchmarkRun(run_id=identifier, kind='rag', dataset_id='fixture', dataset_hash='hash',
        status=status, progress=0, created_at=service._now(),
        config_snapshot={'vault_scope': datasets.current_scope(), 'dataset_cases': [{'query': 'frozen input'}]})


def event_for(run, sequence=0, kind='CaseCompleted'):
    return BenchmarkEvent(run_id=run.run_id, event=kind, sequence=sequence, timestamp=service._now(),
        data=RAGCaseResult(case_id=f'case-{sequence}', mode='fts', repeat=0, latency_ms=1).model_dump(mode='json'))


def test_inputs_written_once_and_new_reports_hydrate_after_reopen():
    run = run_record()
    storage.save(run)
    with closing(connect_knowledge()) as conn:
        original = conn.execute('SELECT run_json FROM benchmark_runs').fetchone()[0]
    running = run.model_copy(update={'status': BenchmarkStatus.running, 'progress': .5})
    running.config_snapshot = {**running.config_snapshot, 'active_agent_run_id': 'live-trace'}
    storage.save(running, event=event_for(run))
    terminal = running.model_copy(update={'status': BenchmarkStatus.completed, 'progress': 1.0})
    report = BenchmarkReport(run_id=run.run_id, kind='rag', dataset_id=run.dataset_id, dataset_hash=run.dataset_hash,
        status='completed', config_snapshot=terminal.config_snapshot, cases=[event_for(run).data])
    storage.save(terminal, report, event_for(run, 1, 'RunCompleted'))
    with closing(connect_knowledge()) as conn:
        row = conn.execute('SELECT run_json,report_json,summary_json FROM benchmark_runs').fetchone()
    assert row[0] == original
    assert 'dataset_cases' not in json.loads(row[1])['config_snapshot']
    assert 'dataset_cases' not in json.loads(row[2])['config_snapshot']
    assert storage.get(run.run_id) == (terminal, report)


def test_duplicate_events_do_not_regress_progress_and_conflicts_roll_back():
    run = run_record()
    storage.save(run)
    first = event_for(run)
    storage.save(run.model_copy(update={'status': BenchmarkStatus.running, 'progress': .5}), event=first)
    storage.save(run, event=first)
    assert storage.get_summary(run.run_id).progress == .5
    with pytest.raises(ValueError, match='Conflicting'):
        storage.save(run, event=first.model_copy(update={'data': {'changed': True}}))
    with pytest.raises(ValueError, match='Out-of-order'):
        storage.save(run, event=event_for(run, 3))
    with closing(connect_knowledge()) as conn:
        conn.execute("CREATE TRIGGER reject_case BEFORE INSERT ON benchmark_events BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='failure'):
        storage.save(run.model_copy(update={'progress': 1.0}), event=event_for(run, 1))
    assert storage.get_summary(run.run_id).progress == .5
    assert storage.events(run.run_id) == [first]


def test_recovery_uses_latest_progress_and_supports_existing_beta1_rows(monkeypatch):
    from app.database import migrations
    run = run_record(status='running')
    # A real beta1-format row contains its latest full snapshot and full report.
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:24])
        with closing(connect_knowledge()) as conn:
            conn.execute('INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?,?,?)',
                (run.run_id, datasets.current_scope(), 'rag', 'running', run.created_at.isoformat(),
                 run.model_dump_json(), None, storage.summary(run).model_dump_json()))
    storage.save(run.model_copy(update={'progress': .75}), event=event_for(run))
    storage.recover_interrupted(run.run_id, service._now())
    saved, report = storage.get(run.run_id)
    assert saved.progress == .75 and saved.status.value == 'failed'
    assert report.config_snapshot == run.config_snapshot
    assert [case.case_id for case in report.cases] == ['case-0']
    assert [event.sequence for event in storage.events(run.run_id)] == [0, 1]


def test_v24_completed_report_migrates_full_configuration_and_evidence(monkeypatch):
    from app.database import migrations
    run = run_record(status='completed')
    run.config_snapshot['model'] = 'original-model'
    event = event_for(run, kind='RunCompleted')
    report = BenchmarkReport(run_id=run.run_id, kind='rag', dataset_id=run.dataset_id, dataset_hash=run.dataset_hash,
        status='completed', config_snapshot=run.config_snapshot, cases=[event.data], metrics={'fts': {'recall_at_k': .75}})
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:24])
        with closing(connect_knowledge()) as conn:
            conn.execute('INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?,?,?)',
                (run.run_id, datasets.current_scope(), 'rag', 'completed', run.created_at.isoformat(),
                 run.model_dump_json(), report.model_dump_json(), storage.summary(run).model_dump_json()))
            conn.execute('INSERT INTO benchmark_events VALUES(?,?,?)', (run.run_id, 0, event.model_dump_json()))
    assert storage.get(run.run_id) == (run, report)
    assert storage.events(run.run_id) == [event]
    with closing(connect_knowledge()) as conn:
        mutable = conn.execute('SELECT run_json,report_json,summary_json FROM benchmark_runs').fetchone()
        assert all('dataset_cases' not in value for value in mutable)
        assert json.loads(conn.execute('SELECT config_json FROM benchmark_inputs').fetchone()[0]) == run.config_snapshot


def test_failed_writer_closes_actual_sse_stream_without_inventing_terminal_event():
    from app import routes
    async def exercise():
        run = run_record(status='running')
        service._runs[run.run_id] = run
        service._events[run.run_id] = [event_for(run, kind='RunStarted')]
        response = await routes.benchmark_events(run.run_id, after_sequence=-1, last_event_id=None)
        iterator = response.body_iterator
        assert 'RunStarted' in await anext(iterator)
        service._forget(run.run_id)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(iterator), 1)
    asyncio.run(exercise())


async def prepare_rag(monkeypatch, count=3):
    from app.services import note_service
    note = await note_service.create_note(title=f'synthetic-{count}', folder='', markdown='fixture content', tags=[])
    identifier = f'incremental-{count}'
    payload = {'dataset_id': identifier, 'kind': 'rag', 'version': '1',
        'cases': [{'case_id': f'case-{index}', 'query': 'x'*1024, 'expected_note_ids': [note.note_id]}
            for index in range(count)]}
    if count <= 100:
        datasets.import_dataset(json.dumps(payload))
    else:
        # Scale fixtures deliberately bypass the import cap (100 cases), while
        # exercising the same typed dataset, real runner and SQLite path.
        from hashlib import sha256
        dataset = datasets.RAGDataset(dataset_id=identifier, kind=service.BenchmarkKind.rag, version='1', description='',
            content_hash=sha256(json.dumps(payload).encode()).hexdigest(), scope='vault',
            cases=[RAGDatasetCase.model_validate(case) for case in payload['cases']])
        monkeypatch.setattr(datasets, 'load_dataset', lambda *_: dataset)
    async def search(request):
        return SearchResponse(query=request.query, mode=request.mode)
    monkeypatch.setattr(service.engine, 'search', search)
    return RAGRunRequest(dataset_id=identifier, modes=['fts'])


@pytest.mark.parametrize('cancel_task', [False, True])
def test_slow_write_yields_loop_has_backpressure_and_publishes_only_after_commit(monkeypatch, cancel_task):
    started, release = Event(), Event()
    original = storage.save
    owner = get_ident()
    active = peak = 0
    def slow_save(run, report=None, event=None):
        nonlocal active, peak
        assert get_ident() != owner
        active += 1
        peak = max(peak, active)
        try:
            if event and event.event.value == 'CaseCompleted':
                started.set()
                assert release.wait(5)
            return original(run, report, event)
        finally:
            active -= 1
    monkeypatch.setattr(storage, 'save', slow_save)
    async def exercise():
        request = await prepare_rag(monkeypatch)
        created = await service.create_rag_run(request)
        queue = service.subscribe(created.run_id)
        try:
            assert await asyncio.to_thread(started.wait, 3)
            assert service.get_run(created.run_id).progress == 0
            assert [event.event.value for event in service.get_events(created.run_id)] == ['RunStarted']
            assert queue.qsize() == 1
            if cancel_task:
                service._tasks[created.run_id].cancel()
            else:
                service.cancel_run(created.run_id)
            # The task has to drain its in-flight transaction before finishing.
            await asyncio.sleep(.01)
            assert not service._tasks[created.run_id].done()
        finally:
            release.set()
        finished = await service.wait_for_run(created.run_id)
        assert finished.status.value == 'cancelled'
        evidence = storage.events(created.run_id)
        assert [event.event.value for event in evidence] == ['RunStarted', 'CaseCompleted', 'RunCancelled']
        assert [event.sequence for event in evidence] == list(range(3))
        assert len(storage.get(created.run_id)[1].cases) == 1
        assert peak == 1
    asyncio.run(exercise())


@pytest.mark.parametrize('persistent_failure', [False, True])
def test_failed_write_never_publishes_case_and_can_recover_after_writer_failure(monkeypatch, persistent_failure):
    original = storage.save
    def failing_save(run, report=None, event=None):
        if event and (event.event.value == 'CaseCompleted' or persistent_failure and report):
            raise sqlite3.OperationalError('injected write failure')
        return original(run, report, event)
    monkeypatch.setattr(storage, 'save', failing_save)
    async def exercise():
        created = await service.create_rag_run(await prepare_rag(monkeypatch))
        queue = service.subscribe(created.run_id)
        await service.wait_for_run(created.run_id)
        delivered = []
        while not queue.empty():
            delivered.append(queue.get_nowait())
        assert delivered[-1] is None
        assert all(event is None or event.event.value != 'CaseCompleted' for event in delivered)
        if persistent_failure:
            assert created.run_id not in service._runs
            assert storage.get_summary(created.run_id).status.value == 'running'
            monkeypatch.setattr(storage, 'save', original)
        recovered = service.get_run(created.run_id)
        assert recovered.status.value == 'failed'
        assert service.get_report(created.run_id).cases == []
        assert [event.sequence for event in storage.events(created.run_id)] == [0, 1]
    asyncio.run(exercise())


def test_worker_keeps_original_vault_when_request_scope_changes(monkeypatch):
    from app import host_bridge
    from app.config import get_settings
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    first, second = str(uuid4()), str(uuid4())
    async def exercise():
        token = host_bridge.vault_id.set(first)
        try:
            run = run_record()
            service._runs[run.run_id] = run
            service._events[run.run_id] = []
            proceed = asyncio.Event()
            async def owner():
                await proceed.wait()
                await service._persist(run.run_id)
                await service._emit(run.run_id, 'RunStarted', {},
                    run=run.model_copy(update={'status': BenchmarkStatus.running}))
            task = asyncio.create_task(owner())
            host_bridge.vault_id.set(second)
            proceed.set()
            await task
            assert storage.get(run.run_id) == (None, None)
            assert service.get_run(run.run_id) is None
            host_bridge.vault_id.set(first)
            assert storage.get_summary(run.run_id).status.value == 'running'
            assert len(storage.events(run.run_id)) == 1
        finally:
            host_bridge.vault_id.reset(token)
    asyncio.run(exercise())


def test_real_agent_and_collaboration_reports_survive_eviction(monkeypatch):
    from app import container as container_module
    from app.container import build_container
    content = json.loads((Path(__file__).parent/'fixtures/agent-collaboration.json').read_text(encoding='utf-8'))
    content['cases'].insert(0, {'case_id': 'single', 'prompt': '/tool system.echo {"text":"single"}',
        'allowed_tools': ['system.echo'], 'expected_tools': [{'name': 'system.echo', 'arguments': {'text': 'single'}}]})
    datasets.import_dataset(json.dumps(content))
    async def exercise():
        container = build_container()
        monkeypatch.setattr(container_module, 'container', container)
        try:
            request = AgentBenchmarkRequest(dataset_id=content['dataset_id'], provider_id='mock', model='mock-1', offline=True)
            created = await agent.create_run(request)
            assert (await service.wait_for_run(created.run_id)).status.value == 'completed'
            report = service.get_report(created.run_id)
            assert len(report.cases) == 3 and all(case.success for case in report.cases)
            assert report.cases[0].agent_run_id and all(case.collaboration_id for case in report.cases[1:])
            assert [event.sequence for event in storage.events(created.run_id)] == list(range(5))
            service._forget(created.run_id)
            assert service.get_report(created.run_id) == report
        finally:
            await container.agent.shutdown()
    asyncio.run(exercise())


def test_linear_sql_parameter_volume_through_real_rag_runner(monkeypatch):
    original_connect = storage.connect_knowledge
    measurements = []
    class MeteredConnection:
        def __init__(self, meter):
            self.conn = original_connect()
            self.meter = meter
        def close(self):
            self.conn.close()
        def execute(self, statement, params=()):
            sql = ' '.join(statement.split()).upper()
            if sql.startswith(('INSERT INTO BENCHMARK_', 'UPDATE BENCHMARK_')):
                self.meter['sql_writes'] += 1
                self.meter['sql_parameter_bytes'] += sum(len(value.encode('utf-8')) for value in params if isinstance(value, str))
                if sql.startswith('INSERT INTO BENCHMARK_INPUTS'):
                    self.meter['snapshot_bytes'] += len(params[1].encode('utf-8'))
                    self.meter['serialized_input_cases'] += len(json.loads(params[1])['dataset_cases'])
                elif sql.startswith('INSERT INTO BENCHMARK_RUNS'):
                    assert 'dataset_cases' not in params[5]
                elif sql.startswith('UPDATE BENCHMARK_RUNS'):
                    assert 'RUN_JSON=' not in sql
            return self.conn.execute(statement, params)
    async def exercise():
        for count in (100, 200, 500):
            request = await prepare_rag(monkeypatch, count)
            meter = dict(cases=count, sql_writes=0, sql_parameter_bytes=0, snapshot_bytes=0, serialized_input_cases=0)
            monkeypatch.setattr(storage, 'connect_knowledge', lambda: MeteredConnection(meter))
            start = perf_counter()
            created = await service.create_rag_run(request)
            finished = await service.wait_for_run(created.run_id)
            meter['elapsed_ms'] = round((perf_counter()-start)*1000, 3)
            assert finished.status.value == 'completed'
            assert len(storage.get(created.run_id)[1].cases) == count
            assert meter['serialized_input_cases'] == count
            measurements.append(meter)
        assert measurements[1]['sql_parameter_bytes'] < measurements[0]['sql_parameter_bytes']*2.1
        assert measurements[2]['sql_parameter_bytes'] < measurements[0]['sql_parameter_bytes']*5.2
    asyncio.run(exercise())
    print(json.dumps({'unit': 'UTF-8 bytes in cumulative SQL string parameters, not physical disk bytes',
        'measurements': measurements}))
