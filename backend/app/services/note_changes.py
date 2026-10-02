"""Vault-scoped content change receipts for AI writes and revision-checked restores."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.contracts import Note
from app.config import get_settings
from app import host_bridge
from app.database.db import connect_knowledge, transaction, _connect_path
from app.errors import ApiError
from app.services import note_service

# Core retains before/planned/committed content with the history record. Host
# operation receipts contain identity/path/hashes only; reconciliation does not
# depend on sync payload retention and never replays a Host write.
_write_intent: ContextVar[dict | None] = ContextVar('note_change_write_intent', default=None)


def prepare_write_intent(document: dict, *, expected: str, operation_id: str, planned_metadata: dict | None = None) -> None:
    """Persist the actual, metadata-expanded document immediately before dispatch."""
    active = _write_intent.get()
    if active is None:
        return
    if active['vault_id'] != host_bridge.vault_id.get() or active['operation_id'] != operation_id:
        raise ApiError(409, 'CHANGE_SCOPE_CONFLICT', '写入操作或知识库已变化。')
    from app.services.desktop_notes import note_from_document
    from app.services.note_preview import snapshot
    planned = note_from_document(document)
    after = planned_metadata or snapshot(planned)
    reviewed = active['reviewed']
    if reviewed and ('after_hash' in reviewed['binding']) and (
            reviewed['binding']['after_hash'] != after['content_hash']
            or reviewed['binding']['path'] != document['path']):
        raise ApiError(409, 'NOTE_PREVIEW_STALE', '实际待写内容与预览不同，请重新确认。')
    intent = {**{key: document[key] for key in ['path', 'file_id', 'created_at', 'updated_at']},
              'operation_id': operation_id, 'expected': expected, 'after_hash': after['content_hash']}
    conn = _connect_path(active['database'])
    try:
        with transaction(conn, immediate=True):
            result = conn.execute(
                '''UPDATE note_changes SET file_path=?,after_content=?,after_hash=?,after_metadata=?,
                   intent_version=1,intent_json=? WHERE change_id=? AND status='pending' AND intent_version=0''',
                (document['path'], document['content'], after['content_hash'], json.dumps(after, ensure_ascii=False),
                 json.dumps(intent, ensure_ascii=False), active['change_id']),
            )
            if result.rowcount != 1:
                raise ApiError(409, 'CHANGE_RECONCILIATION_REQUIRED', '此写入已有核对证据，不能重复执行。')
    finally:
        conn.close()
    active['dispatched'] = True


def prepare_web_write_intent(parsed, markdown: str, expected: str) -> None:
    if _write_intent.get() is None:
        return
    document = {'path': parsed.file_path, 'file_id': parsed.note_id, 'content': markdown,
                'created_at': parsed.created_at.timestamp(), 'updated_at': parsed.updated_at.timestamp()}
    prepare_write_intent(document, expected=expected, operation_id=host_bridge.operation_id.get(),
                         planned_metadata={'note_id': parsed.note_id, 'file_path': parsed.file_path,
                                           'content_hash': content_hash(markdown), 'title': parsed.title, 'tags': parsed.tags})


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
    database = Path(conn.execute('PRAGMA database_list').fetchone()['file'])
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
    operation_token = host_bridge.operation_id.set(operation_id)
    active = {'change_id': change_id, 'operation_id': operation_id, 'database': database,
              'vault_id': host_bridge.vault_id.get(), 'reviewed': reviewed, 'dispatched': False}
    intent_token = _write_intent.set(active)
    try:
        after = await write(expected)
    except BaseException as exc:
        # A returned failure is distinct from an interrupted pending operation.
        # Host timeouts are indeterminate: keep the intent so a retry cannot
        # replay a write that may already have committed.
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, ApiError) and exc.code in {'HOST_TIMEOUT', 'HOST_UNAVAILABLE', 'CHANGE_RECONCILIATION_REQUIRED'}:
            raise ApiError(exc.status_code, exc.code, exc.message + ' 写入结果待核对，可从底栏“写入核对”检查，勿重复执行。',
                           {**exc.details, 'change_id': change_id}) from None
        # A filesystem/DB failure after dispatch can follow an atomic replace.
        # Only an explicit pre-commit rejection is proof of no commit.
        rejected = isinstance(exc, ApiError) and exc.code in {
            'REVISION_CONFLICT', 'NOTE_CONTENT_CONFLICT', 'NOTE_PREVIEW_STALE',
            'PERMISSION_DENIED', 'WORKSPACE_REQUEST_INVALID', 'OPERATION_ID_INVALID',
            'INVALID_FRONTMATTER', 'CHANGE_SCOPE_CONFLICT',
        }
        if not rejected:
            if active['dispatched'] and isinstance(exc, ApiError):
                raise ApiError(exc.status_code, exc.code, exc.message + ' 写入结果待核对，请从底栏“写入核对”检查。',
                               {**exc.details, 'change_id': change_id}) from None
            raise
        conn = _connect_path(database)
        try:
            with transaction(conn, immediate=True):
                conn.execute("UPDATE note_changes SET status='failed',failure_code=? WHERE change_id=? AND status='pending'", (getattr(exc, 'code', type(exc).__name__), change_id))
        finally:
            conn.close()
        raise
    finally:
        expected_write_state.reset(state_token)
        host_bridge.operation_id.reset(operation_token)
        _write_intent.reset(intent_token)
    conn = None
    try:
        conn = _connect_path(database)
        with transaction(conn, immediate=True):
            conn.execute(
                '''UPDATE note_changes SET note_id=?,file_path=?,after_content=?,after_hash=?,
                   status='applied',applied_at=?,after_metadata=? WHERE change_id=? AND status='pending' ''',
                (after.note_id, after.file_path, after.markdown, content_hash(after.markdown),
                 _now(), json.dumps(snapshot(after), ensure_ascii=False), change_id),
            )
    except Exception:
        raise ApiError(503, 'CHANGE_RECONCILIATION_REQUIRED', '提交结果已返回，但历史保存未完成，请从底栏“写入核对”检查。',
                       {'change_id': change_id}) from None
    finally:
        if conn is not None:
            conn.close()
    return after


def list_changes(note_id: str | None, *, limit: int = 100, offset: int = 0, include_pending: bool = False) -> list[dict]:
    where = "status='pending'" if note_id is None else "note_id=?" + ("" if include_pending else " AND status='applied'")
    parameters = () if note_id is None else (note_id,)
    conn = connect_knowledge()
    try:
        rows = conn.execute(
            f'''SELECT change_id,note_id,requested_target,file_path,origin,before_hash,
                      after_hash,created_at,applied_at,status,failure_code FROM note_changes
               WHERE {where}
               ORDER BY created_at DESC,change_id DESC LIMIT ? OFFSET ?''',
            (*parameters, limit, offset)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _get_change(change_id: str, note_id: str | None = None, *, applied_only: bool = True) -> dict:
    conn = connect_knowledge()
    try:
        row = conn.execute(
            "SELECT * FROM note_changes WHERE change_id=? AND (?=0 OR status='applied')", (change_id, int(applied_only))
        ).fetchone()
        change = dict(row) if row else None
    finally:
        conn.close()
    if not change or (note_id is not None and change['note_id'] != note_id):
        raise ApiError(404, 'CHANGE_NOT_FOUND', '变更记录不存在。')
    return change


async def change_detail(note_id: str | None, change_id: str) -> dict:
    from app.services.note_preview import content_diff, snapshot
    change = _get_change(change_id, note_id, applied_only=False)
    note = await note_service.get_note(change['note_id']) if change['status'] == 'applied' and change['note_id'] else None
    after_meta = json.loads(change['after_metadata']) if change['after_metadata'] else None
    can_restore = (change['status'] == 'applied' and change['before_content'] is not None and note is not None
                   and note.file_path == change['file_path'] and content_hash(note.markdown) == change['after_hash']
                   and (not after_meta or after_meta == snapshot(note)))
    return {**{key: value for key, value in change.items() if key not in {'before_content', 'after_content', 'intent_json'}},
            'diff': content_diff(change['before_content'] or '', change['after_content']) if change['after_content'] is not None else None,
            'can_restore': can_restore,
            'restore_reason': '' if can_restore else 'unconfirmed' if change['status'] != 'applied' else 'created' if change['before_content'] is None else 'changed'}


async def reconcile_change(change_id: str) -> dict:
    """Inspect durable receipts only. Absence/conflict is not proof of no commit."""
    change = _get_change(change_id, applied_only=False)
    reason = ''
    if change['status'] == 'pending':
        if change['intent_version'] != 1 or not change['intent_json'] or change['after_content'] is None:
            reason = 'legacy_evidence_missing'
        elif get_settings().environment != 'desktop':
            reason = 'host_receipt_unavailable'
        else:
            from app.services.desktop_notes import call, note_from_document
            try:
                receipt = await asyncio.to_thread(call, 'operation', operation_id=change['operation_id'])
            except ApiError as exc:
                if exc.code not in {'OPERATION_NOT_FOUND', 'HOST_UNAVAILABLE', 'HOST_TIMEOUT'}:
                    raise
                receipt = None
                reason = 'receipt_missing' if exc.code == 'OPERATION_NOT_FOUND' else 'host_receipt_unavailable'
            if receipt is not None:
                intent = json.loads(change['intent_json'])
                result = receipt.get('result') or {}
                expected_id = None if intent['file_id'] == 'pending' else intent['file_id']
                matches = (receipt.get('operation_id') == change['operation_id'] == intent['operation_id']
                           and result.get('path') == change['file_path'] == intent['path']
                           and result.get('hash') == change['after_hash'] == intent['after_hash'] == content_hash(change['after_content'])
                           and result.get('expected') == intent['expected'] == (change['before_hash'] or '')
                           and bool(result.get('file_id')) and (expected_id is None or result['file_id'] == expected_id)
                           and not result.get('deleted') and not result.get('is_folder'))
                if receipt.get('state') == 'committed' and matches:
                    document = {key: intent[key] for key in ['path', 'file_id', 'created_at', 'updated_at']}
                    document.update(file_id=result['file_id'], content=change['after_content'])
                    after = note_from_document(document)
                    from app.services.note_preview import snapshot
                    conn = connect_knowledge()
                    try:
                        with transaction(conn, immediate=True):
                            conn.execute('''UPDATE note_changes SET note_id=?,after_metadata=?,status='applied',applied_at=?
                                            WHERE change_id=? AND status='pending' AND intent_json=?''',
                                         (after.note_id, json.dumps(snapshot(after), ensure_ascii=False), _now(), change_id, change['intent_json']))
                    finally:
                        conn.close()
                else:
                    reason = 'receipt_mismatch' if receipt.get('state') == 'committed' else 'host_result_uncertain'
    current = _get_change(change_id, applied_only=False)
    return {**await change_detail(current['note_id'], change_id), 'reconciliation_reason': reason if current['status'] == 'pending' else ''}


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
                operation_id=host_bridge.operation_id.get() or str(uuid4()),
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
