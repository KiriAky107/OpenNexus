"""Agent Run/Event 持久化与 Trace 查询。

SQLite 中的事件是 SSE、前端 Trace 和 Benchmark 的共同事实来源。写入前统一脱敏和
限长，避免 Secret 或无限大的 Tool Result 进入审计数据。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.contracts import (
    AgentEvent,
    AgentEventType,
    AgentRun,
    AgentRunCreateRequest,
    AgentRunStatus,
    AgentTraceResponse,
    AgentTraceSummary,
)
from app.database.db import connect, transaction

MAX_TRACE_STRING = 4_096
MAX_TRACE_COLLECTION = 100
MAX_TRACE_DEPTH = 8
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "secret",
    "token",
}
_SECRET_KEY_SUFFIXES = ("_api_key", "_password", "_secret")
_TERMINAL_VALUES = {
    AgentRunStatus.completed.value,
    AgentRunStatus.failed.value,
    AgentRunStatus.cancelled.value,
}
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def sanitize_trace_value(
    value: Any, *, depth: int = 0, apply_limits: bool = True
) -> Any:
    """递归净化持久化数据；可按审计用途限制体积，Secret 始终脱敏。"""

    if apply_limits and depth >= MAX_TRACE_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if apply_limits and index >= MAX_TRACE_COLLECTION:
                sanitized["__truncated__"] = True
                break
            normalized = str(key).casefold().replace("-", "_")
            sanitized[str(key)] = (
                "[REDACTED]"
                if normalized in _SECRET_KEYS
                or normalized.endswith(_SECRET_KEY_SUFFIXES)
                else sanitize_trace_value(
                    item, depth=depth + 1, apply_limits=apply_limits
                )
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        source_items = value[:MAX_TRACE_COLLECTION] if apply_limits else value
        items = [
            sanitize_trace_value(
                item, depth=depth + 1, apply_limits=apply_limits
            )
            for item in source_items
        ]
        if apply_limits and len(value) > MAX_TRACE_COLLECTION:
            items.append("[TRUNCATED]")
        return items
    if isinstance(value, str):
        value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        value = _API_KEY_PATTERN.sub("[REDACTED]", value)
        if apply_limits and len(value) > MAX_TRACE_STRING:
            return f"{value[:MAX_TRACE_STRING]}...[TRUNCATED]"
        return value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return sanitize_trace_value(
        str(value), depth=depth + 1, apply_limits=apply_limits
    )


class AgentTraceRepository:
    def create_run(
        self,
        run: AgentRun,
        request: AgentRunCreateRequest,
        config_snapshot: dict[str, Any],
    ) -> None:
        conn = connect()
        try:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO agent_runs(
                        run_id, status, run_json, request_json, config_snapshot_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.status.value,
                        self._serialize_run(run),
                        json.dumps(
                            sanitize_trace_value(request.model_dump(mode="json")),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            sanitize_trace_value(config_snapshot), ensure_ascii=False
                        ),
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                    ),
                )
        finally:
            conn.close()

    def save_run(self, run: AgentRun) -> None:
        conn = connect()
        try:
            with transaction(conn):
                self._update_run(conn, run)
        finally:
            conn.close()

    def append_event(self, run: AgentRun, event: AgentEvent) -> None:
        """在同一事务中保存最新 Run 和事件；复写同一序号时保持幂等。"""

        conn = connect()
        try:
            with transaction(conn):
                self._update_run(conn, run)
                conn.execute(
                    """
                    INSERT INTO agent_events(run_id, sequence, event, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, sequence) DO NOTHING
                    """,
                    (
                        event.run_id,
                        event.sequence,
                        event.event.value,
                        json.dumps(event.data, ensure_ascii=False),
                        event.timestamp.isoformat(),
                    ),
                )
        finally:
            conn.close()

    def get_run(self, run_id: str) -> AgentRun | None:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT run_json FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            return AgentRun.model_validate_json(row["run_json"]) if row else None
        finally:
            conn.close()

    def list_runs(self, limit: int, offset: int) -> tuple[list[AgentRun], int]:
        conn = connect()
        try:
            total = int(conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0])
            rows = conn.execute(
                """
                SELECT run_json FROM agent_runs
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [AgentRun.model_validate_json(row["run_json"]) for row in rows], total
        finally:
            conn.close()

    def list_events(
        self, run_id: str, *, after_sequence: int = -1, limit: int | None = None
    ) -> list[AgentEvent]:
        conn = connect()
        try:
            sql = """
                SELECT event, sequence, data_json, timestamp
                FROM agent_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence
            """
            params: tuple[Any, ...] = (run_id, after_sequence)
            if limit is not None:
                sql += " LIMIT ?"
                params += (limit,)
            return [self._event_from_row(run_id, row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def get_trace(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> AgentTraceResponse | None:
        conn = connect()
        try:
            row = conn.execute(
                """
                SELECT run_json, config_snapshot_json
                FROM agent_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            run = AgentRun.model_validate_json(row["run_json"])
            event_rows = conn.execute(
                """
                SELECT event, sequence, data_json, timestamp
                FROM agent_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (run_id, after_sequence, limit + 1),
            ).fetchall()
            has_more = len(event_rows) > limit
            items = [
                self._event_from_row(run_id, item) for item in event_rows[:limit]
            ]
            counts = {
                item["event"]: int(item["count"])
                for item in conn.execute(
                    """
                    SELECT event, COUNT(*) AS count
                    FROM agent_events WHERE run_id = ? GROUP BY event
                    """,
                    (run_id,),
                )
            }
            tool_errors = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM agent_events
                    WHERE run_id = ? AND event = 'ToolResult'
                      AND json_extract(data_json, '$.success') = 0
                    """,
                    (run_id,),
                ).fetchone()[0]
            )
            errors = (
                counts.get(AgentEventType.run_failed.value, 0)
                + counts.get(AgentEventType.model_call_failed.value, 0)
                + tool_errors
            )
            duration_ms = max(
                0, int((run.updated_at - run.created_at).total_seconds() * 1000)
            )
            return AgentTraceResponse(
                run_id=run_id,
                status=run.status,
                items=items,
                next_sequence=items[-1].sequence if items else after_sequence,
                has_more=has_more,
                summary=AgentTraceSummary(
                    model_calls=counts.get(AgentEventType.model_call_started.value, 0),
                    tool_calls=counts.get(AgentEventType.tool_call.value, 0),
                    duration_ms=duration_ms,
                    token_usage=run.token_usage,
                    errors=errors,
                ),
                config_snapshot=json.loads(row["config_snapshot_json"]),
            )
        finally:
            conn.close()

    def recover_interrupted(self, run_id: str) -> AgentRun | None:
        """把上个进程遗留的非终态 Run 收束为失败，并追加可回放终止事件。"""

        conn = connect()
        try:
            with transaction(conn):
                row = conn.execute(
                    "SELECT run_json, status FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    return None
                run = AgentRun.model_validate_json(row["run_json"])
                if row["status"] in _TERMINAL_VALUES:
                    return run
                run.status = AgentRunStatus.failed
                run.error_code = "AGENT_PROCESS_RESTARTED"
                run.error_message = "Agent process restarted before the run completed."
                run.updated_at = datetime.now(timezone.utc)
                next_sequence = int(
                    conn.execute(
                        """
                        SELECT COALESCE(MAX(sequence), -1) + 1
                        FROM agent_events WHERE run_id = ?
                        """,
                        (run_id,),
                    ).fetchone()[0]
                )
                event = AgentEvent(
                    event=AgentEventType.run_failed,
                    run_id=run_id,
                    sequence=next_sequence,
                    data={
                        "code": run.error_code,
                        "message": run.error_message,
                    },
                    timestamp=run.updated_at,
                )
                self._update_run(conn, run)
                conn.execute(
                    """
                    INSERT INTO agent_events(run_id, sequence, event, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        next_sequence,
                        event.event.value,
                        json.dumps(event.data, ensure_ascii=False),
                        event.timestamp.isoformat(),
                    ),
                )
                return run
        finally:
            conn.close()

    @staticmethod
    def _update_run(conn, run: AgentRun) -> None:
        cursor = conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, run_json = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                run.status.value,
                AgentTraceRepository._serialize_run(run),
                run.updated_at.isoformat(),
                run.run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError(run.run_id)

    @staticmethod
    def _event_from_row(run_id: str, row) -> AgentEvent:
        return AgentEvent(
            event=AgentEventType(row["event"]),
            run_id=run_id,
            sequence=int(row["sequence"]),
            data=json.loads(row["data_json"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    @staticmethod
    def _serialize_run(run: AgentRun) -> str:
        # Run 是重启后 GET/list 的完整事实；只做 Secret 脱敏，不套用 Trace 摘要限长。
        return json.dumps(
            sanitize_trace_value(
                run.model_dump(mode="json"), apply_limits=False
            ),
            ensure_ascii=False,
        )
