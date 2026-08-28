from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app import repository
from app.contracts import Task, TaskStatus
from app.database.db import connect, transaction
from app.errors import ApiError


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        return _task_from_row(row)
    finally:
        conn.close()


def get_task(task_id: str) -> Task | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None
    finally:
        conn.close()


def list_tasks(*, limit: int, offset: int) -> tuple[list[Task], int]:
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
    current = get_task(task_id)
    if current is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id})
    if "note_id" in values and values["note_id"]:
        note_id = str(values["note_id"])
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
        return _task_from_row(row)
    finally:
        conn.close()


def delete_task(task_id: str) -> bool:
    conn = connect()
    try:
        with transaction(conn):
            cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
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
