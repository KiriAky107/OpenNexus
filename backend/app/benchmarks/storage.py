"""Persist run snapshots and evidence in the active vault database."""
from contextlib import closing
from app.database.db import connect_knowledge, transaction
from app.contracts import BenchmarkRun, BenchmarkReport, BenchmarkEvent, BenchmarkEventType, BenchmarkStatus
from app.benchmarks.datasets import current_scope

def summary(run: BenchmarkRun) -> BenchmarkRun:
    # Full frozen inputs remain in run_json/report_json for audit and export.
    return run.model_copy(update={'config_snapshot': {
        key: value for key, value in run.config_snapshot.items() if key != 'dataset_cases'
    }})


def save(run: BenchmarkRun, report: BenchmarkReport | None = None, event: BenchmarkEvent | None = None):
    with closing(connect_knowledge()) as conn, transaction(conn, immediate=True):
        conn.execute('''INSERT INTO benchmark_runs(run_id,scope,kind,status,created_at,run_json,report_json,summary_json) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,run_json=excluded.run_json,
            summary_json=excluded.summary_json,report_json=COALESCE(excluded.report_json,benchmark_runs.report_json)''',
            (run.run_id, current_scope(), run.kind.value, run.status.value, run.created_at.isoformat(),
             run.model_dump_json(), report.model_dump_json() if report else None, summary(run).model_dump_json()))
        if event:
            conn.execute('INSERT OR IGNORE INTO benchmark_events VALUES(?,?,?)', (run.run_id,event.sequence,event.model_dump_json()))

def get(run_id):
    with closing(connect_knowledge()) as conn:
        row = conn.execute('SELECT run_json,report_json FROM benchmark_runs WHERE run_id=? AND scope=?',(run_id,current_scope())).fetchone()
        return (BenchmarkRun.model_validate_json(row[0]), BenchmarkReport.model_validate_json(row[1]) if row[1] else None) if row else (None,None)


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
        row = conn.execute("SELECT run_json FROM benchmark_runs WHERE run_id=? AND scope=? AND status IN ('queued','running')", (run_id, current_scope())).fetchone()
        if not row:
            return
        run = BenchmarkRun.model_validate_json(row[0]).model_copy(update={
            'status': BenchmarkStatus.failed, 'completed_at': now,
            'error_code': 'BENCHMARK_INTERRUPTED', 'error': '应用重启中断了该次评测。',
        })
        evidence = [BenchmarkEvent.model_validate_json(row[0]) for row in conn.execute('SELECT event_json FROM benchmark_events WHERE run_id=? ORDER BY sequence', (run_id,))]
        report = BenchmarkReport(run_id=run_id, kind=run.kind, dataset_id=run.dataset_id,
            dataset_hash=run.dataset_hash, status=run.status, config_snapshot=run.config_snapshot,
            error_code=run.error_code, error=run.error,
            cases=[event.data for event in evidence if event.event == BenchmarkEventType.case_completed])
        conn.execute('UPDATE benchmark_runs SET status=?,run_json=?,report_json=?,summary_json=? WHERE run_id=?',
            (run.status.value, run.model_dump_json(), report.model_dump_json(), summary(run).model_dump_json(), run_id))
        event = BenchmarkEvent(event=BenchmarkEventType.run_failed, run_id=run_id,
            sequence=max((event.sequence for event in evidence), default=-1)+1,
            data={'error_code':run.error_code}, timestamp=now)
        conn.execute('INSERT INTO benchmark_events VALUES(?,?,?)', (run_id, event.sequence, event.model_dump_json()))
