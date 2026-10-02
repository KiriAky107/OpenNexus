"""Fault-injected Host responses; real disk contents are never treated as receipts."""
import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest

from app import host_bridge
from app.agent.builtin_tools import update_note, create_note, NoteUpdateArguments, NoteCreateArguments
from app.agent.tools import ToolExecutionContext
from app.config import get_settings
from app.database.db import connect_knowledge
from app.database.migrations import MIGRATIONS, migrate
from app.errors import ApiError
from app.services import desktop_notes, note_changes


class ReceiptHost:
    def __init__(self, directory):
        self.directory = directory
        self.document = {'file_id': 'stable', 'path': 'A.md', 'content': '---\ntags: [keep]\n---\nbefore', 'created_at': 0, 'updated_at': 1}
        self.writes = 0
        self.failure = 'HOST_TIMEOUT'

    def receipt_path(self, operation):
        return self.directory / (operation + '.json')

    def call(self, method, **params):
        if method == 'read':
            if not self.document or params['file_id'] != self.document['file_id']:
                raise ApiError(404, 'FILE_NOT_FOUND', 'deleted')
            return {**self.document, 'hash': note_changes.content_hash(self.document['content'])}
        if method == 'operation':
            path = self.receipt_path(params['operation_id'])
            if not path.exists():
                raise ApiError(404, 'OPERATION_NOT_FOUND', 'missing')
            return json.loads(path.read_text(encoding='utf-8'))
        assert method == 'write', 'Reconciliation must never recover or replay'
        conn = connect_knowledge()
        try:
            pending = dict(conn.execute('SELECT * FROM note_changes WHERE operation_id=?', (params['operation_id'],)).fetchone())
        finally:
            conn.close()
        assert pending['status'] == 'pending' and pending['intent_version'] == 1
        assert pending['after_content'] == params['content']
        assert pending['after_hash'] == note_changes.content_hash(params['content'])
        assert json.loads(pending['intent_json'])['expected'] == params['expected']
        if self.failure == 'REVISION_CONFLICT':
            raise ApiError(409, 'REVISION_CONFLICT', 'rejected before commit')
        self.writes += 1
        identity = self.document['file_id'] if self.document and params['expected'] else 'created'
        self.document = {'file_id': identity, 'path': params['path'], 'content': params['content'], 'created_at': 0, 'updated_at': 2}
        receipt = {'operation_id': params['operation_id'], 'state': 'committed', 'result': {
            'file_id': identity, 'path': params['path'], 'hash': note_changes.content_hash(params['content']),
            'expected': params['expected'], 'deleted': False, 'is_folder': False}}
        # Only identity and digests live in the Host receipt, never Markdown.
        self.receipt_path(params['operation_id']).write_text(json.dumps(receipt), encoding='utf-8')
        if self.failure == 'cancel':
            raise asyncio.CancelledError()
        if self.failure:
            raise ApiError(503, self.failure, 'response unavailable after replace')
        return receipt


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop')
    get_settings.cache_clear()
    token = host_bridge.vault_id.set(str(uuid4()))
    fake = ReceiptHost(tmp_path)
    monkeypatch.setattr(desktop_notes, 'call', fake.call)
    yield fake
    host_bridge.vault_id.reset(token)


async def interrupted_update(host):
    with pytest.raises((ApiError, asyncio.CancelledError)):
        await update_note(NoteUpdateArguments(note_id='stable', title='Actual title', markdown='AI output'), ToolExecutionContext('run'))
    rows = note_changes.list_changes('stable', include_pending=True)
    assert len(rows) == 1
    return note_changes._get_change(rows[0]['change_id'], applied_only=False)


@pytest.mark.parametrize('failure', ['HOST_TIMEOUT', 'HOST_UNAVAILABLE', 'DATABASE_ERROR', 'cancel'])
def test_committed_but_interrupted_write_reconciles_once_after_restart_and_human_edit(host, failure):
    host.failure = failure
    change = asyncio.run(interrupted_update(host))
    original = host.document['content']
    assert 'title: Actual title' in original and 'keep' in original
    assert change['status'] == 'pending' and change['after_content'] == original
    assert note_changes.list_changes('stable') == []
    # Reopen Core DB and read durable Host receipts from disk, after the file changed.
    get_settings.cache_clear()
    host.document['content'] = 'later manual edit'
    async def audit():
        first, second = await asyncio.gather(*(note_changes.reconcile_change(change['change_id']) for _ in range(2)))
        assert first['status'] == second['status'] == 'applied'
        assert not first['can_restore']
        assert original in note_changes._get_change(change['change_id'])['after_content']
        with pytest.raises(ApiError) as conflict:
            await note_changes.restore_change(change['change_id'])
        assert conflict.value.code == 'CHANGE_CONTENT_CONFLICT'
    asyncio.run(audit())
    assert host.writes == 1 and host.document['content'] == 'later manual edit'
    assert len(note_changes.list_changes('stable')) == 1
    assert note_changes._write_intent.get() is None


@pytest.mark.parametrize('after', ['moved', 'deleted'])
def test_receipt_preserves_original_commit_after_later_move_or_delete(host, after):
    change = asyncio.run(interrupted_update(host))
    if after == 'moved':
        host.document['path'] = 'Moved.md'
    else:
        host.document = None
    detail = asyncio.run(note_changes.reconcile_change(change['change_id']))
    assert detail['status'] == 'applied' and detail['file_path'] == 'A.md'
    assert not detail['can_restore'] and host.writes == 1


@pytest.mark.parametrize('field,value', [('path', 'other.md'), ('file_id', 'other'), ('hash', 'wrong'), ('expected', 'wrong'), ('deleted', True), ('is_folder', True), ('operation_id', 'other')])
def test_mismatched_receipt_cannot_claim_a_commit(host, field, value):
    change = asyncio.run(interrupted_update(host))
    path = host.receipt_path(change['operation_id'])
    receipt = json.loads(path.read_text(encoding='utf-8'))
    (receipt if field == 'operation_id' else receipt['result'])[field] = value
    path.write_text(json.dumps(receipt), encoding='utf-8')
    detail = asyncio.run(note_changes.reconcile_change(change['change_id']))
    assert detail['status'] == 'pending' and detail['reconciliation_reason'] == 'receipt_mismatch'
    assert host.writes == 1 and note_changes.list_changes('stable') == []


@pytest.mark.parametrize('state', [None, 'pending', 'conflict'])
def test_absent_or_uncertain_receipt_does_not_mean_not_committed(host, state):
    change = asyncio.run(interrupted_update(host))
    path = host.receipt_path(change['operation_id'])
    if state is None:
        path.unlink()
    else:
        receipt = json.loads(path.read_text(encoding='utf-8'))
        receipt['state'] = state
        path.write_text(json.dumps(receipt), encoding='utf-8')
    detail = asyncio.run(note_changes.reconcile_change(change['change_id']))
    assert detail['status'] == 'pending'
    assert detail['reconciliation_reason'] == ('receipt_missing' if state is None else 'host_result_uncertain')
    assert host.writes == 1


def test_duplicate_interrupted_operation_and_other_vault_never_replay(host):
    operation = host_bridge.operation_id.set(str(uuid4()))
    try:
        change = asyncio.run(interrupted_update(host))
        with pytest.raises(ApiError) as blocked:
            asyncio.run(update_note(NoteUpdateArguments(note_id='stable', markdown='AI output'), ToolExecutionContext('run')))
        assert blocked.value.code == 'CHANGE_RECONCILIATION_REQUIRED'
        other = host_bridge.vault_id.set(str(uuid4()))
        try:
            assert note_changes.list_changes(None, include_pending=True) == []
            with pytest.raises(ApiError) as missing:
                asyncio.run(note_changes.reconcile_change(change['change_id']))
            assert missing.value.code == 'CHANGE_NOT_FOUND'
        finally:
            host_bridge.vault_id.reset(other)
        assert asyncio.run(note_changes.reconcile_change(change['change_id']))['status'] == 'applied'
    finally:
        host_bridge.operation_id.reset(operation)
    assert host.writes == 1


def test_creation_is_discoverable_without_a_note_id_and_preserves_actual_frontmatter(host):
    host.document = None
    with pytest.raises(ApiError):
        asyncio.run(create_note(NoteCreateArguments(title='中文', folder='课程', markdown='AI output', tags=['tag']), ToolExecutionContext('run')))
    pending = note_changes.list_changes(None, include_pending=True)
    assert len(pending) == 1 and pending[0]['note_id'] is None
    assert pending[0]['file_path'] == '课程/中文.md'
    detail = asyncio.run(note_changes.reconcile_change(pending[0]['change_id']))
    assert detail['note_id'] == 'created' and detail['status'] == 'applied'
    assert detail['restore_reason'] == 'created' and not detail['can_restore']
    assert 'title: 中文' in note_changes._get_change(detail['change_id'])['after_content']
    assert host.writes == 1


def test_explicit_pre_commit_rejection_is_visible_as_not_committed(host):
    host.failure = 'REVISION_CONFLICT'
    change = asyncio.run(interrupted_update(host))
    detail = asyncio.run(note_changes.reconcile_change(change['change_id']))
    assert detail['status'] == 'failed' and detail['failure_code'] == 'REVISION_CONFLICT'
    assert host.writes == 0 and not detail['can_restore']


def test_old_pending_and_failed_records_migrate_without_inventing_evidence():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(MIGRATIONS[17] + MIGRATIONS[18])
    conn.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
    conn.executemany('INSERT INTO schema_migrations VALUES (?,?)', [(version, 'old') for version in range(1, 22)])
    for status in ['pending', 'failed']:
        conn.execute('''INSERT INTO note_changes(change_id,requested_target,origin,operation_id,status,created_at)
                        VALUES (?,?,?,?,?,?)''', (status, 'A.md', 'old', status, status, 'old'))
    conn.commit()
    migrate(conn)
    assert [(row['status'], row['intent_version'], row['after_content']) for row in conn.execute('SELECT * FROM note_changes')] == [('pending', 0, None), ('pending', 0, None)]
    conn.close()


def test_legacy_pending_stays_uncertain_even_when_disk_matches(host):
    conn = connect_knowledge()
    try:
        conn.execute('''INSERT INTO note_changes(change_id,note_id,requested_target,origin,operation_id,status,created_at)
                        VALUES ('old','stable','A.md','old','old','pending','old')''')
    finally:
        conn.close()
    detail = asyncio.run(note_changes.reconcile_change('old'))
    assert detail['status'] == 'pending' and detail['diff'] is None
    assert detail['reconciliation_reason'] == 'legacy_evidence_missing' and host.writes == 0


def test_http_reconciliation_is_audit_only_under_a_denied_write_policy():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.container import container
    from app.agent.permissions import PermissionMode
    conn = connect_knowledge()
    try:
        conn.execute('''INSERT INTO note_changes(change_id,requested_target,origin,operation_id,status,created_at)
                        VALUES ('old','A.md','old','old','pending','old')''')
    finally:
        conn.close()
    with TestClient(app) as client:
        policy = container.permissions.policy
        previous = policy.mode_for('notes.write')
        try:
            policy.set_rule('notes.write', PermissionMode.deny)
            assert client.get('/api/note-changes').json()['items'][0]['change_id'] == 'old'
            response = client.post('/api/note-changes/old/reconcile', json={})
            assert response.status_code == 200, response.text
            assert response.json()['status'] == 'pending'
            assert response.json()['reconciliation_reason'] == 'legacy_evidence_missing'
            assert client.post('/api/notes/no-note/changes/old/restore', json={'expected_content_hash': '0' * 64, 'confirm': True}).status_code == 403
        finally:
            policy.set_rule('notes.write', previous)


def test_core_history_save_failure_after_host_response_can_be_reconciled(host, monkeypatch):
    host.failure = None
    original = note_changes._connect_path
    count = 0
    def fail_final_history_update(path):
        nonlocal count
        count += 1
        if count == 2:
            raise sqlite3.OperationalError('simulated DB unavailable after Host commit')
        return original(path)
    monkeypatch.setattr(note_changes, '_connect_path', fail_final_history_update)
    with pytest.raises(ApiError) as uncertain:
        asyncio.run(update_note(NoteUpdateArguments(note_id='stable', markdown='AI output'), ToolExecutionContext('run')))
    assert uncertain.value.code == 'CHANGE_RECONCILIATION_REQUIRED'
    monkeypatch.setattr(note_changes, '_connect_path', original)
    pending = note_changes.list_changes(None, include_pending=True)
    assert len(pending) == 1
    assert asyncio.run(note_changes.reconcile_change(pending[0]['change_id']))['status'] == 'applied'
    assert host.writes == 1


def test_actual_metadata_expansion_must_match_reviewed_hash_before_dispatch(host, monkeypatch):
    from app.contracts import ToolCall
    from app.services.note_preview import preview_write
    args = NoteUpdateArguments(note_id='stable', title='Title', markdown='AI output')
    preview = asyncio.run(preview_write(ToolCall(tool_call_id='call', name='notes.update', arguments=args.model_dump())))
    original = desktop_notes.metadata
    monkeypatch.setattr(desktop_notes, 'metadata', lambda *values: original(*values) + '\nunreviewed change')
    with pytest.raises(ApiError) as stale:
        asyncio.run(update_note(args, ToolExecutionContext('run', reviewed_write=preview)))
    assert stale.value.code == 'NOTE_PREVIEW_STALE'
    assert host.writes == 0 and note_changes.list_changes('stable') == []
