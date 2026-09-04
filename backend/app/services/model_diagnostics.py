"""Bounded, durable diagnostics. No payloads, paths, exception text or credentials."""
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
