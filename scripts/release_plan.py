"""Fixed release inputs and durable local receipts. Contains no credentials."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


PRIVATE = ('docs/', 'documents/', 'frontend/docs/', '.local-plans/')
HEX = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,179}')
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?')


class ReleaseError(RuntimeError):
    """A stable error code, never a remote response or credential."""


def require(value, code):
    if not value:
        raise ReleaseError(code)


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def text_digest(value):
    return hashlib.sha256(value.replace('\r\n', '\n').encode()).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def journal_lock(path):
    """Kernel ownership ends on process death; no stale lock deletion or PID kill."""
    lock = Path(str(path)+'.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    require(not lock.is_symlink(), 'JOURNAL_LOCK_LINK')
    with lock.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ReleaseError('JOURNAL_BUSY') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def local_file(base, name):
    require(isinstance(name, str) and NAME.fullmatch(name), 'ARTIFACT_NAME_INVALID')
    path = base/name
    require(path.is_file() and not path.is_symlink(), 'ARTIFACT_MISSING_OR_LINK')
    require(path.resolve().parent == base.resolve(), 'ARTIFACT_OUTSIDE_PLAN')
    return path


def validate_notes(notes, plan):
    version, product = plan['version'], plan['product']
    require(notes.startswith(f'# {product} {version}\n'), 'NOTES_TITLE_MISMATCH')
    require('## 更新内容\n' in notes and '## English\n' in notes, 'BILINGUAL_NOTES_REQUIRED')
    chinese = notes.split('## 更新内容\n', 1)[1].split('\n## ', 1)[0]
    english = notes.split('## English\n', 1)[1]
    require(len(re.findall(r'[\u4e00-\u9fff]', chinese)) >= 30 and len(re.findall(r'\b[A-Za-z]+\b', english)) >= 60, 'BILINGUAL_DETAILS_REQUIRED')
    require(len(re.findall(r'^- ', chinese, re.M)) == len(re.findall(r'^- ', english, re.M)) > 0, 'BILINGUAL_LIST_MISMATCH')
    prefix = f'https://github.com/{plan["repository"]}/releases/download/{plan["tag"]}/'
    require(all(prefix+asset['name'] in notes for asset in plan['assets']), 'NOTES_DOWNLOADS_MISSING')
    links = re.findall(r'https://github\.com/'+re.escape(plan['repository'])+r'/releases/(?:download|tag)/([^/)\s]+)', notes)
    require(all(tag == plan['tag'] for tag in links), 'NOTES_STALE_TAG')


class Plan:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.base = self.path.parent
        self.data = json.loads(self.path.read_text('utf-8'))
        p = self.data
        require(p.get('schema') == 1, 'PLAN_SCHEMA_INVALID')
        require(isinstance(p.get('repository'), str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', p['repository']), 'REPOSITORY_INVALID')
        require(isinstance(p.get('version'), str) and VERSION.fullmatch(p['version']), 'VERSION_INVALID')
        require(p.get('tag') == 'v'+p['version'] and COMMIT.fullmatch(p.get('commit', '')), 'TAG_OR_COMMIT_INVALID')
        require(isinstance(p.get('product'), str) and len(p['product']) <= 100 and '\n' not in p['product'], 'PRODUCT_INVALID')
        assets = p.get('assets')
        require(isinstance(assets, list) and 2 <= len(assets) <= 16, 'ASSET_COUNT_INVALID')
        require(all(isinstance(asset, dict) for asset in assets), 'ASSET_RECORD_INVALID')
        names = [asset.get('name') for asset in assets]
        require(len(set(names)) == len(names), 'DUPLICATE_ASSET_NAME')
        for asset in assets:
            require(asset.get('file') == asset.get('name'), 'ASSET_FILE_NAME_MISMATCH')
            file = local_file(self.base, asset['name'])
            require(HEX.fullmatch(asset.get('sha256', '')) and isinstance(asset.get('bytes'), int) and not isinstance(asset['bytes'], bool) and asset['bytes'] > 0, 'ASSET_FINGERPRINT_INVALID')
            require(file.stat().st_size == asset['bytes'] and digest(file) == asset['sha256'], 'ASSET_BYTES_CHANGED')
        notes_path = local_file(self.base, p.get('notes_file'))
        self.notes = notes_path.read_text('utf-8').replace('\r\n', '\n')
        require(text_digest(self.notes) == p.get('notes_sha256'), 'NOTES_CHANGED')
        validate_notes(self.notes, p)
        checksums = next((asset for asset in assets if asset['name'] == 'SHA256SUMS.txt'), None)
        require(checksums is not None, 'CHECKSUM_LIST_REQUIRED')
        expected = ''.join(f'{asset["sha256"]}  {asset["name"]}\n' for asset in assets if asset is not checksums)
        require((self.base/checksums['file']).read_text('utf-8') == expected, 'CHECKSUM_LIST_MISMATCH')
        source = next((a for a in assets if a.get('kind') == 'source'), None)
        require(source is not None, 'FIXED_SOURCE_REQUIRED')
        import zipfile
        with zipfile.ZipFile(self.base/source['file']) as archive:
            require(archive.comment.decode('ascii') == p['commit'], 'SOURCE_COMMIT_MISMATCH')
            require(all(not name.casefold().startswith(PRIVATE) and not name.startswith('/') and '\\' not in name and '..' not in Path(name).parts for name in archive.namelist()), 'PRIVATE_OR_UNSAFE_SOURCE_PATH')
        proof = p.get('verification')
        require(isinstance(proof, dict), 'VERIFICATION_REQUIRED')
        receipt = local_file(self.base, proof.get('file'))
        require(digest(receipt) == proof.get('sha256'), 'VERIFICATION_CHANGED')
        result = json.loads(receipt.read_text('utf-8'))
        require(result.get('source_commit') == p['commit'] and result.get('source_clean') is True, 'BUILD_SOURCE_MISMATCH')
        if p['product'] == 'OpenNexus':
            require(set(names) == {f'OpenNexus_{p["version"]}_x64-setup.exe', f'OpenNexus_{p["version"]}_source.zip', 'SHA256SUMS.txt'}, 'DESKTOP_ASSET_NAMES_INVALID')
            installer = next((a for a in assets if a.get('kind') == 'installer'), None)
            require(installer is not None and result.get('installer_sha256') == installer['sha256'], 'INSTALLER_VERIFICATION_MISMATCH')
            require(result.get('host_version') == result.get('core_version') == p['version'] and result.get('native_smoke', {}).get('passed') is True, 'PACKAGE_VERIFICATION_REQUIRED')
            require(result.get('build_source_verified') is True, 'PRECOMPILE_SOURCE_RECEIPT_REQUIRED')
            require(result.get('experiment_runtime', {}).get('native_probe', {}).get('runtime_source') == 'extracted-installer', 'PACKAGED_RUNTIME_PROBE_REQUIRED')
        else:
            require(result.get('version') == p['version'] and result.get('passed') is True, 'SERVICE_VERIFICATION_REQUIRED')
        original = p.get('original')
        if original is not None:
            require(isinstance(original, dict) and isinstance(original.get('release_id'), int) and original['release_id'] > 0 and COMMIT.fullmatch(original.get('tag_ref_sha', '')) and COMMIT.fullmatch(original.get('tag_commit', '')), 'ORIGINAL_IDENTITY_REQUIRED')
            require(HEX.fullmatch(original.get('body_sha256', '')) and isinstance(original.get('assets'), list), 'ORIGINAL_FINGERPRINT_REQUIRED')
            require(len({a['id'] for a in original['assets']}) == len(original['assets']), 'ORIGINAL_ASSET_IDS_INVALID')
            for asset in original['assets']:
                require(isinstance(asset.get('id'), int) and asset['id'] > 0 and NAME.fullmatch(asset.get('name', '')) and HEX.fullmatch(asset.get('sha256', '')) and isinstance(asset.get('bytes'), int) and asset['bytes'] > 0, 'ORIGINAL_ASSET_INVALID')
        self.fingerprint = hashlib.sha256(json.dumps(p, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def check_local_source(plan, root):
    """Verify each archive blob against the fixed Git tree, rather than its comment."""
    import subprocess
    import zipfile
    raw = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', plan.data['commit']], cwd=root)
    expected = {}
    for item in raw.split(b'\0'):
        if not item:
            continue
        metadata, name = item.split(b'\t', 1)
        mode, kind, sha = metadata.decode('ascii').split()
        path = name.decode('utf-8')
        require(kind == 'blob' and mode in {'100644', '100755'}, 'SOURCE_LINK_OR_SUBMODULE')
        require(not path.casefold().startswith(PRIVATE) and '\\' not in path, 'PRIVATE_SOURCE_TRACKED')
        expected[path] = (sha, int(mode, 8))
    for source in [a for a in plan.data['assets'] if a.get('kind') in {'source', 'deployment'}]:
        with zipfile.ZipFile(plan.base/source['file']) as archive:
            require(archive.comment.decode('ascii') == plan.data['commit'], 'SOURCE_COMMIT_MISMATCH')
            for entry in archive.infolist():
                name = entry.filename
                require(not name.casefold().startswith(PRIVATE) and not name.startswith('/') and '\\' not in name
                        and ':' not in name and all(part not in {'', '.', '..'} for part in name.rstrip('/').split('/')),
                        'PRIVATE_OR_UNSAFE_SOURCE_PATH')
                if entry.is_dir():
                    require(any(path.startswith(name) for path in expected), 'SOURCE_ARCHIVE_TREE_MISMATCH')
            actual = {item.filename: item for item in archive.infolist() if not item.is_dir()}
            require(len(actual) == len([item for item in archive.infolist() if not item.is_dir()]), 'SOURCE_ARCHIVE_TREE_MISMATCH')
            require(bool(actual) and (set(actual) == set(expected) if source['kind'] == 'source' else set(actual) <= set(expected)), 'SOURCE_ARCHIVE_TREE_MISMATCH')
            for name, item in actual.items():
                require(item.create_system == 3 and item.external_attr >> 16 == expected[name][1], 'SOURCE_ARCHIVE_MODE_MISMATCH')
                checksum = hashlib.sha1(f'blob {item.file_size}\0'.encode())
                with archive.open(item) as content:
                    while block := content.read(1024*1024):
                        checksum.update(block)
                require(checksum.hexdigest() == expected[name][0], 'SOURCE_ARCHIVE_BLOB_MISMATCH')


def fixed_archive(root, commit, destination, *, include=None):
    """Archive raw Git blobs in one batch, independent of checkout EOL filters."""
    from datetime import datetime, timezone
    import subprocess
    import zipfile
    raw = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', commit], cwd=root)
    entries = []
    for item in raw.split(b'\0'):
        if not item:
            continue
        metadata, encoded_name = item.split(b'\t', 1)
        mode, kind, sha = metadata.decode('ascii').split()
        name = encoded_name.decode('utf-8')
        require(kind == 'blob' and mode in {'100644', '100755'} and '\\' not in name, 'SOURCE_LINK_OR_SUBMODULE')
        require(not name.casefold().startswith(PRIVATE), 'PRIVATE_SOURCE_TRACKED')
        if include is None or include(name):
            entries.append((name, int(mode, 8), sha))
    require(bool(entries), 'EMPTY_FIXED_ARCHIVE')
    timestamp = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', commit], cwd=root))
    date = datetime.fromtimestamp(max(315532800, min(timestamp, 4354819198)), timezone.utc).timetuple()[:6]
    process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.comment = commit.encode('ascii')
            for name, mode, sha in entries:
                process.stdin.write(sha.encode()+b'\n')
                process.stdin.flush()
                header = process.stdout.readline(200).decode('ascii').split()
                require(len(header) == 3 and header[:2] == [sha, 'blob'], 'GIT_BLOB_READ_FAILED')
                remaining = int(header[2])
                checksum = hashlib.sha1(f'blob {remaining}\0'.encode())
                info = zipfile.ZipInfo(name, date_time=date)
                info.create_system, info.external_attr = 3, mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with archive.open(info, 'w') as output:
                    while remaining:
                        block = process.stdout.read(min(1024*1024, remaining))
                        require(bool(block), 'GIT_BLOB_TRUNCATED')
                        remaining -= len(block)
                        output.write(block)
                        checksum.update(block)
                require(process.stdout.read(1) == b'\n' and checksum.hexdigest() == sha, 'GIT_BLOB_READ_FAILED')
        process.stdin.close()
        require(process.wait(timeout=30) == 0, 'GIT_BLOB_READER_FAILED')
    finally:
        if process.poll() is None:
            # Only the Git child started by this archive writer is terminated.
            process.terminate()
            process.wait(timeout=10)
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


def build_source(root, commit):
    """Capture actual compiler inputs in the checkout before starting a build."""
    import subprocess
    root = Path(root).resolve()
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    require(actual == commit and not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip(), 'FIXED_CLEAN_BUILD_SOURCE_REQUIRED')
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    files = {}
    for name in paths:
        if not name:
            continue
        require(not name.casefold().startswith(PRIVATE), 'PRIVATE_BUILD_SOURCE_TRACKED')
        file = root/name
        require(file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root), 'BUILD_SOURCE_LINK')
        files[name] = digest(file)
    overlays = {}
    signing = root/'frontend/src-tauri/tauri.rc.conf.json'
    if signing.exists():
        require(signing.is_file() and not signing.is_symlink(), 'BUILD_OVERLAY_LINK')
        overlays['frontend/src-tauri/tauri.rc.conf.json'] = digest(signing)
    return {'schema': 1, 'source_commit': commit, 'source_clean': True, 'files_sha256': files, 'overlays_sha256': overlays}


class Journal:
    def __init__(self, plan):
        self.path = plan.base/'release-journal.json'
        require(not self.path.is_symlink(), 'JOURNAL_LINK')
        self.data = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {'schema': 1, 'plan_sha256': plan.fingerprint, 'events': []}
        require(self.data.get('schema') == 1 and self.data.get('plan_sha256') == plan.fingerprint and isinstance(self.data.get('events'), list), 'JOURNAL_PLAN_CHANGED')

    def record(self, action, **fields):
        from datetime import datetime, timezone
        self.data['events'].append({'action': action, 'at': datetime.now(timezone.utc).isoformat(), **fields})
        atomic_json(self.path, self.data)

    def events(self, action):
        return [event for event in self.data['events'] if event['action'] == action]
