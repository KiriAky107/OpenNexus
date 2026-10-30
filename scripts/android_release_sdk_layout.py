"""Fixed owned SDK runtime packages; no downloader, installer or device entry point."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import xml.etree.ElementTree as ET
import zipfile

import android_release_sdk_archive as kernel

PACKAGES = ('platform-tools', 'platforms/android-36')
MAX_FILES = 16384
MAX_DIRS = 8192
MAX_TOTAL = 256 * 1024 ** 2
MAX_FILE = 64 * 1024 ** 2
MAX_RECORD = 8 * 1024 ** 2
SAFE = frozenset(('SDK_LAYOUT_OWNER', 'SDK_LAYOUT_PATH', 'SDK_LAYOUT_LIMIT', 'SDK_LAYOUT_METADATA',
    'SDK_LAYOUT_ABI', 'SDK_LAYOUT_CHANGED', 'SDK_LAYOUT_TARGET', 'SDK_LAYOUT_RECORD',
    'SDK_LAYOUT_ORIGINAL_RECORD_EXISTS', 'SDK_LAYOUT_CLEANUP'))
require = kernel.require


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_size, value.st_mtime_ns)


def _owned_identity(path):
    value = path.lstat()
    return value.st_dev, value.st_ino, value.st_uid, stat.S_IFMT(value.st_mode)


def _directory(path):
    require(path.is_absolute() and path.is_dir() and not path.is_symlink() and path.resolve() == path,
            'SDK_LAYOUT_PATH')


def _file(path):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path, 'SDK_LAYOUT_PATH')
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and not (before.st_mode & 0o7000) and
            0 <= before.st_size <= MAX_FILE, 'SDK_LAYOUT_LIMIT')
    with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    require(not path.is_symlink() and path.resolve() == path and _identity(before) == _identity(path.stat()),
            'SDK_LAYOUT_CHANGED')
    return dict(bytes=before.st_size, sha256=digest, mode=stat.S_IMODE(before.st_mode)), _identity(before)


def _raw(path, limit):
    before, identity = _file(path)
    require(before['bytes'] <= limit, 'SDK_LAYOUT_LIMIT')
    value = path.read_bytes()
    require(_identity(path.stat()) == identity and hashlib.sha256(value).hexdigest() == before['sha256'],
            'SDK_LAYOUT_CHANGED')
    return value


def _metadata(directory, package):
    properties = {}
    for line in _raw(directory / 'source.properties', 16384).decode('ascii').splitlines():
        if not line or line.startswith('#'): continue
        require('=' in line, 'SDK_LAYOUT_METADATA')
        key, value = line.split('=', 1)
        require(key not in properties, 'SDK_LAYOUT_METADATA'); properties[key] = value
    require(re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}', properties.get('Pkg.Revision', '')),
            'SDK_LAYOUT_METADATA')
    raw = _raw(directory / 'package.xml', 256 * 1024)
    require(b'<!doctype' not in raw.lower() and b'<!entity' not in raw.lower(), 'SDK_LAYOUT_METADATA')
    tree = ET.fromstring(raw)
    local = [node for node in tree.iter() if node.tag.rsplit('}', 1)[-1] == 'localPackage']
    expected = 'platform-tools' if package == 'platform-tools' else 'platforms;android-36'
    require(len(local) == 1 and local[0].get('path') == expected, 'SDK_LAYOUT_METADATA')
    if package == 'platform-tools':
        adb = directory / 'adb'; before = adb.stat()
        with adb.open('rb') as stream: header = stream.read(20)
        require(header[:7] == b'\x7fELF\x02\x01\x01' and header[18:20] == b'\x3e\x00' and
                (os.name == 'nt' or before.st_mode & 0o111), 'SDK_LAYOUT_ABI')
        require(_identity(before) == _identity(adb.stat()), 'SDK_LAYOUT_CHANGED')
    else:
        require(properties.get('AndroidVersion.ApiLevel') == '36', 'SDK_LAYOUT_METADATA')
        levels = [node.text for node in local[0].iter() if node.tag.rsplit('}', 1)[-1] == 'api-level']
        require(levels == ['36'], 'SDK_LAYOUT_METADATA')
        jar = directory / 'android.jar'; before = jar.stat()
        with zipfile.ZipFile(jar) as archive:
            require(0 < len(archive.infolist()) <= 100000 and
                    sum(e.file_size for e in archive.infolist()) <= MAX_TOTAL and archive.testzip() is None,
                    'SDK_LAYOUT_METADATA')
        require(_identity(before) == _identity(jar.stat()), 'SDK_LAYOUT_CHANGED')
    return dict(revision=properties['Pkg.Revision'], package_path=expected)


def inventory(base):
    """Full package-relative bytes/modes only; credentials and arbitrary roots are excluded."""
    _directory(base)
    packages = {}; identities = {}; count = total = directories = 0
    for package in PACKAGES:
        root = base / package; _directory(root)
        files = {}; modes = {}; pending = [root]
        while pending:
            directory = pending.pop(); _directory(directory)
            rel = directory.relative_to(root).as_posix(); value = directory.stat()
            require(not (value.st_mode & 0o7000), 'SDK_LAYOUT_PATH')
            modes[rel] = stat.S_IMODE(value.st_mode); identities[package + '/' + rel] = _identity(value)
            directories += 1; require(directories <= MAX_DIRS, 'SDK_LAYOUT_LIMIT')
            for path in sorted(directory.iterdir()):
                require(path.name not in ('.', '..') and not path.name.startswith('.') and
                        re.fullmatch(r'[A-Za-z0-9_+.,@() -]+', path.name) and not path.is_symlink() and
                        path.resolve() == path, 'SDK_LAYOUT_PATH')
                relative = path.relative_to(root).as_posix()
                require(path.suffix.lower() not in ('.key', '.pem', '.p12', '.jks', '.keystore', '.sqlite3', '.db')
                        and not path.name.casefold().startswith(('adbkey', 'owned-auth-')), 'SDK_LAYOUT_PATH')
                if path.is_dir(): pending.append(path)
                else:
                    record, identity = _file(path); files[relative] = record; identities[package + '/' + relative] = identity
                    count += 1; total += record['bytes']
                    require(count <= MAX_FILES and total <= MAX_TOTAL, 'SDK_LAYOUT_LIMIT')
        require('source.properties' in files and 'package.xml' in files and files, 'SDK_LAYOUT_METADATA')
        packages[package] = dict(files=files, directories=modes, metadata=_metadata(root, package))
    return dict(packages=packages, files=count, bytes=total, directories=directories), identities


def _encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode('utf8')


def _record(ctx, name, value):
    path = ctx['evidence'] / ('sdk-layout-' + name + '.json'); _directory(path.parent)
    require(not path.exists() and not path.is_symlink(), 'SDK_LAYOUT_ORIGINAL_RECORD_EXISTS')
    raw = _encoded(value); require(len(raw) <= MAX_RECORD, 'SDK_LAYOUT_LIMIT')
    with path.open('xb') as output: output.write(raw); output.flush(); os.fsync(output.fileno())
    return hashlib.sha256(raw).hexdigest()


def _read(ctx, name):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'SDK_LAYOUT_RECORD'); value[key] = item
        return value
    def invalid(_): raise kernel.ArchiveError('SDK_LAYOUT_RECORD')
    raw = _raw(ctx['evidence'] / ('sdk-layout-' + name + '.json'), MAX_RECORD)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    require(type(value) is dict and value.get('owner') == ctx['owner'], 'SDK_LAYOUT_RECORD')
    return value, hashlib.sha256(raw).hexdigest()


def _owner_guard(ctx):
    root = ctx['root']; _directory(root); _directory(ctx['sdk']); _directory(root / 'runtime')
    require(root.parent == ctx['parent'] and root.name == 'sdk-owned-' + ctx['owner']['GITHUB_RUN_ID'] +
            '-' + ctx['owner']['GITHUB_RUN_ATTEMPT'] and root.stat().st_uid == ctx['owner']['uid'] and
            not root.is_relative_to(ctx['sdk']) and not ctx['sdk'].is_relative_to(root), 'SDK_LAYOUT_OWNER')
    owner = {k: ctx['owner'][k] for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    require(_raw(root / 'owner.json', 4096) == json.dumps(owner, sort_keys=True).encode(), 'SDK_LAYOUT_OWNER')


def _created_parent(path, created):
    parent = path.parent
    while parent in created:
        _directory(parent)
        require(_owned_identity(parent) == created[parent], 'SDK_LAYOUT_CHANGED')
        parent = parent.parent


def _copy_file(source, target, expected, source_identity, created, guard):
    guard(); require(target.parent in created, 'SDK_LAYOUT_TARGET'); _created_parent(target, created)
    require(_identity(source.stat()) == source_identity and not source.is_symlink() and source.resolve() == source,
            'SDK_LAYOUT_CHANGED')
    require(not target.exists() and not target.is_symlink(), 'SDK_LAYOUT_TARGET')
    with source.open('rb') as input_file, target.open('xb') as output:
        created[target] = _owned_identity(target); digest = hashlib.sha256(); count = 0
        while block := input_file.read(1024 * 1024):
            count += len(block); require(count <= expected['bytes'], 'SDK_LAYOUT_CHANGED')
            digest.update(block); output.write(block)
        output.flush(); os.fsync(output.fileno())
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'] and
            _identity(source.stat()) == source_identity, 'SDK_LAYOUT_CHANGED')
    target.chmod(expected['mode'])


def _cleanup(ctx, created, tops, guard):
    guard()
    actual = set()
    for top in tops:
        require(top.is_relative_to(ctx['root'] / 'runtime') and top in created, 'SDK_LAYOUT_CLEANUP')
        pending = [top]
        while pending:
            path = pending.pop()
            require(path in created and not path.is_symlink() and path.resolve() == path and
                    _owned_identity(path) == created[path], 'SDK_LAYOUT_CLEANUP')
            actual.add(path)
            if path.is_dir(): pending.extend(path.iterdir())
    require(actual == set(created), 'SDK_LAYOUT_CLEANUP')
    guard()
    for top in reversed(tops):
        require(_owned_identity(top) == created[top] and not top.is_symlink() and top.resolve() == top,
                'SDK_LAYOUT_CLEANUP')
        shutil.rmtree(top)
    require(all(not p.exists() and not p.is_symlink() for p in tops), 'SDK_LAYOUT_CLEANUP')


def prepare(ctx, owner_guard):
    """Only the caller's already-admitted SDK owner; never resumes or replaces a partial copy."""
    owner_guard(); _owner_guard(ctx)
    require(not list(ctx['evidence'].glob('sdk-layout-*.json')), 'SDK_LAYOUT_ORIGINAL_RECORD_EXISTS')
    bases = {path: _owned_identity(path) for path in
             (ctx['parent'], ctx['root'], ctx['root'] / 'runtime', ctx['sdk'], ctx['evidence'])}
    def guard():
        _owner_guard(ctx)
        for path, identity in bases.items():
            _directory(path); require(_owned_identity(path) == identity, 'SDK_LAYOUT_OWNER')
    _record(ctx, 'intent', dict(owner=ctx['owner'], packages=list(PACKAGES), automatic_retry=False))
    value = dict(owner=ctx['owner'], passed=False, inventory_sha256=None, files=0, bytes=0,
                 copied_packages=[], failed_copy_removed=None, cleanup_unknown=False,
                 SDKmanager_install_or_runtime_verified=False)
    created = {}; tops = []
    try:
        original, identities = inventory(ctx['sdk']); owner_guard(); guard()
        private = ctx['root'] / 'runtime'
        require(all(not (private / package).exists() and not (private / package).is_symlink() for package in PACKAGES),
                'SDK_LAYOUT_TARGET')
        platforms = private / 'platforms'
        if platforms.exists():
            _directory(platforms); require(not list(platforms.iterdir()), 'SDK_LAYOUT_TARGET')
            bases[platforms] = _owned_identity(platforms)
        else:
            platforms.mkdir(); created[platforms] = _owned_identity(platforms); tops.append(platforms)
        for package, data in original['packages'].items():
            target = private / package; guard(); _created_parent(target, created); target.mkdir()
            created[target] = _owned_identity(target)
            if not target.is_relative_to(platforms) or platforms not in created: tops.append(target)
            for relative, mode in sorted(data['directories'].items(), key=lambda row: len(Path(row[0]).parts)):
                directory = target / relative
                if relative != '.':
                    guard(); _created_parent(directory, created); directory.mkdir(); created[directory] = _owned_identity(directory)
                _created_parent(directory / 'sdk-layout-mode-check', created)
                directory.chmod(mode)
            for relative, record in data['files'].items():
                _copy_file(ctx['sdk'] / package / relative, target / relative, record,
                           identities[package + '/' + relative], created, guard)
            value['copied_packages'].append(package)
        copied, _ = inventory(private)
        after, final_identities = inventory(ctx['sdk']); owner_guard(); guard()
        require(after == original and final_identities == identities and copied == original, 'SDK_LAYOUT_CHANGED')
        digest = _record(ctx, 'inventory', dict(owner=ctx['owner'], packages=original['packages'],
                          files=original['files'], bytes=original['bytes'], directories=original['directories']))
        value.update(passed=True, inventory_sha256=digest, files=original['files'], bytes=original['bytes'])
    except Exception as error:
        value['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_LAYOUT_UNAVAILABLE'
        value['error_kind'] = {kernel.ArchiveError: 'sequence_guard', FileExistsError: 'file_exists',
            FileNotFoundError: 'file_missing', PermissionError: 'permission_denied', OSError: 'io',
            ET.ParseError: 'metadata', zipfile.BadZipFile: 'metadata', UnicodeDecodeError: 'metadata'}.get(type(error), 'unclassified')
        if created:
            try: owner_guard(); _cleanup(ctx, created, tops, guard); value['failed_copy_removed'] = True
            except Exception: value['failed_copy_removed'] = False; value['cleanup_unknown'] = True
    _directory(ctx['evidence'])
    require(_owned_identity(ctx['evidence']) == bases[ctx['evidence']], 'SDK_LAYOUT_OWNER')
    _record(ctx, 'result', value)
    return value


def ready(ctx, owner_guard):
    owner_guard(); _owner_guard(ctx)
    result, _ = _read(ctx, 'result'); expected, digest = _read(ctx, 'inventory'); intent, _ = _read(ctx, 'intent')
    require(set(intent) == {'owner', 'packages', 'automatic_retry'} and intent['packages'] == list(PACKAGES)
            and intent['automatic_retry'] is False, 'SDK_LAYOUT_RECORD')
    require(set(result) == {'owner', 'passed', 'inventory_sha256', 'files', 'bytes', 'copied_packages',
                'failed_copy_removed', 'cleanup_unknown', 'SDKmanager_install_or_runtime_verified'} and
            set(expected) == {'owner', 'packages', 'files', 'bytes', 'directories'} and
            type(expected['packages']) is dict and set(expected['packages']) == set(PACKAGES), 'SDK_LAYOUT_RECORD')
    for key in ('files', 'bytes', 'directories'):
        require(type(expected[key]) is int and expected[key] >= 0, 'SDK_LAYOUT_RECORD')
    for package in expected['packages'].values():
        require(type(package) is dict and set(package) == {'files', 'directories', 'metadata'} and
                type(package['files']) is dict and type(package['directories']) is dict, 'SDK_LAYOUT_RECORD')
        for record in package['files'].values():
            require(type(record) is dict and set(record) == {'bytes', 'mode', 'sha256'} and
                    type(record['bytes']) is int and 0 <= record['bytes'] <= MAX_FILE and
                    type(record['mode']) is int and 0 <= record['mode'] <= 0o777 and
                    type(record['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', record['sha256']), 'SDK_LAYOUT_RECORD')
        require(all(type(mode) is int and 0 <= mode <= 0o777 for mode in package['directories'].values()), 'SDK_LAYOUT_RECORD')
    require(result.get('passed') is True and result.get('inventory_sha256') == digest and
            result.get('copied_packages') == list(PACKAGES) and result.get('cleanup_unknown') is False and
            result.get('SDKmanager_install_or_runtime_verified') is False and
            result.get('failed_copy_removed') is None and type(result['files']) is int and
            type(result['bytes']) is int, 'SDK_LAYOUT_RECORD')
    target = {k: expected[k] for k in ('packages', 'files', 'bytes', 'directories')}
    source, _ = inventory(ctx['sdk']); copied, _ = inventory(ctx['root'] / 'runtime')
    require(source == copied == target and result['files'] == target['files'] and result['bytes'] == target['bytes'],
            'SDK_LAYOUT_CHANGED')
    owner_guard(); _owner_guard(ctx)
    return result
