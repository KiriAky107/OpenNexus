"""Synthetic report scale, bounded summaries and concurrent restart recovery."""
import json
import asyncio
from contextlib import closing
from contextvars import ContextVar
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import perf_counter

import pytest

from app.benchmarks import datasets, service, storage
from app.contracts import BenchmarkEvent, BenchmarkReport, BenchmarkRun, RAGCaseResult
from app.database.db import connect_knowledge


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for name in ('_runs', '_events', '_reports', '_tasks', '_subscribers', '_cancel_flags'):
        monkeypatch.setattr(service, name, {})


def record(identifier, **values):
    return BenchmarkRun(run_id=identifier, kind='rag', dataset_id='synthetic', dataset_hash='hash',
        status=values.pop('status', 'completed'), created_at=values.pop('created_at', service._now()),
        config_snapshot={'vault_scope': datasets.current_scope(), 'dataset_cases': [{'query': 'synthetic'}]}, **values)


def test_fifteen_reports_with_3000_legal_results_never_load_report_for_summaries(monkeypatch):
    cases = [RAGCaseResult(case_id=f'case-{i}', mode='fts', repeat=0, latency_ms=1.5,
        retrieved_note_ids=[f'note-{i}'], retrieved_block_ids=[f'block-{i}'], recall=1,
        hit_at_1=True, hit_at_5=True, reciprocal_rank=1) for i in range(3000)]
    for i in range(15):
        run = record(f'run-{i:02}')
        storage.save(run, BenchmarkReport(run_id=run.run_id, kind=run.kind, dataset_id=run.dataset_id,
            dataset_hash=run.dataset_hash, status=run.status, config_snapshot=run.config_snapshot, cases=cases))
    started = perf_counter()
    # Reproduce the old terminal-list path with exactly the same valid evidence.
    old = [storage.get(run.run_id)[0] for run in storage.list_runs()[0]]
    old_ms = (perf_counter()-started)*1000
    assert len(old) == 15
    monkeypatch.setattr(storage, 'get', lambda *_: pytest.fail('summary loaded a complete report'))
    started = perf_counter()
    listed, total = service.list_runs()
    new_ms = (perf_counter()-started)*1000
    assert total == len(listed) == 15
    assert all('dataset_cases' not in run.config_snapshot for run in listed)
    assert service._reports == {}  # No unbounded historical report cache.
    assert service.get_run(listed[0].run_id).run_id == listed[0].run_id
    with closing(connect_knowledge()) as conn:
        sizes = conn.execute('SELECT SUM(length(report_json)),SUM(length(summary_json)) FROM benchmark_runs').fetchone()
    print(json.dumps({'fixture': '15 reports x 3000 legal RAG results', 'old_full_report_reads': 15,
        'new_full_report_reads': 0, 'old_ms': round(old_ms,3), 'summary_ms': round(new_ms,3),
        'report_bytes': sizes[0], 'summary_bytes': sizes[1]}))


def test_summary_filter_order_and_legacy_entries_paginate_without_repeats():
    now = service._now()
    for i in range(7):
        run = record(str(i), status='failed' if i%2 else 'completed', created_at=now+timedelta(seconds=i))
        if i == 3:
            service._runs[run.run_id] = run
        else:
            storage.save(run)
    pages = [service.list_runs(limit=2, offset=offset)[0] for offset in range(0,7,2)]
    assert [run.run_id for page in pages for run in page] == ['6','5','4','3','2','1','0']
    from app.contracts import BenchmarkStatus, BenchmarkKind
    listed, total = service.list_runs(status=BenchmarkStatus.failed, limit=2, offset=1)
    assert total == 3 and [run.run_id for run in listed] == ['3','1']
    assert service.list_runs(kind=BenchmarkKind.agent) == ([],0)


def test_concurrent_restart_recovery_is_once_and_preserves_noncontiguous_evidence():
    run = record('restart', status='running')
    event = BenchmarkEvent(run_id=run.run_id, event='CaseCompleted', sequence=7, timestamp=service._now(),
        data=RAGCaseResult(case_id='one',mode='fts',repeat=0,latency_ms=2).model_dump(mode='json'))
    storage.save(run, event=event)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.list_runs(), range(4)))
    assert all(rows[0].status.value == 'failed' and total == 1 for rows,total in results)
    events = storage.events(run.run_id)
    assert [event.sequence for event in events] == [7,8]
    assert service.get_report(run.run_id).cases[0].case_id == 'one'
    assert service.get_report(run.run_id).config_snapshot['dataset_cases'] == [{'query':'synthetic'}]


def test_active_run_is_not_recovered_and_summary_keeps_trace_identity():
    run = record('live',status='running')
    run.config_snapshot['active_agent_run_id'] = 'agent-live'
    storage.save(run)
    service._runs[run.run_id] = run
    listed,total = service.list_runs()
    assert total == 1 and listed[0].status.value == 'running'
    assert listed[0].config_snapshot['active_agent_run_id'] == 'agent-live'
    assert service.get_run(run.run_id).status.value == 'running'


def test_existing_v23_full_snapshot_is_migrated_to_summary_without_losing_evidence(monkeypatch):
    from app.database import migrations
    from contextlib import closing
    with monkeypatch.context() as old:
        old.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:23])
        with closing(connect_knowledge()) as conn:
            run = record('old')
            conn.execute('INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?,?)',
                (run.run_id,datasets.current_scope(),run.kind.value,run.status.value,run.created_at.isoformat(),run.model_dump_json(),None))
    assert 'dataset_cases' not in storage.get_summary('old').config_snapshot
    assert storage.get('old')[0].config_snapshot['dataset_cases'] == [{'query':'synthetic'}]


def test_summary_route_does_not_block_loop_and_preserves_request_context(monkeypatch):
    from app import routes
    request_scope = ContextVar('benchmark_test_scope')
    started, released = Event(), Event()
    def delayed_list(**_):
        assert request_scope.get() == 'vault-request'
        started.set()
        assert released.wait(3)
        return [],0
    monkeypatch.setattr(service, 'list_runs', delayed_list)
    async def exercise():
        request_scope.set('vault-request')
        task = asyncio.create_task(routes.list_benchmark_runs(kind=None,status=None,limit=50,offset=0))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not task.done()  # The request loop ran while DB work waited.
        finally:
            released.set()
        assert (await task).page.total == 0
    asyncio.run(exercise())
