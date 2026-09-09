from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
import asyncio
from contextvars import copy_context
from functools import partial
from weakref import WeakKeyDictionary

from app import repository
from app.contracts import Task, TaskStatus
from app.database.db import connect_knowledge as connect, transaction
from app.errors import ApiError
from app.operation_logs import log_event

def _desktop():
    from app.config import get_settings
    return get_settings().environment == 'desktop'


_write_locks = WeakKeyDictionary()


async def write_in_background(operation, *args, **kwargs):
    # SQLite 有 1 个写入器。协作排队，而不是让许多工作线程争夺文件锁并导致不相关的模型工作匮乏。
    loop = asyncio.get_running_loop()
    lock = _write_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        work = loop.run_in_executor(None, copy_context().run, partial(operation, *args, **kwargs))
        cancelled = False
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                cancelled = True
        result = work.result()
        if cancelled:
            raise asyncio.CancelledError
        return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prepare_note_link(note_id: str | None) -> None:
    from app.config import get_settings
    if note_id and get_settings().environment == 'desktop':
        from app.services.desktop_projection import _refresh
        _refresh()


def _task_from_row(row) -> Task:
    return Task(
        task_id=row["task_id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        note_id=row["note_id"],
        due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_task(
    *, title: str, description: str = "", note_id: str | None = None,
    due_at: datetime | None = None,
) -> Task:
    if _desktop():
        from app.services import desktop_tasks
        return desktop_tasks.create(title=title, description=description, note_id=note_id, due_at=due_at)
    _prepare_note_link(note_id)
    if note_id and repository.get_note_record(note_id) is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id})
    task_id = f"task_{uuid4().hex}"
    now = _now()
    conn = connect()
    try:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO tasks
                    (task_id, title, description, status, note_id, due_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, title, description, TaskStatus.todo.value, note_id,
                    due_at.isoformat() if due_at else None, now.isoformat(), now.isoformat(),
                ),
            )
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        log_event('tasks', 'task.created', task_id=task_id, note_id=note_id, status='todo')
        return _task_from_row(row)
    finally:
        conn.close()


def get_task(task_id: str) -> Task | None:
    if _desktop():
        from app.services import desktop_tasks
        return desktop_tasks.get(task_id)
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None
    finally:
        conn.close()


def list_tasks(*, limit: int, offset: int) -> tuple[list[Task], int]:
    if _desktop():
        from app.services import desktop_tasks
        return desktop_tasks.list_tasks(limit=limit, offset=offset)
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_task_from_row(row) for row in rows], total
    finally:
        conn.close()


def update_task(task_id: str, values: dict[str, object]) -> Task:
    if _desktop():
        from app.services import desktop_tasks
        return desktop_tasks.update(task_id, values)
    current = get_task(task_id)
    if current is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id})
    if "note_id" in values and values["note_id"]:
        note_id = str(values["note_id"])
        _prepare_note_link(note_id)
        if repository.get_note_record(note_id) is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id})
    if values.get("title") is None:
        values.pop("title", None)
    if values.get("description") is None:
        values.pop("description", None)
    if values.get("status") is None:
        values.pop("status", None)

    columns: list[str] = []
    params: list[object] = []
    for name, value in values.items():
        columns.append(f"{name} = ?")
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, TaskStatus):
            value = value.value
        params.append(value)
    columns.append("updated_at = ?")
    params.append(_now().isoformat())
    params.append(task_id)
    conn = connect()
    try:
        with transaction(conn):
            conn.execute(
                f"UPDATE tasks SET {', '.join(columns)} WHERE task_id = ?",
                params,
            )
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        log_event('tasks', 'task.updated', task_id=task_id, status=row['status'], changed_fields=','.join(values))
        return _task_from_row(row)
    finally:
        conn.close()


def delete_task(task_id: str) -> bool:
    if _desktop():
        from app.services import desktop_tasks
        return desktop_tasks.delete(task_id)
    conn = connect()
    try:
        with transaction(conn):
            cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        log_event('tasks', 'task.deleted' if cursor.rowcount else 'task.not_found', task_id=task_id)
        return cursor.rowcount > 0
    finally:
        conn.close()


def note_links() -> dict[str, str]:
    """重建可再生 Note 索引前，暂存不可再生 Task 到 Note 的业务关联。"""
    conn = connect()
    try:
        return {
            row["task_id"]: row["note_id"]
            for row in conn.execute(
                "SELECT task_id, note_id FROM tasks WHERE note_id IS NOT NULL"
            )
        }
    finally:
        conn.close()


def restore_note_links(links: dict[str, str]) -> None:
    if not links:
        return
    conn = connect()
    try:
        with transaction(conn):
            for task_id, note_id in links.items():
                exists = conn.execute(
                    "SELECT 1 FROM notes WHERE note_id = ?", (note_id,)
                ).fetchone()
                if exists:
                    conn.execute(
                        "UPDATE tasks SET note_id = ? WHERE task_id = ?",
                        (note_id, task_id),
                    )
    finally:
        conn.close()
