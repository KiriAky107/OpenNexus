"""Stopped, owned-release auth index observation; raw copies stay private.

Only the fixed metadata index is copied. Ciphertext is fingerprinted on device,
never read. SQLite sees a fresh working copy and explicit nonsecret columns.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from android_release_observation import APP, StoppedReleaseObserver, admit_root, exclusive
from android_runtime_probe import require, require_hosted_emulator

INDEX = 'mobile-sync-auth.sqlite3'
INDEX_FILES = {INDEX + suffix for suffix in ('', '-wal', '-shm')}
APP_ROOT = '/data/user/0/' + APP + '/'
CIPHER_DIRECTORY = 'no_backup/sync-credentials'
CIPHER = re.compile(re.escape(CIPHER_DIRECTORY) + r'/[0-9a-f]{64}/[0-9a-f]{64}\.bin')
INTENT_COLUMNS = 'operation_id,endpoint,account,device_name,kind,phase,device_id,generation,server_logout_confirmed,previous_operation'
SELECTION_COLUMNS = 'revision,endpoint,account,operation_id,generation'


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def project_metadata(snapshot, working):
    """No arbitrary SQL/table/path input, and never open the retained originals."""
    snapshot, working = Path(snapshot).resolve(), Path(working).resolve()
    require(not working.exists() and not working.is_relative_to(snapshot), 'RELEASE_AUTH_NEW_WORKING_COPY')
    manifest = json.loads((snapshot / 'snapshot.json').read_bytes())
    require(manifest.get('package') == APP and manifest.get('app_stopped_before_and_after') is True
            and manifest.get('credential_store_copied') is False and manifest.get('SQL_executed_on_device') is False
            and set(manifest['files']) == INDEX_FILES, 'RELEASE_AUTH_INDEX_SCOPE')
    working.mkdir(parents=True)
    hashes = {}
    for name in sorted(INDEX_FILES):
        original = snapshot / name; item = manifest['files'][name]
        if item is None:
            require(name.endswith(('-wal', '-shm')) and not original.exists(), 'RELEASE_AUTH_ABSENT_WAL_CHANGED')
            continue
        require(type(item) is dict and set(item) == {'bytes', 'sha256'} and type(item['bytes']) is int
                and 0 <= item['bytes'] <= 32 * 1024**2 and original.is_file() and not original.is_symlink()
                and original.stat().st_size == item['bytes'] and file_hash(original) == item['sha256'],
                'RELEASE_AUTH_INDEX_ORIGINAL_CHANGED')
        with original.open('rb') as src, (working / name).open('xb') as out:
            while block := src.read(1024 * 1024): out.write(block)
        hashes[name] = item['sha256']
        require(file_hash(working / name) == item['sha256'], 'RELEASE_AUTH_INDEX_COPY_CHANGED')
    with closing(sqlite3.connect((working / INDEX).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'RELEASE_AUTH_INDEX_INTEGRITY')
        rows = db.execute('SELECT ' + INTENT_COLUMNS + ' FROM auth_intents ORDER BY rowid LIMIT 17').fetchall()
        require(len(rows) <= 16, 'RELEASE_AUTH_INDEX_ROW_BOUND')
        selected = db.execute('SELECT ' + SELECTION_COLUMNS + ' FROM auth_selection LIMIT 2').fetchall()
        require(len(selected) <= 1, 'RELEASE_AUTH_SELECTION_AMBIGUOUS')
    for name, sha in hashes.items():
        require(file_hash(snapshot / name) == sha, 'RELEASE_AUTH_RETAINED_INDEX_CHANGED')
    return dict(intents=[dict(zip(INTENT_COLUMNS.split(','), row)) for row in rows],
                selection=dict(zip(SELECTION_COLUMNS.split(','), selected[0])) if selected else None,
                integrity='ok', secret_payload_selected=False)


class StoppedAuthObserver(StoppedReleaseObserver):
    def __init__(self, device, serial, private_output):
        require_hosted_emulator(serial)
        self.device, self.serial, self.output = device, serial, Path(private_output)
        self.allowed = set(INDEX_FILES)
        self.files = ()
        self.identity = None
        self.stopped_checks = 0
        require(not self.output.exists(), 'RELEASE_AUTH_PRIVATE_ORIGINAL_OUTPUT_EXISTS')
        self.output.mkdir(parents=True)

    def discover_auth_root(self):
        self.require_stopped()
        args = ('find', '/data/user/0/' + APP, '-name', INDEX, '-type', 'f')
        exclusive(self.output / 'root-discovery-intent.json', json.dumps(dict(arguments=args, read_only=True)).encode())
        result = self.shell(*args, check=False)
        exclusive(self.output / 'root-discovery.stdout', result.stdout)
        exclusive(self.output / 'root-discovery.stderr', result.stderr)
        require(result.returncode == 0 and len(result.stdout) <= 8192, 'RELEASE_AUTH_INDEX_DISCOVERY_FAILED')
        values = result.stdout.decode('utf-8', 'strict').splitlines()
        require(len(values) == 1 and values[0].endswith('/' + INDEX), 'RELEASE_AUTH_INDEX_ROOT_AMBIGUOUS')
        root = values[0][:-len(INDEX)]
        if root.startswith('/data/data/' + APP + '/'): root = root.replace('/data/data/', '/data/user/0/', 1)
        admit_root(root); self.path(root, INDEX)
        return root

    def admitted_cipher_path(self, relative):
        require(type(relative) is str and (relative == CIPHER_DIRECTORY or CIPHER.fullmatch(relative)),
                'RELEASE_AUTH_EXACT_CIPHER_FINGERPRINT_PATH')
        self.allowed.add(relative)
        try: return self.path(APP_ROOT, relative)
        finally: self.allowed.remove(relative)

    def ciphertext_fingerprints(self):
        self.require_stopped()
        directory = APP_ROOT + CIPHER_DIRECTORY
        status = self.shell('test', '-e', directory, check=False).returncode
        require(status in (0, 1), 'RELEASE_AUTH_CIPHER_STATUS_UNKNOWN')
        if status == 1:
            self.require_stopped()
            return {}
        self.admitted_cipher_path(CIPHER_DIRECTORY)
        require(self.shell('test', '-d', directory, check=False).returncode == 0, 'RELEASE_AUTH_CIPHER_DIRECTORY')
        require(not self.shell('find', directory, '-mindepth', '1', '-maxdepth', '2', '-type', 'l').stdout,
                'RELEASE_AUTH_CIPHER_SYMLINK')
        raw = self.shell('find', directory, '-mindepth', '1', '-maxdepth', '2', '-type', 'f').stdout
        require(len(raw) <= 16384, 'RELEASE_AUTH_CIPHER_INVENTORY_BOUND')
        names = raw.decode('utf-8', 'strict').splitlines()
        require(len(names) <= 16 and len(set(names)) == len(names), 'RELEASE_AUTH_CIPHER_INVENTORY_BOUND')
        result = {}
        for name in names:
            require(name.startswith(APP_ROOT), 'RELEASE_AUTH_FOREIGN_CIPHER_PATH')
            relative = name[len(APP_ROOT):]
            self.admitted_cipher_path(relative)
            size = self.text('stat', '-c', '%s', name)
            require(re.fullmatch(r'[0-9]{1,6}', size) and 1 <= int(size) <= 65536, 'RELEASE_AUTH_CIPHER_SIZE')
            hashed = self.text('sha256sum', name).split()
            require(len(hashed) == 2 and re.fullmatch(r'[0-9a-f]{64}', hashed[0]) and hashed[1] == name,
                    'RELEASE_AUTH_CIPHER_HASH')
            result[relative] = dict(bytes=int(size), sha256=hashed[0])
        self.require_stopped()
        # No cat/copy/decryption, even on the private runner, for any ciphertext.
        return result
