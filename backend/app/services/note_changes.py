"""Vault-scoped content change receipts for AI writes and revision-checked restores."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    reviewed: dict | None = None,
) -> Note:
    """Persist intent before the write; only a confirmed result becomes history.

    An interrupted operation remains pending rather than claiming a successful write.
    All updates pass the observed hash into the underlying Host/web compare-and-swap.
    """
    before = await note_service.get_note(note_id) if note_id else None
    if note_id and before is None:
        raise ApiError(404, 'RESOURCE_NOT_FOUND', 'note not found')
    expected = content_hash(before.markdown) if before else None
    from app.services.note_preview import snapshot
    if reviewed and reviewed['binding']['before'] != snapshot(before):
        raise ApiError(409, 'NOTE_PREVIEW_STALE', '笔记在确认后已变化，请重新预览。')
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
                    before_content,before_hash,status,created_at,before_metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (change_id, note_id, requested_target, before.file_path if before else None,
                 origin, operation_id, before.markdown if before else None, expected, 'pending', _now(),
                 json.dumps(snapshot(before), ensure_ascii=False)),
            )
    finally:
        conn.close()
    from app.services.note_preview import expected_write_state
    state_token = expected_write_state.set(snapshot(before))
    try:
        after = await write(expected)
    except BaseException as exc:
        # A returned failure is distinct from an interrupted pending operation.
        # Host timeouts are indeterminate: keep the intent so a retry cannot
        # replay a write that may already have committed.
        if isinstance(exc, asyncio.CancelledError) or (isinstance(exc, ApiError) and exc.code in {'HOST_TIMEOUT', 'HOST_UNAVAILABLE', 'CHANGE_RECONCILIATION_REQUIRED'}):
            raise
        conn = connect_knowledge()
        try:
            with transaction(conn, immediate=True):
                conn.execute("UPDATE note_changes SET status='failed' WHERE change_id=? AND status='pending'", (change_id,))
        finally:
            conn.close()
        raise
    finally:
        expected_write_state.reset(state_token)
    conn = connect_knowledge()
    try:
        with transaction(conn, immediate=True):
            conn.execute(
                '''UPDATE note_changes SET note_id=?,file_path=?,after_content=?,after_hash=?,
                   status='applied',applied_at=?,after_metadata=? WHERE change_id=? AND status='pending' ''',
                (after.note_id, after.file_path, after.markdown, content_hash(after.markdown),
                 _now(), json.dumps(snapshot(after), ensure_ascii=False), change_id),
            )
    finally:
        conn.close()
    return after


def list_changes(note_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
    conn = connect_knowledge()
    try:
        rows = conn.execute(
            '''SELECT change_id,note_id,requested_target,file_path,origin,before_hash,
                      after_hash,created_at,applied_at FROM note_changes
               WHERE note_id=? AND status='applied' ORDER BY created_at DESC,change_id DESC LIMIT ? OFFSET ?''', (note_id, limit, offset)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _get_change(change_id: str, note_id: str | None = None) -> dict:
    conn = connect_knowledge()
    try:
        row = conn.execute(
            "SELECT * FROM note_changes WHERE change_id=? AND status='applied'", (change_id,)
        ).fetchone()
        change = dict(row) if row else None
    finally:
        conn.close()
    if not change or (note_id is not None and change['note_id'] != note_id):
        raise ApiError(404, 'CHANGE_NOT_FOUND', '变更记录不存在。')
    return change


async def change_detail(note_id: str, change_id: str) -> dict:
    from app.services.note_preview import content_diff, snapshot
    change = _get_change(change_id, note_id)
    note = await note_service.get_note(note_id)
    after_meta = json.loads(change['after_metadata']) if change['after_metadata'] else None
    can_restore = (change['before_content'] is not None and note is not None
                   and note.file_path == change['file_path'] and content_hash(note.markdown) == change['after_hash']
                   and (not after_meta or after_meta == snapshot(note)))
    return {**{key: value for key, value in change.items() if key not in {'before_content', 'after_content'}},
            'diff': content_diff(change['before_content'] or '', change['after_content']),
            'can_restore': can_restore,
            'restore_reason': '' if can_restore else 'created' if change['before_content'] is None else 'changed'}


async def restore_change(change_id: str, *, note_id: str | None = None, expected_hash: str | None = None) -> Note:
    from app.services.note_preview import snapshot
    change = _get_change(change_id, note_id)
    if expected_hash is not None and expected_hash != change['after_hash']:
        raise ApiError(409, 'CHANGE_CONTENT_CONFLICT', '恢复确认的修订不匹配。')
    if change['before_content'] is None:
        raise ApiError(409, 'CHANGE_RESTORE_UNSUPPORTED', '新建笔记不能作为内容修订直接恢复。')
    note = await note_service.get_note(change['note_id'])
    after_meta = json.loads(change['after_metadata']) if change['after_metadata'] else None
    if (not note or note.file_path != change['file_path']
            or content_hash(note.markdown) != change['after_hash']
            or (after_meta and after_meta != snapshot(note))):
        raise ApiError(409, 'CHANGE_CONTENT_CONFLICT', '笔记在此次写入后已被修改或移动，请先核对当前内容。')

    async def apply(expected: str | None) -> Note:
        if expected != change['after_hash']:
            raise ApiError(409, 'CHANGE_CONTENT_CONFLICT', '恢复确认后笔记已变化。')
        if get_settings().environment == 'desktop':
            from app.services import desktop_notes
            return await desktop_notes.write_content(
                path=note.file_path,
                expected=expected, content=change['before_content'],
                operation_id=str(uuid4()),
                previous={'file_id': note.note_id, 'created_at': note.created_at.timestamp()},
            )
        from app.knowledge.parser import _extract_frontmatter, _parse_tags
        before_meta = json.loads(change['before_metadata']) if change['before_metadata'] else None
        tags = before_meta['tags'] if before_meta else _parse_tags(_extract_frontmatter(change['before_content']).get('tags'))
        title_args = {'title': before_meta['title']} if before_meta and before_meta['title'] else {}
        return await note_service.update_note(
            note.note_id, markdown=change['before_content'],
            tags=tags, expected_content_hash=expected, defer_vectors=True, **title_args,
        )

    return await record_ai_write(
        origin=f"restore:{change_id}", requested_target=note.file_path,
        note_id=note.note_id, write=apply, reviewed={'binding': {'before': snapshot(note)}},
    )
