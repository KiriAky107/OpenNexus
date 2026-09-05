"""Local installation journal. Only explicitly managed ZIP roots may be removed."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.extensions.errors import ExtensionError

log = logging.getLogger(__name__)


def package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    files = sorted(root.rglob('*'))
    for path in files:
        if path.is_symlink():
            raise ValueError('Package links cannot be restored automatically')
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        total += path.stat().st_size
        if total > 50 * 1024 * 1024 or len(files) > 4096:
            raise ValueError('Package exceeds restoration limits')
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


class InstalledRuntime:
    def __init__(self, runtime, kind: str, data_dir: Path):
        self.runtime = runtime
        self.kind = kind
        self.storage = (data_dir / 'extension-packages').resolve()
        self.path = data_dir / 'extension-installations.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.restoring = False
        self.restore_errors: list[dict[str, str]] = []
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS installations (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def _read(self, identifier):
        with self._db() as db:
            row = db.execute('SELECT data FROM installations WHERE kind=? AND id=?', (self.kind, identifier)).fetchone()
        return json.loads(row[0]) if row else {}

    def _write(self, identifier, data):
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO installations VALUES (?,?,?)', (self.kind, identifier, json.dumps(data)))

    def _save(self, identifier, managed_root=None, *, installing=False):
        if self.restoring:
            return
        record = self.runtime._records[identifier]
        item = self.runtime.get(identifier)
        previous = self._read(identifier)
        self._write(identifier, {
            'path': str(record.package_path), 'digest': package_digest(record.package_path) if installing or not previous else previous['digest'],
            'enabled': item.enabled, 'permissions': getattr(item, 'granted_permissions', []),
            'managed_root': (str(managed_root) if managed_root else None) if installing else previous.get('managed_root'),
            'removed': False,
        })

    def install(self, package_path, *, managed_root=None):
        with self.lock:
            root = Path(package_path).resolve()
            package_digest(root)  # Check before changing runtime state.
            if managed_root is not None:
                owned = Path(managed_root).resolve()
                if owned.parent != self.storage or not root.is_relative_to(owned):
                    raise ValueError('Invalid managed package root')
            item = self.runtime.install(root)
            identifier = getattr(item.manifest, f'{self.kind}_id')
            try:
                self._save(identifier, managed_root, installing=True)
            except Exception:
                self.runtime.uninstall(identifier)
                raise
            self.restore_errors = [error for error in self.restore_errors if error['id'] != identifier]
            return item

    def enable(self, identifier):
        with self.lock:
            # Changed packages must be reinstalled to re-parse their declarations.
            saved = self._read(identifier)
            root = self.runtime._record(identifier).package_path
            if saved and saved.get('digest') != package_digest(root):
                raise ExtensionError('EXTENSION_PACKAGE_CHANGED', 'Package changed; reinstall and review its permissions.', status_code=409)
            item = self.runtime.enable(identifier)
            self._save(identifier)
            return item

    def disable(self, identifier):
        with self.lock:
            item = self.runtime.disable(identifier)
            self._save(identifier)
            return item

    def set_permissions(self, identifier, permissions):
        with self.lock:
            item = self.runtime.set_permissions(identifier, permissions)
            self._save(identifier)
            return item

    def uninstall(self, identifier, *args, **kwargs):
        with self.lock:
            saved = self._read(identifier)
            self.runtime.uninstall(identifier, *args, **kwargs)
            saved['removed'] = True
            self._write(identifier, saved)
            self._cleanup(saved)

    def _cleanup(self, saved):
        raw = saved.get('managed_root')
        if not raw:
            return  # Directory installs belong to the user.
        path = Path(raw)
        if path.is_symlink() or path.resolve().parent != self.storage:
            raise ValueError('Refusing to remove an unmanaged package directory')
        if path.exists():
            shutil.rmtree(path)

    def restore(self):
        with self.lock:
            with self._db() as db:
                rows = db.execute('SELECT id,data FROM installations WHERE kind=?', (self.kind,)).fetchall()
            self.restoring = True
            try:
                for identifier, raw in rows:
                    try:
                        saved = json.loads(raw)
                        if identifier in self.runtime._records:
                            self.runtime.uninstall(identifier)
                        if saved.get('removed'):
                            self._cleanup(saved)
                            continue
                        root = Path(saved['path'])
                        if not root.is_dir() or package_digest(root) != saved['digest']:
                            raise ValueError('Package missing or changed; reinstall and review permissions')
                        item = self.runtime.install(root)
                        actual_id = getattr(item.manifest, f'{self.kind}_id')
                        if actual_id != identifier:
                            self.runtime.uninstall(actual_id)
                            raise ValueError('Package identity changed')
                        if self.kind == 'plugin':
                            self.runtime.set_permissions(identifier, saved.get('permissions', []))
                        if saved.get('enabled'):
                            self.runtime.enable(identifier)
                    except Exception as error:
                        self.restore_errors.append({'kind': self.kind, 'id': identifier, 'message': 'Package recovery failed; inspect the package and reinstall or enable it again.'})
                        log.warning('Extension restore failed: %s/%s (%s)', self.kind, identifier, type(error).__name__)
            finally:
                self.restoring = False
