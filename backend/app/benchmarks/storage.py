"""Persist frozen inputs once, and atomically append evidence and small progress rows.

``benchmark_inputs`` keeps frozen configuration outside the mutable run row.
``summary_json`` is the current state; reports rehydrate their dataset on reads.
Migration v25 extracts beta1 inputs without losing report or event evidence.
"""
import json
from contextlib import closing
from typing import Any
from pydantic import TypeAdapter
from app.database.db import connect_knowledge, transaction
from app.contracts import BenchmarkRun, BenchmarkReport, BenchmarkEvent, BenchmarkEventType, BenchmarkStatus
from app.benchmarks.datasets import current_scope

_CONFIG_JSON = TypeAdapter(dict[str, Any])

def summary(run: BenchmarkRun) -> BenchmarkRun:
    # Full frozen inputs remain in benchmark_inputs for audit and export.
    return run.model_copy(update={'config_snapshot': {
        key: value for key, value in run.config_snapshot.items() if key != 'dataset_cases'
    }})


def save(run: BenchmarkRun, report: BenchmarkReport | None = None, event: BenchmarkEvent | None = None):
    with closing(connect_knowledge()) as conn, transaction(conn, immediate=True):
        scope = current_scope()
        existing = conn.execute('SELECT scope,status FROM benchmark_runs WHERE run_id=?', (run.run_id,)).fetchone()
        if existing and existing['scope'] != scope:
            raise ValueError('Benchmark scope mismatch')
        if event and event.run_id != run.run_id:
            raise ValueError('Benchmark event belongs to another run')
        event_json = event.model_dump_json() if event else None
        if existing and event:
            previous = conn.execute('SELECT event_json FROM benchmark_events WHERE run_id=? AND sequence=?',
                (run.run_id, event.sequence)).fetchone()
            if previous:
                if previous[0] != event_json:
                    raise ValueError('Conflicting benchmark event sequence')
                return  # An acknowledged retry must not roll progress backwards.
            last = conn.execute('SELECT MAX(sequence) FROM benchmark_events WHERE run_id=?', (run.run_id,)).fetchone()[0]
            if event.sequence != (last + 1 if last is not None else 0):
                raise ValueError('Out-of-order benchmark event sequence')
        if existing and existing['status'] not in ('queued', 'running'):
            raise ValueError('Benchmark is already terminal')
        report_json = report.model_dump_json(exclude={'config_snapshot': {'dataset_cases'}}) if report else None
        summary_json = summary(run).model_dump_json()
        if existing:
            conn.execute('''UPDATE benchmark_runs SET status=?,summary_json=?,
                report_json=COALESCE(?,report_json) WHERE run_id=?''',
                (run.status.value, summary_json, report_json, run.run_id))
        else:
            conn.execute('''INSERT INTO benchmark_runs(run_id,scope,kind,status,created_at,run_json,report_json,summary_json)
                VALUES(?,?,?,?,?,?,?,?)''',
                (run.run_id, scope, run.kind.value, run.status.value, run.created_at.isoformat(),
                 summary_json, report_json, summary_json))
            conn.execute('INSERT INTO benchmark_inputs(run_id,config_json) VALUES(?,?)',
                (run.run_id, _CONFIG_JSON.dump_json(run.config_snapshot).decode('utf-8')))
        if event:
            conn.execute('INSERT INTO benchmark_events VALUES(?,?,?)', (run.run_id,event.sequence,event_json))


def _hydrate_run(snapshot_json, summary_json, config_json):
    frozen = BenchmarkRun.model_validate_json(snapshot_json)
    latest = BenchmarkRun.model_validate_json(summary_json) if summary_json else frozen
    inputs = json.loads(config_json) if config_json else frozen.config_snapshot
    if 'dataset_cases' in inputs:
        latest.config_snapshot['dataset_cases'] = inputs['dataset_cases']
    return latest

def get(run_id):
    with closing(connect_knowledge()) as conn:
        row = conn.execute('''SELECT r.run_json,r.report_json,r.summary_json,i.config_json FROM benchmark_runs r
            LEFT JOIN benchmark_inputs i ON i.run_id=r.run_id WHERE r.run_id=? AND r.scope=?''',(run_id,current_scope())).fetchone()
        if not row:
            return None, None
        run = _hydrate_run(row[0], row[2], row[3])
        report = BenchmarkReport.model_validate_json(row[1]) if row[1] else None
        if report and 'dataset_cases' in run.config_snapshot:
            report.config_snapshot['dataset_cases'] = run.config_snapshot['dataset_cases']
        return run, report


def get_summary(run_id):
    with closing(connect_knowledge()) as conn:
        row = conn.execute('SELECT summary_json FROM benchmark_runs WHERE run_id=? AND scope=?', (run_id, current_scope())).fetchone()
        return BenchmarkRun.model_validate_json(row[0]) if row else None


def existing_ids(run_ids):
    if not run_ids:
        return set()
    with closing(connect_knowledge()) as conn:
        placeholders = ','.join('?' for _ in run_ids)
        return {row[0] for row in conn.execute(f'SELECT run_id FROM benchmark_runs WHERE scope=? AND run_id IN ({placeholders})', [current_scope(), *run_ids])}

def list_runs(kind=None,status=None,limit=50,offset=0):
    where,params=['scope=?'],[current_scope()]
    if kind: where.append('kind=?');params.append(kind.value)
    if status: where.append('status=?');params.append(status.value)
    condition=' AND '.join(where)
    with closing(connect_knowledge()) as conn:
        total=conn.execute(f'SELECT COUNT(*) FROM benchmark_runs WHERE {condition}',params).fetchone()[0]
        rows=conn.execute(f'SELECT summary_json FROM benchmark_runs WHERE {condition} ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
        return [BenchmarkRun.model_validate_json(row[0]) for row in rows],total

def events(run_id):
    with closing(connect_knowledge()) as conn:
        rows=conn.execute('''SELECT e.event_json FROM benchmark_events e JOIN benchmark_runs r ON r.run_id=e.run_id
            WHERE r.run_id=? AND r.scope=? ORDER BY e.sequence''',(run_id,current_scope())).fetchall()
        return [BenchmarkEvent.model_validate_json(row[0]) for row in rows]

def unfinished_ids():
    with closing(connect_knowledge()) as conn:
        return [row[0] for row in conn.execute("SELECT run_id FROM benchmark_runs WHERE scope=? AND status IN ('queued','running')",(current_scope(),))]


def recover_interrupted(run_id, now):
    """Recover only nonterminal evidence, once even with concurrent readers."""
    with closing(connect_knowledge()) as conn, transaction(conn, immediate=True):
        row = conn.execute('''SELECT r.run_json,r.summary_json,i.config_json FROM benchmark_runs r
            LEFT JOIN benchmark_inputs i ON i.run_id=r.run_id
            WHERE r.run_id=? AND r.scope=? AND r.status IN ('queued','running')''', (run_id, current_scope())).fetchone()
        if not row:
            return
        run = _hydrate_run(row[0], row[1], row[2]).model_copy(update={
            'status': BenchmarkStatus.failed, 'completed_at': now,
            'error_code': 'BENCHMARK_INTERRUPTED', 'error': '应用重启中断了该次评测。',
        })
        evidence = [BenchmarkEvent.model_validate_json(row[0]) for row in conn.execute('SELECT event_json FROM benchmark_events WHERE run_id=? ORDER BY sequence', (run_id,))]
        report = BenchmarkReport(run_id=run_id, kind=run.kind, dataset_id=run.dataset_id,
            dataset_hash=run.dataset_hash, status=run.status, config_snapshot=run.config_snapshot,
            error_code=run.error_code, error=run.error,
            cases=[event.data for event in evidence if event.event == BenchmarkEventType.case_completed])
        conn.execute('UPDATE benchmark_runs SET status=?,report_json=?,summary_json=? WHERE run_id=?',
            (run.status.value, report.model_dump_json(exclude={'config_snapshot': {'dataset_cases'}}), summary(run).model_dump_json(), run_id))
        event = BenchmarkEvent(event=BenchmarkEventType.run_failed, run_id=run_id,
            sequence=max((event.sequence for event in evidence), default=-1)+1,
            data={'error_code':run.error_code}, timestamp=now)
        conn.execute('INSERT INTO benchmark_events VALUES(?,?,?)', (run_id, event.sequence, event.model_dump_json()))
