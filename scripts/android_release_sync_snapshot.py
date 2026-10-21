"""Closed content/Sync projections of stopped own signed-release snapshots.

SQLite only opens new copies; auth indexes, tokens and arbitrary queries are
not accepted. Durable file jobs are distinct from the Native RAM invocation.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from android_release_observation import APP, DATABASES, MAX_FILE, admit_attachment
from android_runtime_probe import IMAGE_BYTES, require

LOCAL = ('release-owned-note.md', 'release-owned-original.canvas')
PEER = ('release-owned-peer-note.md', 'release-owned-peer-original.canvas')
IMAGE_HASH = hashlib.sha256(IMAGE_BYTES).hexdigest()
ASSET = f'attachments/{IMAGE_HASH[:2]}/{IMAGE_HASH}.png'
TABLES = {
    'jobs': ('sync_jobs', 'operation_id,file_id,path,hash,size,state,remote_revision,error'),
    'attempts': ('sync_attempts', 'operation_id,attempts,outcome,error'),
    'heads': ('sync_heads', 'file_id,revision,path,hash'),
    'inbox': ('sync_inbox', 'sequence,revision,operation_id,rename_id,state'),
    'conflicts': ('sync_conflicts', 'sequence,file_id,local_path,local_hash,state'),
    'issuers': ('mobile_sync_attempt_devices', 'operation_id,device_id'),
}


def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def owned_paths(incoming):
    require(type(incoming) is bool, 'RELEASE_SYNC_EXACT_CONTENT_STAGE')
    return LOCAL + PEER + (ASSET,) if incoming else LOCAL


def rows(db, sql, args=()):
    value = db.execute(sql + ' LIMIT 65', args).fetchall()
    require(len(value) <= 64, 'RELEASE_SYNC_METADATA_ROW_BOUND')
    return value


def project_sync(snapshot, working, *, incoming=False):
    snapshot, working = Path(snapshot).resolve(), Path(working).resolve()
    paths = owned_paths(incoming)
    admit_attachment(ASSET)
    expected = {'vault/' + path for path in paths} | {db + suffix for db in DATABASES for suffix in ('', '-wal', '-shm')}
    manifest = json.loads((snapshot / 'snapshot.json').read_bytes())
    require(manifest['package'] == APP and manifest['app_stopped_before_and_after'] is True
            and manifest['credential_store_copied'] is False and manifest['SQL_executed_on_device'] is False
            and set(manifest['files']) == expected, 'RELEASE_SYNC_RAW_SNAPSHOT_SCOPE')
    require(not working.exists() and not working.is_relative_to(snapshot), 'RELEASE_SYNC_NEW_WORKING_COPY')
    working.mkdir(parents=True); hashes = {}
    for relative in sorted(expected):
        value = manifest['files'][relative]; original = snapshot / relative
        if value is None:
            require(relative.endswith(('-wal', '-shm')) and not original.exists(), 'RELEASE_SYNC_ABSENT_WAL_CHANGED')
            continue
        require(set(value) == {'bytes', 'sha256'} and type(value['bytes']) is int and 0 <= value['bytes'] <= MAX_FILE
                and original.is_file() and not original.is_symlink() and original.stat().st_size == value['bytes']
                and sha(original) == value['sha256'], 'RELEASE_SYNC_ORIGINAL_CHANGED')
        target = working / relative; target.parent.mkdir(parents=True, exist_ok=True)
        with original.open('rb') as src, target.open('xb') as out:
            while block := src.read(1024 * 1024): out.write(block)
        require(sha(target) == value['sha256'], 'RELEASE_SYNC_WORKING_COPY_CHANGED')
        hashes[relative] = value['sha256']
    with closing(sqlite3.connect((working / DATABASES[0]).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'RELEASE_SYNC_DRAFT_INTEGRITY')
        drafts, saves = {}, {}
        for path in paths:
            for result, table, columns in ((drafts, 'drafts', 'id,file_id,path,base_hash,generation,saved_generation,content'),
                    (saves, 'save_intents', 'operation_id,draft_id,generation,file_id,path,expected,content,state,error')):
                result[path] = [dict(zip(columns.split(','), row)) for row in rows(db,
                    'SELECT ' + columns + ' FROM ' + table + ' WHERE path=? ORDER BY rowid', (path,))]
    with closing(sqlite3.connect((working / DATABASES[1]).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'RELEASE_SYNC_HOST_INTEGRITY')
        columns = 'id,path,hash,revision,deleted'
        files = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT ' + columns + ' FROM files ORDER BY path')]
        require(len(files) == len(paths) and {v['path'] for v in files} == set(paths)
                and all(v['deleted'] == 0 and v['hash'] == hashes['vault/' + v['path']] for v in files),
                'RELEASE_SYNC_EXACT_OWNED_FILE_SET')
        columns = 'operation_id,file_id,revision,path,hash,operation,state'
        outbox = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT ' + columns + ' FROM outbox ORDER BY rowid')]
        require(all(v['path'] in paths for v in outbox), 'RELEASE_SYNC_FOREIGN_OUTBOX')
        columns = 'id,remote_vault,state,cursor,endpoint,account'
        bindings = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT ' + columns + ' FROM sync_bindings')]
        require(len(bindings) <= 1, 'RELEASE_SYNC_BINDING_AMBIGUOUS')
        native = dict(binding=None, association=None, outbox=outbox, **{name: [] for name in TABLES})
        if bindings:
            binding = bindings[0]; require(binding['state'] == 'active', 'RELEASE_SYNC_BINDING_INACTIVE')
            native['binding'] = binding
            for name, (table, columns) in TABLES.items():
                if name == 'issuers' and not db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_attempt_devices')").fetchone()[0]:
                    require(not native['attempts'], 'RELEASE_SYNC_ATTEMPT_WITHOUT_ISSUER_TABLE')
                    continue
                native[name] = [dict(zip(columns.split(','), row)) for row in rows(db,
                    'SELECT ' + columns + ' FROM ' + table + ' WHERE binding=? ORDER BY rowid', (binding['id'],))]
            columns = 'operation_id,request_hash,request,state,attempt_revision,binding'
            intents = rows(db, 'SELECT ' + columns + ' FROM mobile_sync_binding_intents')
            require(len(intents) == 1 and intents[0][3] == 'bound', 'RELEASE_SYNC_ASSOCIATION_NOT_UNIQUE_BOUND')
            original = intents[0]; request = json.loads(original[2]); receipt = json.loads(original[5])
            require(set(request) == {'operation_id', 'local_vault', 'remote_vault', 'selection', 'preview_fingerprint'}
                    and set(request['selection']) == {'revision', 'endpoint', 'account', 'operation_id', 'generation'}
                    and set(receipt) == {'id', 'endpoint', 'remote_vault', 'account', 'cursor'}
                    and receipt['id'] == binding['id'] and receipt['remote_vault'] == binding['remote_vault'] == request['remote_vault']
                    and receipt['endpoint'] == binding['endpoint'] == request['selection']['endpoint']
                    and receipt['account'] == binding['account'] == request['selection']['account']
                    and type(receipt['cursor']) is int and 0 <= receipt['cursor'] <= binding['cursor'],
                    'RELEASE_SYNC_ORIGINAL_BINDING_RECEIPT_CHANGED')
            native['association'] = dict(operation_id=original[0], request_hash=original[1], state=original[3], attempt_revision=original[4],
                preview_fingerprint=request['preview_fingerprint'], remote_vault=request['remote_vault'], auth_operation_id=request['selection']['operation_id'],
                original_request_bytes_sha256=hashlib.sha256(original[2].encode()).hexdigest(),
                original_binding_bytes_sha256=hashlib.sha256(original[5].encode()).hexdigest())
        require(all(v.get('path', v.get('local_path')) in paths for table in ('jobs', 'heads', 'conflicts') for v in native[table]),
                'RELEASE_SYNC_FOREIGN_TRANSFER_PATH')
    for relative, value in hashes.items(): require(sha(snapshot / relative) == value, 'RELEASE_SYNC_RETAINED_ORIGINAL_CHANGED')
    return dict(native=native, files=files, drafts=drafts, saves=saves, raw_sha256=hashes,
                integrity='ok', credentials_selected=False, query_only_new_working_copies=True,
                raw_DB_WAL_SHM_never_opened_by_SQLite=True, native_RAM_invocation_id_observed=False)
