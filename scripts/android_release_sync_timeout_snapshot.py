"""One-note network-failed states on new read-only copies; no timeout-cause inference.

The dedicated caller must separately correlate the held-response clock chain.
Stop/process/drop contracts and the shared reader remain unchanged.
"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3

from android_release_sync_snapshot import DATABASES, TABLES, _project_owned, rows, sha
from android_release_observation import APP, MAX_FILE
from android_runtime_probe import BODY, require

NOTE = 'release-owned-note.md'
HASH = hashlib.sha256(BODY.encode()).hexdigest()
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
STAGES = ('seeded', 'associated', 'timed-out', 'timed-out-read', 'reconciled', 'cold')


def canonical(value):
    return type(value) is str and re.fullmatch(UUID, value) is not None


def revision_json(raw):
    require(type(raw) is str and len(raw.encode()) <= 4096, 'RELEASE_SYNC_TIMEOUT_REVISION_BOUND')
    def pairs(items):
        value = dict(items); require(len(value) == len(items), 'RELEASE_SYNC_TIMEOUT_REVISION_DUPLICATE_KEYS'); return value
    return json.loads(raw, object_pairs_hook=pairs)


def _admit_original(original, working):
    require(original.is_dir() and not original.is_symlink() and original.absolute()==original.resolve()
        and not working.exists() and not working.is_symlink() and working.absolute()==working.resolve()
        and not working.resolve().is_relative_to(original.resolve()),'RELEASE_SYNC_TIMEOUT_ORIGINAL_OR_WORK_ALIAS')
    allowed={'.':{'snapshot.json','mobile-drafts.sqlite3','mobile-drafts.sqlite3-wal','mobile-drafts.sqlite3-shm','vault'},
        'vault':{NOTE,'.ainote'},'vault/.ainote':{'host.sqlite3','host.sqlite3-wal','host.sqlite3-shm'}}
    for relative,names in allowed.items():
        directory=original/relative
        require(directory.is_dir() and not directory.is_symlink() and directory.absolute()==directory.resolve(),
            'RELEASE_SYNC_TIMEOUT_NESTED_DIRECTORY_ALIAS')
        entries=list(directory.iterdir())
        require(len(entries)<=16 and all(v.name in names and not v.is_symlink() for v in entries),
            'RELEASE_SYNC_TIMEOUT_EXTRA_OR_LINKED_ORIGINAL')
    path=original/'snapshot.json'
    require(path.is_file() and 0<path.stat().st_size<=16384,'RELEASE_SYNC_TIMEOUT_MANIFEST_BOUND')
    raw=path.read_bytes()
    def pairs(items):
        value=dict(items);require(len(value)==len(items),'RELEASE_SYNC_TIMEOUT_MANIFEST_DUPLICATE_KEYS');return value
    manifest=json.loads(raw,object_pairs_hook=pairs)
    required={'package','files','app_stopped_before_and_after','credential_store_copied','SQL_executed_on_device',
        'identity','normal_phone_permissions_verified'}
    require(type(manifest) is dict and set(manifest)==required
        and manifest['package']==APP and manifest['app_stopped_before_and_after'] is True
        and manifest['credential_store_copied'] is False and manifest['SQL_executed_on_device'] is False
        and manifest['normal_phone_permissions_verified'] is False,
        'RELEASE_SYNC_TIMEOUT_MANIFEST_SCOPE')
    expected={'vault/'+NOTE}|{db+suffix for db in DATABASES for suffix in ('','-wal','-shm')}
    require(type(manifest['files']) is dict and set(manifest['files'])==expected,'RELEASE_SYNC_TIMEOUT_EXACT_ORIGINAL_FILES')
    for relative,value in manifest['files'].items():
        if value is None:
            require(relative.endswith(('-wal','-shm')) and not (original/relative).exists(),'RELEASE_SYNC_TIMEOUT_ABSENT_ORIGINAL_CHANGED')
        else:
            require(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
                and 0<=value['bytes']<=MAX_FILE and type(value['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',value['sha256']),
                'RELEASE_SYNC_TIMEOUT_ORIGINAL_FILE_METADATA')
    identity=manifest.get('identity')
    if 'identity' in manifest:
        require(type(identity) is dict and set(identity)=={'avd','qemu','api','build_type','debuggable','boot_id'}
            and type(identity['avd']) is str and re.fullmatch(r'opennexus-ci-[1-9][0-9]{0,19}-[1-9][0-9]{0,5}',identity['avd'])
            and identity['qemu']=='1' and identity['api']=='36' and identity['build_type'] in ('eng','userdebug')
            and identity['debuggable']=='1' and canonical(identity['boot_id']),'RELEASE_SYNC_TIMEOUT_OWNED_SNAPSHOT_IDENTITY')
    return raw,identity


def project_timeout(snapshot, working, *, stage):
    require(type(stage) is str and stage in STAGES, 'RELEASE_SYNC_TIMEOUT_EXACT_STAGE')
    original, copy = Path(snapshot), Path(working)
    original_manifest,identity=_admit_original(original,copy)
    require(original.is_dir() and not original.is_symlink() and original.absolute() == original.resolve()
            and not copy.is_symlink() and copy.absolute() == copy.resolve(), 'RELEASE_SYNC_TIMEOUT_ORIGINAL_OR_COPY_ALIAS')
    value = _project_owned(original, copy, (NOTE,)); native = value['native']
    require(len(value['drafts'][NOTE]) == len(value['saves'][NOTE]) == len(value['files']) == 1
            and len(native['outbox']) == (1 if stage == 'seeded' else 2), 'RELEASE_SYNC_TIMEOUT_UNIQUE_NOTE_SAVE_OUTBOX')
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
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_SAVE_OR_UPLOAD_CHANGED')
    require(archived == dict(upload, operation_id=save['operation_id'], state='pending' if stage == 'seeded' else 'archived')
            and (stage == 'seeded' or upload['operation_id'] != save['operation_id']), 'RELEASE_SYNC_TIMEOUT_INITIAL_OUTBOX_LINEAGE_CHANGED')
    # All-row checks read only approved identifying metadata for rejected hidden
    # documents. Foreign draft/Save bodies and credentials are never queried.
    with closing(sqlite3.connect((copy.resolve()/DATABASES[0]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(rows(db, 'SELECT id,file_id,path,generation,saved_generation FROM drafts') ==
                [tuple(draft[k] for k in ('id','file_id','path','generation','saved_generation'))]
                and rows(db, 'SELECT operation_id,draft_id,file_id,path,state FROM save_intents') ==
                [tuple(save[k] for k in ('operation_id','draft_id','file_id','path','state'))], 'RELEASE_SYNC_TIMEOUT_HIDDEN_DRAFT_OR_SAVE')
    with closing(sqlite3.connect((copy.resolve()/DATABASES[1]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); binding = native['binding']
        for name, (table, columns) in TABLES.items():
            exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?)", (table,)).fetchone()[0]
            if name == 'issuers' and not exists:
                require(not native['attempts'] and not native['issuers'], 'RELEASE_SYNC_TIMEOUT_ISSUER_TABLE_MISSING'); continue
            actual = rows(db, 'SELECT binding,'+columns+' FROM '+table+' ORDER BY rowid')
            require(all(binding is not None and row[0] == binding['id'] for row in actual)
                    and [dict(zip(columns.split(','), row[1:])) for row in actual] == native[name], 'RELEASE_SYNC_TIMEOUT_HIDDEN_TRANSFER_RECORD')
        exists = db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_binding_intents')").fetchone()[0]
        if exists:
            require(rows(db, 'SELECT operation_id FROM mobile_sync_binding_intents') ==
                    ([(native['association']['operation_id'],)] if binding else []), 'RELEASE_SYNC_TIMEOUT_HIDDEN_ASSOCIATION')
            for request,receipt in rows(db,'SELECT request,binding FROM mobile_sync_binding_intents'):
                revision_json(request);revision_json(receipt)
        if binding is not None:
            association=native['association']
            require(canonical(binding['id']) and type(binding['remote_vault']) is str and re.fullmatch(r'[0-9a-f]{32}',binding['remote_vault'])
                and canonical(association['operation_id']) and canonical(association['auth_operation_id'])
                and type(association['attempt_revision']) is int and association['attempt_revision']>=1
                and all(type(association[k]) is str and re.fullmatch(r'[0-9a-f]{64}',association[k]) for k in ('request_hash','preview_fingerprint')),
                'RELEASE_SYNC_TIMEOUT_ASSOCIATION_METADATA')
        columns = 'binding,operation_id,file_id,operation,base_revision,upload_id'
        commits = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT '+columns+' FROM sync_jobs ORDER BY rowid')]
    require(not native['conflicts'] and all(row['error'] is None for row in native['jobs']),
            'RELEASE_SYNC_TIMEOUT_UNEXPECTED_ERROR_OR_CONFLICT')
    if stage in ('seeded', 'associated'):
        require((native['binding'] is None) == (stage == 'seeded') and upload['state'] == 'pending'
                and (stage == 'seeded' or native['binding']['cursor'] == 0)
                and all(not native[k] for k in TABLES) and not commits, 'RELEASE_SYNC_TIMEOUT_FRESH_UNATTEMPTED_ORIGINAL')
    else:
        require(len(native['jobs']) == len(native['attempts']) == len(native['issuers']) == len(commits) == 1,
                'RELEASE_SYNC_TIMEOUT_UNIQUE_ORIGINAL_TRANSFER')
        job, = native['jobs']; attempt, = native['attempts']; issuer, = native['issuers']; commit, = commits
        require(binding is not None and all(row['operation_id'] == upload['operation_id'] for row in (job, attempt, issuer, commit))
                and commit['binding'] == binding['id'] and job['file_id'] == commit['file_id'] == upload['file_id']
                and job['path'] == NOTE and job['hash'] == HASH and type(job['size']) is int and job['size'] == len(BODY.encode())
                and commit['operation'] == 'put' and type(commit['base_revision']) is int and commit['base_revision'] == 0
                and (commit['upload_id'] is None or type(commit['upload_id']) is str and re.fullmatch(r'[A-Za-z0-9-]{1,80}', commit['upload_id']))
                and type(attempt['attempts']) is int and attempt['attempts'] == 1
                and attempt['outcome'] == 'failed' and attempt['error'] == 'SYNC_NETWORK_ERROR'
                and type(issuer['device_id']) is str and re.fullmatch(r'[0-9a-f]{32}', issuer['device_id']),
                'RELEASE_SYNC_TIMEOUT_ORIGINAL_NETWORK_FAILED_ATTEMPT_CHANGED')
        stopped = stage in ('timed-out', 'timed-out-read')
        require(type(binding['cursor']) is int and binding['cursor'] == (0 if stopped else 1)
                and job['state'] == ('committing' if stopped else 'acked') and job['remote_revision'] == (None if stopped else 1)
                and (stopped or type(job['remote_revision']) is int) and upload['state'] == ('queued' if stopped else 'acked'),
                'RELEASE_SYNC_TIMEOUT_ORIGINAL_JOB_STAGE_CHANGED')
        if stopped:
            require(not native['heads'] and not native['inbox'], 'RELEASE_SYNC_TIMEOUT_PREMATURE_APPLY')
        else:
            require(len(native['heads']) == len(native['inbox']) == 1, 'RELEASE_SYNC_TIMEOUT_UNIQUE_ORIGINAL_APPLY')
            head, = native['heads']; inbox, = native['inbox']; revision = revision_json(inbox['revision'])
            require(head == dict(file_id=file['id'], revision=1, path=NOTE, hash=HASH)
                    and type(head['revision']) is int and type(inbox['sequence']) is int and inbox['sequence'] == 1 and inbox['state'] == 'applied'
                    and canonical(inbox['operation_id']) and canonical(inbox['rename_id'])
                    and type(revision) is dict and set(revision) == {'operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id'}
                    and all(type(revision[k]) is int for k in ('base_revision','size','sequence'))
                    and revision == dict(operation_id=upload['operation_id'], file_id=file['id'], base_revision=0, path=NOTE,
                        operation='put', hash=HASH, size=len(BODY.encode()), sequence=1, vault_id=binding['remote_vault']),
                    'RELEASE_SYNC_TIMEOUT_APPLIED_ORIGINAL_CHANGED')
    value.update(timeout_stage=stage, original_job_commit=commits, all_clean_draft_ids=[draft['id']], all_Save_ids=[save['operation_id']],
        original_Save_outbox_id=archived['operation_id'], original_association_upload_id=None if stage == 'seeded' else upload['operation_id'],
        original_manifest_scope_verified=True,owned_snapshot_identity=identity)
    require((original/'snapshot.json').read_bytes()==original_manifest,'RELEASE_SYNC_TIMEOUT_MANIFEST_CHANGED_DURING_READ')
    # The dedicated all-row checks follow the common reader's final hash guard.
    # Recheck at this later boundary too; never return a mixed-time original.
    for relative, metadata in json.loads(original_manifest)['files'].items():
        path = original/relative
        require(not path.exists() if metadata is None else path.is_file() and not path.is_symlink()
                and path.stat().st_size == metadata['bytes'] and sha(path) == metadata['sha256'],
                'RELEASE_SYNC_TIMEOUT_RETAINED_ORIGINAL_CHANGED')
    return value


def preserve_note(before, after):
    require(all(before[k] == after[k] for k in ('files','drafts','saves','all_clean_draft_ids','all_Save_ids'))
            and before['owned_snapshot_identity']==after['owned_snapshot_identity']
            and before['raw_sha256']['vault/'+NOTE] == after['raw_sha256']['vault/'+NOTE], 'RELEASE_SYNC_TIMEOUT_ORIGINAL_NOTE_OR_SAVE_CHANGED')


def preserve_initial_outbox(seeded, associated):
    preserve_note(seeded, associated)
    saved, = seeded['native']['outbox']; archived, upload = associated['native']['outbox']
    require(seeded['timeout_stage'] == 'seeded' and associated['timeout_stage'] == 'associated'
            and saved['operation_id'] == seeded['all_Save_ids'][0] == associated['all_Save_ids'][0]
            and saved['state'] == 'pending' and archived == dict(saved, state='archived')
            and upload == dict(saved, operation_id=associated['original_association_upload_id']) and upload['operation_id'] != saved['operation_id'],
            'RELEASE_SYNC_TIMEOUT_INITIAL_OUTBOX_PUBLICATION_CHANGED')


def preserve_read(before, after):
    require((before['timeout_stage'], after['timeout_stage']) in (('timed-out','timed-out-read'),('reconciled','cold')),
            'RELEASE_SYNC_TIMEOUT_EXACT_READ_PAIR')
    require({k:v for k,v in before.items() if k not in ('raw_sha256','timeout_stage')} ==
            {k:v for k,v in after.items() if k not in ('raw_sha256','timeout_stage')}, 'RELEASE_SYNC_TIMEOUT_READ_CHANGED_RECORDS')
    preserve_note(before, after)
