"""Bounded, asynchronous operational diagnostics, separate from business/Trace data.

Only explicitly allowed metadata is stored. Never store prompts, tool arguments,
provider response bodies or raw exception messages in this diagnostic channel.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import re
import sqlite3
import threading
import traceback
from contextvars import ContextVar
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

request_id: ContextVar[str] = ContextVar('log_request_id', default='')
agent_run_id: ContextVar[str] = ContextVar('log_agent_run_id', default='')
_allowed = {'run_id', 'task_id', 'note_id', 'job_id', 'provider_id', 'model',
            'device', 'error_code', 'error_type', 'status', 'duration_ms', 'count',
            'step', 'sequence', 'tool', 'method', 'route', 'request_id', 'fallback',
            'frames', 'source', 'changed_fields'}
_safe = re.compile(r'[^\w .:/@{}\[\],()=+\-]', re.UNICODE)


def metadata(values: dict) -> dict:
    result = {}
    for key, value in values.items():
        if key not in _allowed or value is None:
            continue
        if isinstance(value, (int, float, bool)):
            if not isinstance(value, float) or math.isfinite(value):
                result[key] = value
        else:
            text = str(value)
            text = re.sub(r'(?i)(?:bearer\s+\S+|sk-[\w-]+)', '[REDACTED]', text)
            result[key] = _safe.sub('', text)[:500]
    return result


class LogStore:
    def __init__(self, path: Path, *, retain: int = 20_000):
        self.path = path
        self.retain = retain
        self.queue: queue.Queue = queue.Queue(maxsize=4096)
        self.dropped = 0
        self.failed = 0
        self.closed = False
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self._write, name='operation-logs', daemon=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, level TEXT NOT NULL, source TEXT NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS logs_level_id ON logs(level, id)')
            conn.execute('CREATE INDEX IF NOT EXISTS logs_source_id ON logs(source, id)')
        self.thread.start()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def emit(self, level: str, source: str, event: str, details: dict):
        row = (datetime.now(timezone.utc).isoformat(), level, source[:100], event[:160], json.dumps(metadata(details), ensure_ascii=False))
        with self.state_lock:
            if self.closed:
                return
            try:
                self.queue.put_nowait(row)
            except queue.Full:
                self.dropped += 1

    def _write(self):
        while True:
            first = self.queue.get()
            batch = [first]
            while len(batch) < 128:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            stop = None in batch
            rows = [row for row in batch if row is not None]
            try:
                if rows:
                    with closing(self._connect()) as conn, conn:
                        conn.executemany('INSERT INTO logs(timestamp,level,source,event,details) VALUES(?,?,?,?,?)', rows)
                        conn.execute('DELETE FROM logs WHERE id <= (SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET ?)', (self.retain,))
            except Exception:
                self.failed += len(rows)
            finally:
                for _ in batch:
                    self.queue.task_done()
            if stop:
                return

    def query(self, *, limit=50, before=None, level='', source='', q=''):
        clauses, args = [], []
        for column, value in [('level', level), ('source', source)]:
            if value:
                clauses.append(f'{column} = ?')
                args.append(value)
        if before is not None:
            clauses.append('id < ?')
            args.append(before)
        if q:
            clauses.append('(instr(event, ?) > 0 OR instr(details, ?) > 0)')
            args += [q, q]
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with closing(self._connect()) as conn, conn:
            rows = conn.execute('SELECT * FROM logs' + where + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
            sources = [row[0] for row in conn.execute('SELECT DISTINCT source FROM logs ORDER BY source')]
        items = [{**dict(row), 'details': json.loads(row['details'])} for row in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['id'] if len(rows) > limit else None,
                'sources': sources, 'pending': self.queue.qsize(), 'dropped': self.dropped,
                'write_failures': self.failed, 'retention': self.retain}

    def close(self):
        with self.state_lock:
            if self.closed:
                return
            self.closed = True
        self.queue.put(None)
        self.thread.join(timeout=15)


_store: LogStore | None = None
_lock = threading.Lock()


def get_store() -> LogStore:
    global _store
    path = get_settings().data_dir / 'logs' / 'operations.sqlite3'
    with _lock:
        if _store is None or _store.path != path or _store.closed:
            if _store is not None and not _store.closed:
                _store.close()
            _store = LogStore(path)
        return _store


def log_event(module: str, event: str, *, level='INFO', error: BaseException | None = None, **details):
    if request_id.get():
        details.setdefault('request_id', request_id.get())
    if agent_run_id.get():
        details.setdefault('run_id', agent_run_id.get())
    if error:
        details['error_type'] = type(error).__name__
        details.setdefault('error_code', getattr(error, 'code', None))
        details['frames'] = '; '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(error.__traceback__)[-8:])
    try:
        get_store().emit(level, module, event, details)
    except Exception:
        # Logging must not turn a successful save/run into a business failure.
        logging.getLogger('operation_log_storage').error('Operational log storage unavailable')


class ApplicationLogHandler(logging.Handler):
    def emit(self, record):
        if record.name == 'operation_log_storage' or getattr(record, '_notes_operation_logged', False):
            return
        record._notes_operation_logged = True
        # Legacy log messages can include note text/credentials, even in f-strings.
        # Preserve source location and error class; structured call sites carry IDs.
        log_event(record.name, 'application.warning' if record.levelno < 40 else 'application.error',
                  level=record.levelname, error=record.exc_info[1] if record.exc_info else None,
                  frames=f'{Path(record.pathname).name}:{record.lineno}:{record.funcName}')


def install_logging():
    # Uvicorn's default logger stops propagation before the root logger.
    for name in ('', 'uvicorn'):
        logger = logging.getLogger(name)
        if not any(isinstance(h, ApplicationLogHandler) for h in logger.handlers):
            logger.addHandler(ApplicationLogHandler(level=logging.WARNING))


def shutdown_logging():
    if _store is not None and not _store.closed:
        _store.close()
