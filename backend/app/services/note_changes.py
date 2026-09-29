"""Vault-scoped content change receipts for AI writes and revision-checked restores."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import uuid4

from app.contracts import Note
from app.config import get_settings
from app import host_bridge
from app.database.db import connect_knowledge, transaction
from app.errors import ApiError
from app.services import note_service


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_ai_write(
    *, origin: str, requested_target: str, note_id: str | None,
    write: Callable[[str | None], Awaitable[Note]],
) -> Note:
    """Persist intent before the write; only a confirmed result becomes history.

    An interrupted operation remains pending rather than claiming a successful write.
    All updates pass the observed hash into the underlying Host/web compare-and-swap.
    """
    before = await note_service.get_note(note_id) if note_id else None
    if note_id and before is None:
        raise ApiError(404, 'RESOURCE_NOT_FOUND', 'note not found')
    expected = content_hash(before.markdown) if before else None
    change_id = str(uuid4())
    operation_id = host_bridge.operation_id.get() or change_id
    conn = connect_knowledge()
    try:
        previous = conn.execute(
            'SELECT note_id,after_hash,status FROM note_changes WHERE operation_id=?', (operation_id,)
        ).fetchone()
        if previous:
            if previous['status'] == 'applied' and previous['note_id']:
                existing = await note_service.get_note(previous['note_id'])
                if existing and content_hash(existing.markdown) == previous['after_hash']:
                    return existing
            raise ApiError(409, 'CHANGE_RECONCILIATION_REQUIRED', '此写入已有待核对的操作记录，不能重复执行。')
        with transaction(conn, immediate=True):
            conn.execute(
                '''INSERT INTO note_changes
                   (change_id,note_id,requested_target,file_path,origin,operation_id,
                    before_content,before_hash,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (change_id, note_id, requested_target, before.file_path if before else None,
                 origin, operation_id, before.markdown if before else None, expected, 'pending', _now()),
            )
    finally:
        conn.close()
    try:
        after = await write(expected)
    except BaseException as exc:
        # A returned failure is distinct from an interrupted pending operation.
        # Host timeouts are indeterminate: keep the intent so a retry cannot
        # replay a write that may already have committed.
        if isinstance(exc, ApiError) and exc.code in {'HOST_TIMEOUT', 'HOST_UNAVAILABLE'}:
            raise
        conn = connect_knowledge()
        try:
            with transaction(conn, immediate=True):
                conn.execute("UPDATE note_changes SET status='failed' WHERE change_id=? AND status='pending'", (change_id,))
        finally:
            conn.close()
        raise
    conn = connect_knowledge()
    try:
        with transaction(conn, immediate=True):
            conn.execute(
                '''UPDATE note_changes SET note_id=?,file_path=?,after_content=?,after_hash=?,
                   status='applied',applied_at=? WHERE change_id=? AND status='pending' ''',
                (after.note_id, after.file_path, after.markdown, content_hash(after.markdown),
                 _now(), change_id),
            )
    finally:
        conn.close()
    return after


def list_changes(note_id: str) -> list[dict]:
    conn = connect_knowledge()
    try:
        rows = conn.execute(
            '''SELECT change_id,note_id,requested_target,file_path,origin,before_hash,
                      after_hash,created_at,applied_at FROM note_changes
               WHERE note_id=? AND status='applied' ORDER BY created_at DESC''', (note_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


async def restore_change(change_id: str) -> Note:
    conn = connect_knowledge()
    try:
        row = conn.execute(
            "SELECT * FROM note_changes WHERE change_id=? AND status='applied'", (change_id,)
        ).fetchone()
        change = dict(row) if row else None
    finally:
        conn.close()
    if not change:
        raise ApiError(404, 'CHANGE_NOT_FOUND', '变更记录不存在。')
    if change['before_content'] is None:
        raise ApiError(409, 'CHANGE_RESTORE_UNSUPPORTED', '新建笔记不能作为内容修订直接恢复。')
    note = await note_service.get_note(change['note_id'])
    if (not note or note.file_path != change['file_path']
            or content_hash(note.markdown) != change['after_hash']):
        raise ApiError(409, 'CHANGE_CONTENT_CONFLICT', '笔记在此次写入后已被修改或移动，请先核对当前内容。')

    async def apply(expected: str | None) -> Note:
        if get_settings().environment == 'desktop':
            from app.services import desktop_notes
            await asyncio.to_thread(
                desktop_notes.call, 'write', path=note.file_path,
                expected=expected, content=change['before_content'],
                operation_id=str(uuid4()),
            )
            restored = await note_service.get_note(note.note_id)
            if restored is None:
                raise ApiError(409, 'CHANGE_CONTENT_CONFLICT', '恢复后笔记不可读取。')
            return restored
        from app.knowledge.parser import _extract_frontmatter, _parse_tags
        tags = _parse_tags(_extract_frontmatter(change['before_content']).get('tags'))
        return await note_service.update_note(
            note.note_id, markdown=change['before_content'],
            tags=tags, expected_content_hash=expected, defer_vectors=True,
        )

    return await record_ai_write(
        origin=f"restore:{change_id}", requested_target=note.file_path,
        note_id=note.note_id, write=apply,
    )
