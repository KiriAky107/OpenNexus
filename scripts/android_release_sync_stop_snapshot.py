"""One-note original Stop states on new read-only copies; no recovery relaxation."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3

from android_release_sync_snapshot import DATABASES, TABLES, _project_owned, rows
from android_runtime_probe import BODY, require

NOTE = 'release-owned-note.md'
HASH = hashlib.sha256(BODY.encode()).hexdigest()
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
STAGES = ('seeded', 'associated', 'stopped', 'stopped-read', 'reconciled', 'cold')


def canonical(value):
    return type(value) is str and re.fullmatch(UUID, value) is not None


def revision_json(raw):
    require(type(raw) is str and len(raw.encode()) <= 4096, 'RELEASE_SYNC_STOP_REVISION_BOUND')
    def pairs(items):
        value = dict(items); require(len(value) == len(items), 'RELEASE_SYNC_STOP_REVISION_DUPLICATE_KEYS'); return value
    return json.loads(raw, object_pairs_hook=pairs)


def project_stop(snapshot, working, *, stage):
    require(type(stage) is str and stage in STAGES, 'RELEASE_SYNC_STOP_EXACT_STAGE')
    original, copy = Path(snapshot), Path(working)
    require(original.is_dir() and not original.is_symlink() and original.absolute() == original.resolve()
            and not copy.is_symlink() and copy.absolute() == copy.resolve(), 'RELEASE_SYNC_STOP_ORIGINAL_OR_COPY_ALIAS')
    value = _project_owned(original, copy, (NOTE,)); native = value['native']
    require(len(value['drafts'][NOTE]) == len(value['saves'][NOTE]) == len(value['files']) == 1
            and len(native['outbox']) == (1 if stage == 'seeded' else 2), 'RELEASE_SYNC_STOP_UNIQUE_NOTE_SAVE_OUTBOX')
    draft, = value['drafts'][NOTE]; save, = value['saves'][NOTE]; file, = value['files']
    archived, upload = native['outbox'][0], native['outbox'][-1]
    require(all(canonical(v) for v in (draft['id'], save['operation_id'], file['id'], upload['operation_id']))
            and save['draft_id'] == draft['id'] and save['file_id'] is None and save['expected'] == ''
            and draft['file_id'] == file['id'] == upload['file_id'] and save['operation_id'] == archived['operation_id']
            and save['state'] == 'committed' and save['error'] is None
            and all(type(v) is int for v in (draft['generation'], draft['saved_generation'], save['generation'], file['revision'], file['deleted'], upload['revision']))
            and draft['generation'] >= 1 and draft['generation'] == draft['saved_generation'] == save['generation']
            and draft['base_hash'] == file['hash'] == upload['hash'] == HASH and draft['content'] == save['content'] == BODY
            and upload['operation'] == 'put' and upload['revision'] == file['revision'] >= 1 and file['deleted'] == 0,
            'RELEASE_SYNC_STOP_ORIGINAL_SAVE_OR_UPLOAD_CHANGED')
    require(archived == dict(upload, operation_id=save['operation_id'], state='pending' if stage == 'seeded' else 'archived')
            and (stage == 'seeded' or upload['operation_id'] != save['operation_id']), 'RELEASE_SYNC_STOP_INITIAL_OUTBOX_LINEAGE_CHANGED')
    # All-row checks read only approved identifying metadata for rejected hidden
    # documents. Foreign draft/Save bodies and credentials are never queried.
    with closing(sqlite3.connect((copy.resolve()/DATABASES[0]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(rows(db, 'SELECT id,file_id,path,generation,saved_generation FROM drafts') ==
                [tuple(draft[k] for k in ('id','file_id','path','generation','saved_generation'))]
                and rows(db, 'SELECT operation_id,draft_id,file_id,path,state FROM save_intents') ==
                [tuple(save[k] for k in ('operation_id','draft_id','file_id','path','state'))], 'RELEASE_SYNC_STOP_HIDDEN_DRAFT_OR_SAVE')
    with closing(sqlite3.connect((copy.resolve()/DATABASES[1]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); binding = native['binding']
        for name, (table, columns) in TABLES.items():
            exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?)", (table,)).fetchone()[0]
            if name == 'issuers' and not exists:
                require(not native['attempts'] and not native['issuers'], 'RELEASE_SYNC_STOP_ISSUER_TABLE_MISSING'); continue
            actual = rows(db, 'SELECT binding,'+columns+' FROM '+table+' ORDER BY rowid')
            require(all(binding is not None and row[0] == binding['id'] for row in actual)
                    and [dict(zip(columns.split(','), row[1:])) for row in actual] == native[name], 'RELEASE_SYNC_STOP_HIDDEN_TRANSFER_RECORD')
        exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_binding_intents')").fetchone()[0]
        if exists:
            require(rows(db, 'SELECT operation_id FROM mobile_sync_binding_intents') ==
                    ([(native['association']['operation_id'],)] if binding else []), 'RELEASE_SYNC_STOP_HIDDEN_ASSOCIATION')
        columns = 'binding,operation_id,file_id,operation,base_revision,upload_id'
        commits = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT '+columns+' FROM sync_jobs ORDER BY rowid')]
    require(not native['conflicts'] and all(row['error'] is None for name in ('jobs','attempts') for row in native[name]),
            'RELEASE_SYNC_STOP_UNEXPECTED_ERROR_OR_CONFLICT')
    if stage in ('seeded', 'associated'):
        require((native['binding'] is None) == (stage == 'seeded') and upload['state'] == 'pending'
                and (stage == 'seeded' or native['binding']['cursor'] == 0)
                and all(not native[k] for k in TABLES) and not commits, 'RELEASE_SYNC_STOP_FRESH_UNATTEMPTED_ORIGINAL')
    else:
        require(len(native['jobs']) == len(native['attempts']) == len(native['issuers']) == len(commits) == 1,
                'RELEASE_SYNC_STOP_UNIQUE_ORIGINAL_TRANSFER')
        job, = native['jobs']; attempt, = native['attempts']; issuer, = native['issuers']; commit, = commits
        require(binding is not None and all(row['operation_id'] == upload['operation_id'] for row in (job, attempt, issuer, commit))
                and commit['binding'] == binding['id'] and job['file_id'] == commit['file_id'] == upload['file_id']
                and job['path'] == NOTE and job['hash'] == HASH and type(job['size']) is int and job['size'] == len(BODY.encode())
                and commit['operation'] == 'put' and type(commit['base_revision']) is int and commit['base_revision'] == 0
                and (commit['upload_id'] is None or type(commit['upload_id']) is str and re.fullmatch(r'[A-Za-z0-9-]{1,80}', commit['upload_id']))
                and type(attempt['attempts']) is int and attempt['attempts'] == 1 and attempt['outcome'] == 'interrupted'
                and type(issuer['device_id']) is str and re.fullmatch(r'[0-9a-f]{32}', issuer['device_id']),
                'RELEASE_SYNC_STOP_ORIGINAL_INTERRUPTED_ATTEMPT_CHANGED')
        stopped = stage in ('stopped', 'stopped-read')
        require(type(binding['cursor']) is int and binding['cursor'] == (0 if stopped else 1)
                and job['state'] == ('committing' if stopped else 'acked') and job['remote_revision'] == (None if stopped else 1)
                and (stopped or type(job['remote_revision']) is int) and upload['state'] == ('queued' if stopped else 'acked'),
                'RELEASE_SYNC_STOP_ORIGINAL_JOB_STAGE_CHANGED')
        if stopped:
            require(not native['heads'] and not native['inbox'], 'RELEASE_SYNC_STOP_PREMATURE_APPLY')
        else:
            require(len(native['heads']) == len(native['inbox']) == 1, 'RELEASE_SYNC_STOP_UNIQUE_ORIGINAL_APPLY')
            head, = native['heads']; inbox, = native['inbox']; revision = revision_json(inbox['revision'])
            require(head == dict(file_id=file['id'], revision=1, path=NOTE, hash=HASH)
                    and type(head['revision']) is int and type(inbox['sequence']) is int and inbox['sequence'] == 1 and inbox['state'] == 'applied'
                    and canonical(inbox['operation_id']) and canonical(inbox['rename_id'])
                    and type(revision) is dict and set(revision) == {'operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id'}
                    and all(type(revision[k]) is int for k in ('base_revision','size','sequence'))
                    and revision == dict(operation_id=upload['operation_id'], file_id=file['id'], base_revision=0, path=NOTE,
                        operation='put', hash=HASH, size=len(BODY.encode()), sequence=1, vault_id=binding['remote_vault']),
                    'RELEASE_SYNC_STOP_APPLIED_ORIGINAL_CHANGED')
    value.update(stop_stage=stage, original_job_commit=commits, all_clean_draft_ids=[draft['id']], all_Save_ids=[save['operation_id']],
        original_Save_outbox_id=archived['operation_id'], original_association_upload_id=None if stage == 'seeded' else upload['operation_id'])
    return value


def preserve_note(before, after):
    require(all(before[k] == after[k] for k in ('files','drafts','saves','all_clean_draft_ids','all_Save_ids'))
            and before['raw_sha256']['vault/'+NOTE] == after['raw_sha256']['vault/'+NOTE], 'RELEASE_SYNC_STOP_ORIGINAL_NOTE_OR_SAVE_CHANGED')


def preserve_initial_outbox(seeded, associated):
    preserve_note(seeded, associated)
    saved, = seeded['native']['outbox']; archived, upload = associated['native']['outbox']
    require(seeded['stop_stage'] == 'seeded' and associated['stop_stage'] == 'associated'
            and saved['operation_id'] == seeded['all_Save_ids'][0] == associated['all_Save_ids'][0]
            and saved['state'] == 'pending' and archived == dict(saved, state='archived')
            and upload == dict(saved, operation_id=associated['original_association_upload_id']) and upload['operation_id'] != saved['operation_id'],
            'RELEASE_SYNC_STOP_INITIAL_OUTBOX_PUBLICATION_CHANGED')


def preserve_read(before, after):
    require((before['stop_stage'], after['stop_stage']) in (('stopped','stopped-read'), ('reconciled','cold')),
            'RELEASE_SYNC_STOP_EXACT_READ_PAIR')
    require({k:v for k,v in before.items() if k not in ('raw_sha256','stop_stage')} ==
            {k:v for k,v in after.items() if k not in ('raw_sha256','stop_stage')}, 'RELEASE_SYNC_STOP_READ_CHANGED_RECORDS')
    preserve_note(before, after)
