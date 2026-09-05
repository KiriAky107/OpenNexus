from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any
from uuid import uuid4

from app.contracts import ChatMessage, Conversation
from app.database.db import connect, transaction
from app.errors import ApiError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conversation(row) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        message_count=row["message_count"],
    )


def _message(row) -> ChatMessage:
    citations = json.loads(row["citations_json"])
    for citation in citations:
        if isinstance(citation.get("heading_path"), list):
            citation["heading_path"] = " / ".join(str(part) for part in citation["heading_path"])
    return ChatMessage(
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        thinking=row["thinking"],
        citations=citations,
        tool_calls=json.loads(row["tool_calls_json"]),
        usage=json.loads(row["usage_json"]) if row["usage_json"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create(title: str, conversation_id: str | None = None) -> Conversation:
    conversation_id = conversation_id or f"conversation_{uuid4().hex}"
    now = _now().isoformat()
    with closing(connect()) as conn, transaction(conn):
        try:
            conn.execute(
                "INSERT INTO chat_conversations(conversation_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (conversation_id, title.strip(), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "CONVERSATION_ALREADY_EXISTS", "conversation already exists", {"conversation_id": conversation_id}) from exc
    result = get(conversation_id)
    assert result is not None
    return result


def get(conversation_id: str) -> Conversation | None:
    with closing(connect()) as conn:
        row = conn.execute(
            """SELECT c.*, COUNT(m.message_id) AS message_count
               FROM chat_conversations c LEFT JOIN chat_messages m USING(conversation_id)
               WHERE c.conversation_id=? GROUP BY c.conversation_id""",
            (conversation_id,),
        ).fetchone()
        return _conversation(row) if row else None


def list_conversations(limit: int, offset: int) -> tuple[list[Conversation], int]:
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chat_conversations").fetchone()[0]
        rows = conn.execute(
            """SELECT c.*, COUNT(m.message_id) AS message_count
               FROM chat_conversations c LEFT JOIN chat_messages m USING(conversation_id)
               GROUP BY c.conversation_id ORDER BY c.updated_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [_conversation(row) for row in rows], total


def list_messages(conversation_id: str, limit: int, offset: int) -> tuple[list[ChatMessage], int]:
    if get(conversation_id) is None:
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "conversation not found", {"conversation_id": conversation_id})
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY sequence LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        ).fetchall()
        return [_message(row) for row in rows], total


def delete(conversation_id: str) -> bool:
    with closing(connect()) as conn, transaction(conn):
        return conn.execute("DELETE FROM chat_conversations WHERE conversation_id=?", (conversation_id,)).rowcount > 0


def append_message(
    conversation_id: str,
    *,
    message_id: str,
    role: str,
    content: str,
    title: str | None = None,
    thinking: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    now = _now().isoformat()
    clean_title = (title or "").strip() or content[:30].strip() or "New conversation"
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _append_message_in_transaction(
                conn, conversation_id, message_id=message_id, role=role, content=content,
                title=clean_title, thinking=thinking, citations=citations, tool_calls=tool_calls,
                usage=usage, now=now,
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _append_message_in_transaction(
    conn,
    conversation_id: str,
    *,
    message_id: str,
    role: str,
    content: str,
    title: str,
    thinking: str | None,
    citations: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]] | None,
    usage: dict[str, Any] | None,
    now: str,
) -> None:
    conversation = conn.execute(
        "SELECT 1 FROM chat_conversations WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        # A stream may finish after deletion. Check under BEGIN IMMEDIATE so
        # deletion and assistant persistence cannot recreate an orphaned chat.
        if role == "assistant":
            return
        conn.execute(
            "INSERT INTO chat_conversations(conversation_id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (conversation_id, title, now, now),
        )
    count = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?", (conversation_id,)
    ).fetchone()[0]
    if count == 0:
        conn.execute(
            "UPDATE chat_conversations SET title=? WHERE conversation_id=?",
            (title, conversation_id),
        )
    existing = conn.execute(
        "SELECT conversation_id FROM chat_messages WHERE message_id=?", (message_id,)
    ).fetchone()
    if existing:
        if existing["conversation_id"] != conversation_id:
            raise ApiError(409, "MESSAGE_ID_CONFLICT", "message id belongs to another conversation")
        return
    sequence = conn.execute(
        "SELECT COALESCE(MAX(sequence), -1) + 1 FROM chat_messages WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO chat_messages(message_id,conversation_id,sequence,role,content,thinking,citations_json,tool_calls_json,usage_json,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (message_id, conversation_id, sequence, role, content, thinking,
         json.dumps(citations or [], ensure_ascii=False), json.dumps(tool_calls or [], ensure_ascii=False),
         json.dumps(usage, ensure_ascii=False) if usage is not None else None, now),
    )
    conn.execute(
        "UPDATE chat_conversations SET updated_at=? WHERE conversation_id=?",
        (now, conversation_id),
    )
