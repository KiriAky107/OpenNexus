"""One owned saved note and original durable transfer on NEW read-only copies."""
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
STAGES = ('seeded', 'associated', 'unknown', 'unknown-read', 'reconciled', 'cold')


def code(value):
    return value is None or isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', value)


def project_recovery(snapshot, working, *, stage):
    require(type(stage) is str and stage in STAGES, 'RELEASE_SYNC_RECOVERY_EXACT_STAGE')
    value = _project_owned(snapshot, working, (NOTE,))
    native = value['native']
    require(len(value['drafts'][NOTE]) == len(value['saves'][NOTE]) == len(value['files']) == len(native['outbox']) == 1,
            'RELEASE_SYNC_RECOVERY_UNIQUE_NOTE_SAVE_OUTBOX')
    draft, = value['drafts'][NOTE]; save, = value['saves'][NOTE]
    file, = value['files']; original, = native['outbox']
    require(all(re.fullmatch(UUID, v) for v in (draft['id'], save['operation_id'], file['id'], original['operation_id']))
            and save['draft_id'] == draft['id'] and save['file_id'] is None and save['expected'] == ''
            and draft['file_id'] == file['id'] == original['file_id']
            and save['operation_id'] == original['operation_id'] and save['state'] == 'committed' and save['error'] is None
            and type(draft['generation']) is int and draft['generation'] >= 1
            and save['generation'] == draft['generation'] == draft['saved_generation']
            and draft['base_hash'] == file['hash'] == original['hash'] == HASH
            and draft['content'] == save['content'] == BODY and original['operation'] == 'put'
            and original['revision'] == file['revision'] and file['deleted'] == 0,
            'RELEASE_SYNC_RECOVERY_ORIGINAL_SAVE_OR_OUTBOX_CHANGED')
    with closing(sqlite3.connect((Path(working).resolve()/DATABASES[0]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(rows(db, 'SELECT id,file_id,path,generation,saved_generation FROM drafts') ==
                [tuple(draft[k] for k in ('id','file_id','path','generation','saved_generation'))]
                and rows(db, 'SELECT operation_id,draft_id,file_id,path,state FROM save_intents') ==
                [tuple(save[k] for k in ('operation_id','draft_id','file_id','path','state'))],
                'RELEASE_SYNC_RECOVERY_HIDDEN_DRAFT_OR_SAVE')
    with closing(sqlite3.connect((Path(working).resolve()/DATABASES[1]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); binding = native['binding']
        for name,(table,columns) in TABLES.items():
            exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?)",(table,)).fetchone()[0]
            if name == 'issuers' and not exists:
                require(not native['attempts'] and not native['issuers'], 'RELEASE_SYNC_RECOVERY_ISSUER_TABLE_MISSING'); continue
            actual = rows(db, 'SELECT binding,'+columns+' FROM '+table+' ORDER BY rowid')
            require(all(binding is not None and row[0] == binding['id'] for row in actual)
                    and [dict(zip(columns.split(','),row[1:])) for row in actual] == native[name],
                    'RELEASE_SYNC_RECOVERY_HIDDEN_TRANSFER_RECORD')
        exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_binding_intents')").fetchone()[0]
        if exists:
            ids = rows(db, 'SELECT operation_id FROM mobile_sync_binding_intents')
            require(ids == ([(native['association']['operation_id'],)] if binding else []),
                    'RELEASE_SYNC_RECOVERY_HIDDEN_ASSOCIATION')
        columns = 'operation_id,file_id,operation,base_revision'
        commits = [dict(zip(columns.split(','),r)) for r in rows(db,'SELECT '+columns+' FROM sync_jobs ORDER BY rowid')]
    require(all(code(v['error']) for name in ('jobs','attempts') for v in native[name])
            and not native['conflicts'], 'RELEASE_SYNC_RECOVERY_RAW_ERROR_OR_CONFLICT')
    if stage in ('seeded','associated'):
        require((native['binding'] is None) == (stage == 'seeded') and original['state'] == 'pending'
                and (stage == 'seeded' or native['binding']['cursor'] == 0)
                and all(not native[k] for k in TABLES) and not commits,
                'RELEASE_SYNC_RECOVERY_FRESH_UNATTEMPTED_ORIGINAL')
    else:
        require(len(native['jobs']) == len(native['attempts']) == len(native['issuers']) == len(commits) == 1,
                'RELEASE_SYNC_RECOVERY_UNIQUE_ORIGINAL_TRANSFER')
        job, = native['jobs']; attempt, = native['attempts']; issuer, = native['issuers']; commit, = commits
        require(native['binding'] is not None and all(v['operation_id'] == original['operation_id'] for v in (job,attempt,issuer,commit))
                and job['file_id'] == commit['file_id'] == original['file_id'] and job['path'] == NOTE
                and job['hash'] == HASH and job['size'] == len(BODY.encode())
                and commit['operation'] == 'put' and commit['base_revision'] == 0
                and attempt['attempts'] == 1 and attempt['outcome'] == 'failed' and bool(attempt['error']),
                'RELEASE_SYNC_RECOVERY_ORIGINAL_FAILED_ATTEMPT_CHANGED')
        unknown = stage in ('unknown','unknown-read')
        require(native['binding']['cursor'] == (0 if unknown else 1)
                and job['state'] == ('committing' if unknown else 'acked')
                and job['remote_revision'] == (None if unknown else 1)
                and original['state'] == ('queued' if unknown else 'acked')
                and (unknown or job['error'] is None), 'RELEASE_SYNC_RECOVERY_ORIGINAL_JOB_STAGE_CHANGED')
        if unknown:
            require(not native['heads'] and not native['inbox'], 'RELEASE_SYNC_RECOVERY_PREMATURE_APPLY')
        else:
            require(len(native['heads']) == len(native['inbox']) == 1, 'RELEASE_SYNC_RECOVERY_UNIQUE_ORIGINAL_APPLY')
            head, = native['heads']; inbox, = native['inbox']; revision = json.loads(inbox['revision'])
            require(head == dict(file_id=file['id'],revision=1,path=NOTE,hash=HASH)
                    and inbox['sequence'] == 1 and inbox['state'] == 'applied'
                    and re.fullmatch(UUID,inbox['operation_id']) and re.fullmatch(UUID,inbox['rename_id'])
                    and set(revision) == {'operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id'}
                    and revision == dict(operation_id=original['operation_id'],file_id=file['id'],base_revision=0,path=NOTE,
                        operation='put',hash=HASH,size=len(BODY.encode()),sequence=1,vault_id=native['binding']['remote_vault']),
                    'RELEASE_SYNC_RECOVERY_APPLIED_ORIGINAL_CHANGED')
    value.update(recovery_stage=stage, original_job_commit=commits, all_clean_draft_ids=[draft['id']], all_Save_ids=[save['operation_id']])
    return value


def preserve_note(before, after):
    require(all(before[k] == after[k] for k in ('files','drafts','saves','all_clean_draft_ids','all_Save_ids'))
            and before['raw_sha256']['vault/'+NOTE] == after['raw_sha256']['vault/'+NOTE],
            'RELEASE_SYNC_RECOVERY_ORIGINAL_NOTE_OR_SAVE_CHANGED')


def same_recovery(before, after):
    require({k:v for k,v in before.items() if k not in ('raw_sha256','recovery_stage')} ==
            {k:v for k,v in after.items() if k not in ('raw_sha256','recovery_stage')},
            'RELEASE_SYNC_RECOVERY_READ_CHANGED_RECORDS')
    preserve_note(before,after)
