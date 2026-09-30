"""Persist run snapshots and evidence in the active vault database."""
from contextlib import closing
from app.database.db import connect_knowledge, transaction
from app.contracts import BenchmarkRun, BenchmarkReport, BenchmarkEvent
from app.benchmarks.datasets import current_scope

def save(run: BenchmarkRun, report: BenchmarkReport | None = None, event: BenchmarkEvent | None = None):
    with closing(connect_knowledge()) as conn, transaction(conn, immediate=True):
        conn.execute('''INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,run_json=excluded.run_json,
            report_json=COALESCE(excluded.report_json,benchmark_runs.report_json)''',
            (run.run_id, current_scope(), run.kind.value, run.status.value, run.created_at.isoformat(),
             run.model_dump_json(), report.model_dump_json() if report else None))
        if event:
            conn.execute('INSERT OR IGNORE INTO benchmark_events VALUES(?,?,?)', (run.run_id,event.sequence,event.model_dump_json()))

def get(run_id):
    with closing(connect_knowledge()) as conn:
        row = conn.execute('SELECT run_json,report_json FROM benchmark_runs WHERE run_id=? AND scope=?',(run_id,current_scope())).fetchone()
        return (BenchmarkRun.model_validate_json(row[0]), BenchmarkReport.model_validate_json(row[1]) if row[1] else None) if row else (None,None)

def list_runs(kind=None,status=None,limit=50,offset=0):
    where,params=['scope=?'],[current_scope()]
    if kind: where.append('kind=?');params.append(kind.value)
    if status: where.append('status=?');params.append(status.value)
    condition=' AND '.join(where)
    with closing(connect_knowledge()) as conn:
        total=conn.execute(f'SELECT COUNT(*) FROM benchmark_runs WHERE {condition}',params).fetchone()[0]
        rows=conn.execute(f'SELECT run_json FROM benchmark_runs WHERE {condition} ORDER BY created_at DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
        return [BenchmarkRun.model_validate_json(row[0]) for row in rows],total

def events(run_id):
    with closing(connect_knowledge()) as conn:
        rows=conn.execute('''SELECT e.event_json FROM benchmark_events e JOIN benchmark_runs r ON r.run_id=e.run_id
            WHERE r.run_id=? AND r.scope=? ORDER BY e.sequence''',(run_id,current_scope())).fetchall()
        return [BenchmarkEvent.model_validate_json(row[0]) for row in rows]

def unfinished_ids():
    with closing(connect_knowledge()) as conn:
        return [row[0] for row in conn.execute("SELECT run_id FROM benchmark_runs WHERE scope=? AND status IN ('queued','running')",(current_scope(),))]
