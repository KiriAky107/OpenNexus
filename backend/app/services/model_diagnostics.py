"""有界、持久的诊断。没有有效负载、路径、异常文本或凭据。"""
import json
import logging
import math
from contextlib import closing
from datetime import datetime, timezone

from app.database.db import connect, transaction

TEXT = {"model", "revision", "operation", "source", "requested_device", "actual_device",
        "attempted_device", "fallback_reason", "error_code", "status", "request_id", "attempt_id"}
NUMBERS = {"load_seconds", "inference_seconds", "elapsed_seconds", "peak_memory_bytes", "queue_seconds"}


def connection():
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS model_diagnostics (id INTEGER PRIMARY KEY AUTOINCREMENT, record_json TEXT NOT NULL)")
    return conn


def record(**values):
    from app.operation_logs import log_event
    log_event('models', 'model.' + str(values.get('operation', 'inference')),
              level='ERROR' if values.get('status') == 'failed' else 'WARNING' if values.get('status') == 'fallback' else 'INFO',
              model=values.get('model'), source=values.get('source'), status=values.get('status'),
              device=values.get('actual_device') or values.get('attempted_device'),
              error_code=values.get('error_code'), fallback=values.get('fallback_reason'),
              duration_ms=round(values.get('elapsed_seconds', 0) * 1000, 2))
    safe = {key: value[:240] for key, value in values.items() if key in TEXT and isinstance(value, str)}
    safe.update({key: value for key, value in values.items()
                 if key in NUMBERS and type(value) in (float, int) and math.isfinite(value) and value >= 0})
    safe["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        with closing(connection()) as conn, transaction(conn):
            conn.execute("INSERT INTO model_diagnostics(record_json) VALUES (?)", (json.dumps(safe),))
            conn.execute("DELETE FROM model_diagnostics WHERE id NOT IN (SELECT id FROM model_diagnostics ORDER BY id DESC LIMIT 200)")
    except Exception:
        logging.getLogger(__name__).warning("Model diagnostic persistence failed")
    return safe


def recent():
    with closing(connection()) as conn:
        return [json.loads(row[0]) for row in conn.execute("SELECT record_json FROM model_diagnostics ORDER BY id")]
