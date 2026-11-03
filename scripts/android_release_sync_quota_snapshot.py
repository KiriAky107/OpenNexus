"""Closed quota projections from new readonly copies of retained own originals.

This local reader does not collect a device or attest completed-driver/two-stop
admission. The future caller must establish that gate before retaining originals
and remove its task-owned working directory in finally, including on failure.
Only the fixed 56B Note may be retained as release-owned-quota-spool.md; its
manifest mapping names the exact owned spool, never an arbitrary spool prefix.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import android_release_sync_quota_validation as validation

APP = 'cc.kronecker.opennexus'
DATABASES = ('mobile-drafts.sqlite3', 'vault/.ainote/host.sqlite3')
NOTE = 'vault/' + validation.NOTE
SPOOL = 'release-owned-quota-spool.md'
SPOOL_SOURCE = 'vault/.ainote/sync-spool/' + validation.HASH
MAX_FILE = 32 * 1024 * 1024
MAX_MANIFEST = 16384
FILES = frozenset((NOTE, SPOOL)) | frozenset(db + suffix for db in DATABASES for suffix in ('', '-wal', '-shm'))
DRAFT_COLUMNS = {
    'drafts': 'id,file_id,path,base_hash,generation,saved_generation,content',
    'save_intents': 'operation_id,draft_id,generation,file_id,path,expected,content,state,error',
}
HOST_COLUMNS = {
    'files': 'id,path,hash,revision,deleted',
    'outbox': 'operation_id,file_id,revision,path,hash,operation,state',
    'sync_bindings': 'id,remote_vault,state,cursor,endpoint,account',
    'sync_jobs': 'operation_id,file_id,path,hash,size,state,remote_revision,error',
    'sync_attempts': 'operation_id,attempts,outcome,error',
    'mobile_sync_attempt_devices': 'operation_id,device_id',
    'sync_heads': 'file_id,revision,path,hash',
    'sync_inbox': 'sequence,revision,operation_id,rename_id,state',
    'sync_conflicts': 'sequence,file_id,local_path,local_hash,state',
    'mobile_sync_binding_intents': 'operation_id,request_hash,request,state,attempt_revision,binding',
}


def need(value):
    if not value:
        raise RuntimeError('RELEASE_QUOTA_PROJECTION_UNAVAILABLE')


def strict_json(raw):
    def pairs(items):
        value = dict(items)
        need(len(value) == len(items))
        return value
    def constant(_):
        need(False)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def safe_path(path):
    need(path.absolute() == path.resolve())
    stat = path.lstat()
    need(not path.is_symlink() and not getattr(stat, 'st_file_attributes', 0) & 0x400)
    return stat


def file_hash(path):
    stat = safe_path(path)
    need(path.is_file() and stat.st_nlink == 1 and 0 <= stat.st_size <= MAX_FILE)
    digest = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block); need(size <= stat.st_size)
            digest.update(block)
    need(size == stat.st_size)
    return stat.st_size, digest.hexdigest()


def bounded_bytes(path, limit):
    stat = safe_path(path)
    need(path.is_file() and stat.st_nlink == 1 and 0 <= stat.st_size <= limit)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    need(len(raw) == stat.st_size and len(raw) <= limit)
    return raw


def selection_scope(value):
    keys = ('endpoint', 'account', 'local_vault', 'revision', 'generation', 'preview_fingerprint')
    need(validation._closed(value, keys)
         and type(value['endpoint']) is str and len(value['endpoint']) <= 256
         and re.fullmatch(r'https://[a-z0-9.-]+(?::[0-9]{1,5})?/?', value['endpoint'])
         and validation._pattern(value['account'], r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}')
         and validation._uuid(value['local_vault']) and validation._number(value['revision'], 1)
         and validation._number(value['generation'], 1)
         and validation._pattern(value['preview_fingerprint'], r'[0-9a-f]{64}'))
    return dict(value)


def layout(snapshot):
    # Visit only these directories, rejecting foreign trees without entering.
    for relative in ('', 'vault', 'vault/.ainote'):
        directory = snapshot / relative
        safe_path(directory); need(directory.is_dir())
        parts = Path(relative).parts
        allowed = {Path(name).parts[len(parts)] for name in FILES if Path(name).parts[:len(parts)] == parts}
        if not relative:
            allowed.add('snapshot.json')
        entries = list(directory.iterdir())
        need(len(entries) <= 8 and all(entry.name in allowed for entry in entries))
        for entry in entries:
            safe_path(entry)


def admit(snapshot, working, scope, stage):
    need(type(stage) is str and stage in ('associated', 'quota-failed'))
    safe_path(snapshot); safe_path(working.parent)
    need(snapshot.is_dir() and not working.exists() and not working.is_symlink()
         and working.absolute() == working.resolve() and not working.is_relative_to(snapshot)
         and not snapshot.is_relative_to(working))
    path = snapshot / 'snapshot.json'
    stat = safe_path(path)
    need(path.is_file() and stat.st_nlink == 1 and 0 < stat.st_size <= MAX_MANIFEST)
    raw = bounded_bytes(path, MAX_MANIFEST); value = strict_json(raw)
    keys = ('schema', 'package', 'identity', 'quota_scope', 'quota_stage', 'spool_source', 'files',
            'app_stopped_before_and_after', 'credential_store_copied', 'SQL_executed_on_device', 'normal_phone_permissions_verified')
    need(validation._closed(value, keys) and validation._same(value['schema'], 1)
         and validation._same(value['package'], APP) and validation._same(value['quota_scope'], scope)
         and validation._same(value['quota_stage'], stage) and validation._same(value['spool_source'], SPOOL_SOURCE)
         and value['app_stopped_before_and_after'] is True and value['credential_store_copied'] is False
         and value['SQL_executed_on_device'] is False and value['normal_phone_permissions_verified'] is False)
    identity = value['identity']
    need(validation._closed(identity, ('avd', 'qemu', 'api', 'build_type', 'debuggable', 'boot_id'))
         and type(identity['build_type']) is str and identity['build_type'] in ('eng', 'userdebug')
         and validation._same(identity, dict(avd=scope['avd'], qemu='1', api='36',
             build_type=identity['build_type'], debuggable='1', boot_id=scope['boot_id'])))
    need(validation._closed(value['files'], FILES))
    layout(snapshot)
    for name, metadata in value['files'].items():
        path = snapshot / name
        if metadata is None:
            need(name.endswith(('-wal', '-shm')) and not path.exists())
        else:
            need(validation._closed(metadata, ('bytes', 'sha256'))
                 and validation._number(metadata['bytes']) and metadata['bytes'] <= MAX_FILE
                 and validation._pattern(metadata['sha256'], r'[0-9a-f]{64}'))
            need(file_hash(path) == (metadata['bytes'], metadata['sha256']))
    for name in (NOTE, SPOOL):
        need(validation._same(value['files'][name], dict(bytes=56, sha256=validation.HASH))
             and bounded_bytes(snapshot / name, 56) == validation.BODY)
    return raw, value


def copy_originals(snapshot, working, manifest):
    working.mkdir()  # exclusive; unknown/existing output is never replaced
    safe_path(working)
    for name, metadata in sorted(manifest['files'].items()):
        if metadata is None:
            continue
        target = working / name
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_path(target.parent)
        stat = safe_path(snapshot / name)
        need((snapshot / name).is_file() and stat.st_nlink == 1 and stat.st_size == metadata['bytes'])
        copied = 0; digest = hashlib.sha256()
        with (snapshot / name).open('rb') as source, target.open('xb') as out:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                copied += len(block); need(copied <= metadata['bytes'])
                need(out.write(block) == len(block)); digest.update(block)
        need((copied, digest.hexdigest()) == (metadata['bytes'], metadata['sha256'])
             and file_hash(target) == (metadata['bytes'], metadata['sha256']))


def open_copy(path, tables):
    stat = safe_path(path)
    need(path.is_file() and stat.st_nlink == 1 and 0 <= stat.st_size <= MAX_FILE)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    def authorize(action, first, second, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if first in (*tables, 'sqlite_master') else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if second in ('count', 'length') else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            return sqlite3.SQLITE_OK if first in ('query_only', 'integrity_check') else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_OK if first in ('BEGIN', 'COMMIT', 'ROLLBACK') else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_DENY
    try:
        conn.set_authorizer(authorize)
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        need(conn.execute('PRAGMA integrity_check').fetchone() == ('ok',))
        for table in tables:
            row = conn.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (table,)).fetchone()
            need(row is not None and row[0] == 'table' and type(row[1]) is str
                 and row[1].lstrip().upper().startswith('CREATE TABLE '))
        return conn
    except BaseException:
        conn.close()
        raise


def rows(db, table, columns, count):
    need(db.execute('SELECT COUNT(*) FROM ' + table).fetchone() == (count,))
    result = db.execute('SELECT ' + columns + ' FROM ' + table + ' ORDER BY rowid LIMIT 3').fetchall()
    need(len(result) == count and count <= 2)
    return [dict(zip(columns.split(','), row)) for row in result]


def association(db, scope, selected):
    minimal = rows(db, 'mobile_sync_binding_intents', 'operation_id,state,attempt_revision,request_hash', 1)[0]
    need(validation._same(minimal['operation_id'], scope['association_operation_id'])
         and validation._same(minimal['state'], 'bound') and validation._number(minimal['attempt_revision'], 1)
         and validation._pattern(minimal['request_hash'], r'[0-9a-f]{64}'))
    lengths = db.execute('SELECT length(CAST(request AS BLOB)),length(CAST(binding AS BLOB)) FROM mobile_sync_binding_intents').fetchone()
    need(all(type(n) is int and 0 < n <= 8192 for n in lengths))
    request_raw, binding_raw = db.execute('SELECT request,binding FROM mobile_sync_binding_intents').fetchone()
    need(type(request_raw) is str and type(binding_raw) is str)
    expected_request = dict(operation_id=scope['association_operation_id'], local_vault=selected['local_vault'],
        remote_vault=scope['remote_vault'], selection=dict(revision=selected['revision'], endpoint=selected['endpoint'],
        account=selected['account'], operation_id=scope['login_operation_id'], generation=selected['generation']),
        preview_fingerprint=selected['preview_fingerprint'])
    expected_binding = dict(id=scope['binding_id'], endpoint=selected['endpoint'], remote_vault=scope['remote_vault'],
                            account=selected['account'], cursor=0)
    need(validation._same(strict_json(request_raw), expected_request) and validation._same(strict_json(binding_raw), expected_binding))
    return dict(operation_id=minimal['operation_id'], auth_operation_id=scope['login_operation_id'],
        remote_vault=scope['remote_vault'], state='bound', attempt_revision=minimal['attempt_revision'],
        request_hash=minimal['request_hash'], preview_fingerprint=selected['preview_fingerprint'],
        original_request_bytes_sha256=hashlib.sha256(request_raw.encode('utf-8')).hexdigest(),
        original_binding_bytes_sha256=hashlib.sha256(binding_raw.encode('utf-8')).hexdigest())


@dataclass(frozen=True, slots=True)
class QuotaProjection:
    projection: dict
    evidence: dict
    note: bytes
    spool: bytes


def project_quota(snapshot, working, *, scope, selected, stage):
    """Read approved copies only; caller removes its working directory in finally."""
    try:
        scope = validation._scope(scope); selected = selection_scope(selected)
        need(type(snapshot) in (str, type(Path())) and type(working) in (str, type(Path())))
        snapshot, working = Path(snapshot).absolute(), Path(working).absolute()
        manifest_raw, manifest = admit(snapshot, working, scope, stage)
        copy_originals(snapshot, working, manifest)
        with closing(open_copy(working / DATABASES[0], DRAFT_COLUMNS)) as db:
            # Reject hidden/foreign documents before reading any body.
            drafts = rows(db, 'drafts', 'id,file_id,path,generation,saved_generation', 1)
            saves = rows(db, 'save_intents', 'operation_id,draft_id,file_id,path,state', 1)
            need(drafts[0]['path'] == saves[0]['path'] == validation.NOTE)
            draft = rows(db, 'drafts', DRAFT_COLUMNS['drafts'], 1)[0]
            save = rows(db, 'save_intents', DRAFT_COLUMNS['save_intents'], 1)[0]
        with closing(open_copy(working / DATABASES[1], HOST_COLUMNS)) as db:
            binding = rows(db, 'sync_bindings', HOST_COLUMNS['sync_bindings'], 1)[0]
            need(validation._same(binding, dict(id=scope['binding_id'], remote_vault=scope['remote_vault'], state='active',
                    cursor=0, endpoint=selected['endpoint'], account=selected['account'])))
            value = dict(schema=1, scope=scope, stage=stage, draft=draft, save=save,
                         file=rows(db, 'files', HOST_COLUMNS['files'], 1)[0],
                         binding={k: binding[k] for k in ('id', 'remote_vault', 'state', 'cursor')},
                         association=association(db, scope, selected), outbox=rows(db, 'outbox', HOST_COLUMNS['outbox'], 2))
            attempted = int(stage == 'quota-failed')
            for name, table in (('jobs', 'sync_jobs'), ('attempts', 'sync_attempts'), ('issuers', 'mobile_sync_attempt_devices'),
                                ('heads', 'sync_heads'), ('inbox', 'sync_inbox'), ('conflicts', 'sync_conflicts')):
                count = attempted if name in ('jobs', 'attempts', 'issuers') else 0
                value[name] = rows(db, table, HOST_COLUMNS[table], count)
                if count:
                    need(db.execute('SELECT binding FROM ' + table).fetchall() == [(scope['binding_id'],)])
            value['commits'] = rows(db, 'sync_jobs', 'binding,operation_id,file_id,operation,base_revision,upload_id', attempted)
        if stage == 'associated':
            validation._before(value, scope)
        else:
            candidate = dict(value, stage='associated', outbox=[value['outbox'][0], dict(value['outbox'][1], state='pending')],
                             jobs=[], attempts=[], issuers=[], commits=[])
            before = validation._before(candidate, scope)
            need(validation._same(value, validation._failed(before, scope)))
        # Final original checks include manifest, nullable companions and exact
        # Note/spool bytes. SQLite never opens these retained original paths.
        layout(snapshot)
        need(bounded_bytes(snapshot / 'snapshot.json', MAX_MANIFEST) == manifest_raw)
        for name, metadata in manifest['files'].items():
            need(not (snapshot / name).exists() if metadata is None else
                 file_hash(snapshot / name) == (metadata['bytes'], metadata['sha256']))
        note, spool = bounded_bytes(working / NOTE, 56), bounded_bytes(working / SPOOL, 56)
        need(note == spool == validation.BODY)
        evidence = dict(original_manifest_raw_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            original_files=manifest['files'], query_only_new_working_copies=True,
            raw_DB_WAL_SHM_never_opened_by_SQLite=True, credentials_selected=False,
            completed_driver_and_stop_gate_verified=False, runtime_verified=False)
        return QuotaProjection(value, evidence, note, spool)
    except (OSError, sqlite3.Error, ValueError, KeyError, IndexError, UnicodeError, RecursionError):
        raise RuntimeError('RELEASE_QUOTA_PROJECTION_UNAVAILABLE') from None
