"""Inspect only working copies of closed, hash-bound own release snapshots.

Retained DB/WAL/SHM bytes are never opened by SQLite. No auth database, user-chosen
SQL or unspecific database export is accepted. This is observation, not runtime proof.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from android_release_observation import APP, DATABASES, admit_files
from android_runtime_probe import require


def fingerprint(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect_stopped_snapshot(snapshot, working, files):
    snapshot, working = Path(snapshot).resolve(), Path(working).resolve()
    fixtures = admit_files(files)
    require(not working.exists() and not working.is_relative_to(snapshot), 'RELEASE_SNAPSHOT_NEW_WORKING_COPY_REQUIRED')
    original = json.loads((snapshot / 'snapshot.json').read_text('utf-8'))
    expected = set(fixtures) | {db + suffix for db in DATABASES for suffix in ('', '-wal', '-shm')}
    require(type(original) is dict and original.get('package') == APP and original.get('app_stopped_before_and_after') is True
        and original.get('credential_store_copied') is False and original.get('SQL_executed_on_device') is False
        and type(original.get('files')) is dict and set(original['files']) == expected,
        'RELEASE_SNAPSHOT_ORIGINAL_SCOPE')
    working.mkdir(parents=True)
    hashes = {}
    for relative in sorted(expected):
        metadata = original['files'][relative];path = snapshot / relative
        if metadata is None:
            require(relative.endswith(('-wal', '-shm')) and not path.exists(), 'RELEASE_SNAPSHOT_ABSENT_WAL_CHANGED')
            continue
        require(type(metadata) is dict and set(metadata) == {'bytes', 'sha256'} and type(metadata['bytes']) is int
            and path.is_file() and not path.is_symlink() and path.stat().st_size == metadata['bytes']
            and fingerprint(path) == metadata['sha256'], 'RELEASE_SNAPSHOT_RETAINED_ORIGINAL_CHANGED')
        target = working / relative;target.parent.mkdir(parents=True, exist_ok=True)
        with path.open('rb') as source, target.open('xb') as output:
            while block := source.read(1024 * 1024): output.write(block)
        require(fingerprint(target) == metadata['sha256'], 'RELEASE_SNAPSHOT_WORKING_COPY_CHANGED')
        hashes[relative] = metadata['sha256']
    result = {path: {} for path in files}
    def rows(db, sql, values):
        found = db.execute(sql + ' LIMIT 17', values).fetchall()
        require(len(found) <= 16, 'RELEASE_SNAPSHOT_OWN_ROW_BOUND')
        return found
    with closing(sqlite3.connect((working / DATABASES[0]).as_uri() + '?mode=ro', uri=True)) as drafts:
        drafts.execute('PRAGMA query_only=ON')
        require(drafts.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'RELEASE_SNAPSHOT_DRAFT_INTEGRITY')
        for key, table, columns in [('drafts', 'drafts', 'id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
                ('saves', 'save_intents', 'operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
            for path in files:
                result[path][key] = [dict(zip(columns.split(','), row)) for row in rows(drafts,
                    'SELECT ' + columns + ' FROM ' + table + ' WHERE path=? ORDER BY rowid', (path,))]
    with closing(sqlite3.connect((working / DATABASES[1]).as_uri() + '?mode=ro', uri=True)) as host:
        host.execute('PRAGMA query_only=ON')
        require(host.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'RELEASE_SNAPSHOT_HOST_INTEGRITY')
        require(host.execute('SELECT COUNT(*) FROM files').fetchone() == (len(files),),
            'RELEASE_SNAPSHOT_FRESH_PROFILE_HAS_FOREIGN_FILES')
        for path, item in result.items():
            columns = 'id,path,hash,revision,deleted'
            item['files'] = [dict(zip(columns.split(','), row)) for row in rows(host,
                'SELECT ' + columns + ' FROM files WHERE path=?', (path,))]
            require(len(item['files']) == 1 and item['files'][0]['deleted'] == 0
                and item['files'][0]['hash'] == hashes['vault/' + path], 'RELEASE_SNAPSHOT_RAW_NATIVE_HASH_MISMATCH')
            columns = 'operation_id,file_id,revision,path,hash,operation,state'
            item['outbox'] = [dict(zip(columns.split(','), row)) for row in rows(host,
                'SELECT ' + columns + ' FROM outbox WHERE file_id=? ORDER BY operation_id', (item['files'][0]['id'],))]
            ids = sorted({v['operation_id'] for v in item['outbox'] + item['saves']})
            require(len(ids) <= 16, 'RELEASE_SNAPSHOT_OPERATION_BOUND')
            columns = 'operation_id,fingerprint,state,result'
            item['operations'] = [dict(zip(columns.split(','), row)) for row in rows(host,
                'SELECT ' + columns + ' FROM operations WHERE operation_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY operation_id', ids)] if ids else []
    for relative, value in hashes.items():
        require(fingerprint(snapshot / relative) == value, 'RELEASE_SNAPSHOT_RETAINED_ORIGINAL_CHANGED_DURING_INSPECTION')
    return dict(owned_files=result, raw_sha256=hashes, query_only_working_copies=True,
        retained_DB_WAL_SHM_never_opened_by_SQLite=True, credential_store_copied=False,
        actual_GUI_upgrade_or_physical_result=False)


def same_originals(before, after):
    def stable(value):
        value = json.loads(json.dumps(value))
        for item in value['owned_files'].values():
            for row in item['drafts']: row.pop('edit_session', None)
        return value['owned_files']
    require(stable(before) == stable(after), 'RELEASE_UPGRADE_ORIGINAL_ROWS_CHANGED')
    files = {'vault/' + name for name in before['owned_files']}
    require(set(after['owned_files']) == set(before['owned_files']) and all(
        before['raw_sha256'][name] == after['raw_sha256'][name] for name in files), 'RELEASE_UPGRADE_ORIGINAL_BYTES_CHANGED')
    return dict(original_files_and_draft_content_and_original_save_outbox_operation_rows_unchanged=True,
        only_transient_edit_session_excluded=True)
