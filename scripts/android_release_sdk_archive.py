"""Fixed official Emulator archive verification, before any owned AVD is started.

This module does not launch an emulator, install an APK, or accept download options.
Workflow integration and verification of stopped-AVD cleanup are separate steps.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile


FIXED = dict(
    version='37.2.12',
    url='https://dl.google.com/android/repository/emulator-linux_x64-16428233.zip',
    bytes=349654171, sha1='cd7362ea55dfb86a418958138dc396e74165dd01',
    directory_sha256='b6dc1abbc35f7e13a78fef0c1c307f1ba029d008f6cafc88f684d49fa542e23d',
    entries=451, expanded_bytes=903291301,
)
MAX_ENTRIES = 1024
MAX_EXPANDED = 1024 ** 3
MAX_FILE = 256 * 1024 ** 2
DOWNLOAD_SECONDS = 900
SAFE_CODES = frozenset('''SDK_REDIRECT_REFUSED SDK_HOSTED_TIMEOUT_ONLY SDK_OWNER_INVALID
SDK_WORKSPACE_ALIAS SDK_PARENT_ALIAS SDK_ORIGINAL_NAMESPACE_EXISTS SDK_DOWNLOAD_EXISTS
SDK_RESPONSE_IDENTITY SDK_RESPONSE_LENGTH SDK_DOWNLOAD_DEADLINE SDK_DOWNLOAD_OVERSIZE
SDK_DOWNLOAD_INCOMPLETE SDK_ZIP_END SDK_ZIP_DIRECTORY SDK_ZIP_COUNT SDK_ZIP_PATH
SDK_ZIP_DUPLICATE SDK_ZIP_LINK_OR_MODE SDK_ZIP_ENTRY SDK_ZIP_EXPANDED_LIMIT
SDK_ZIP_EXPANDED_SIZE SDK_ZIP_REQUIRED_FILES SDK_ARCHIVE_ALIAS SDK_ARCHIVE_SIZE
SDK_ARCHIVE_CHECKSUM SDK_ARCHIVE_DIRECTORY_HASH SDK_EXTRACT_EXISTS_OR_ALIAS
SDK_ZIP_CRC SDK_EXTRACT_ESCAPE SDK_EXTRACT_ALIAS SDK_EXTRACT_OVERSIZE
SDK_EXTRACT_INCOMPLETE SDK_ARCHIVE_CHANGED SDK_LAUNCHER_ABI SDK_PROPERTIES_LIMIT
SDK_INSTALLED_REVISION SDK_CLEANUP_OWNER_CHANGED'''.split())


class ArchiveError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise ArchiveError(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArchiveError('SDK_REDIRECT_REFUSED')


def hosted_owner():
    require(sys.platform == 'linux' and all(os.environ.get(k) == v for k, v in (
        ('GITHUB_ACTIONS', 'true'), ('RUNNER_ENVIRONMENT', 'github-hosted'),
        ('RUNNER_OS', 'Linux'), ('RUNTIME_SCENE', 'sync-timeout'))), 'SDK_HOSTED_TIMEOUT_ONLY')
    owner = {k: os.environ.get(k, '') for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    require(re.fullmatch(r'[1-9][0-9]{0,19}', owner['GITHUB_RUN_ID']) and
            re.fullmatch(r'[1-9][0-9]{0,3}', owner['GITHUB_RUN_ATTEMPT']) and
            re.fullmatch(r'[0-9a-f]{40}', owner['GITHUB_SHA']), 'SDK_OWNER_INVALID')
    raw_workspace = os.environ.get('GITHUB_WORKSPACE', '')
    workspace = Path(raw_workspace)
    require(workspace.is_absolute() and '..' not in workspace.parts and workspace.is_dir()
            and str(workspace) == raw_workspace and workspace.resolve() == workspace
            and not workspace.is_symlink(), 'SDK_WORKSPACE_ALIAS')
    parent = workspace / '.build/android-release-runtime'
    require(parent.is_dir() and parent.resolve() == parent and not parent.is_symlink(), 'SDK_PARENT_ALIAS')
    root = parent / ('sdk-owned-' + owner['GITHUB_RUN_ID'] + '-' + owner['GITHUB_RUN_ATTEMPT'])
    require(not root.exists() and not root.is_symlink(), 'SDK_ORIGINAL_NAMESPACE_EXISTS')
    return root, owner


def download(path, facts, *, opener=None):
    require(not path.exists() and path.name == 'emulator.zip', 'SDK_DOWNLOAD_EXISTS')
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect()).open
    request = urllib.request.Request(FIXED['url'], headers={
        'Accept-Encoding': 'identity', 'User-Agent': 'OpenNexus-fixed-owned-SDK'})
    facts.update(url=FIXED['url'], request_method='GET', attempt_count=1, automatic_retry=False)
    started = time.monotonic()
    last_clock = started
    def clock_guard():
        nonlocal last_clock
        now = time.monotonic()
        require(now >= last_clock and now - started <= DOWNLOAD_SECONDS, 'SDK_DOWNLOAD_DEADLINE')
        last_clock = now
    with opener(request, timeout=45) as response:
        require(type(response.status) is int and 100 <= response.status <= 599, 'SDK_RESPONSE_IDENTITY')
        facts['response_status'] = response.status
        require(response.status == 200 and response.geturl() == FIXED['url'], 'SDK_RESPONSE_IDENTITY')
        require(response.headers.get('Content-Encoding') in (None, 'identity') and
                response.headers.get('Content-Range') is None and
                response.headers.get('Content-Length') == str(FIXED['bytes']), 'SDK_RESPONSE_LENGTH')
        count = 0
        with path.open('xb') as output:
            while True:
                clock_guard()
                block = response.read(1024 * 1024)
                clock_guard()
                if not block:
                    break
                count += len(block)
                require(count <= FIXED['bytes'], 'SDK_DOWNLOAD_OVERSIZE')
                output.write(block)
                facts['bytes_written'] = count
            output.flush()
            os.fsync(output.fileno())
        facts['actual_download_bytes'] = count
        require(count == FIXED['bytes'], 'SDK_DOWNLOAD_INCOMPLETE')


def directory_bytes(stream, size):
    stream.seek(max(0, size - 65557))
    tail = stream.read()
    offset = tail.rfind(b'PK\x05\x06')
    require(offset >= 0 and len(tail) - offset >= 22, 'SDK_ZIP_END')
    _, disk, cd_disk, disk_count, count, length, start, comment = struct.unpack_from('<4s4H2LH', tail, offset)
    require(disk == cd_disk == 0 and disk_count == count and
            offset + 22 + comment == len(tail) and count <= MAX_ENTRIES and
            length <= 2 * 1024 ** 2 and start + length == size - len(tail) + offset, 'SDK_ZIP_DIRECTORY')
    stream.seek(start)
    data = stream.read(length)
    require(len(data) == length, 'SDK_ZIP_DIRECTORY')
    return data


def inventory(entries):
    require(len(entries) == FIXED['entries'] <= MAX_ENTRIES, 'SDK_ZIP_COUNT')
    names = set()
    total = 0
    for entry in entries:
        name = entry.orig_filename
        raw_parts = name.rstrip('/').split('/')
        require(name == entry.filename and name and len(name) <= 512 and name.isascii() and
                all(32 <= ord(c) < 127 for c in name) and '\\' not in name and ':' not in name and
                all(p not in ('', '.', '..') for p in raw_parts) and raw_parts[0] == 'emulator'
                and not PurePosixPath(name).is_absolute(), 'SDK_ZIP_PATH')
        require(name.casefold().rstrip('/') not in names, 'SDK_ZIP_DUPLICATE')
        names.add(name.casefold().rstrip('/'))
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        require(kind in (0, stat.S_IFREG, stat.S_IFDIR) and not mode & 0o7000 and
                (kind != stat.S_IFDIR or entry.is_dir()) and
                (kind != stat.S_IFREG or not entry.is_dir()), 'SDK_ZIP_LINK_OR_MODE')
        require(not entry.flag_bits & 1 and entry.compress_type in (0, 8) and
                0 <= entry.file_size <= MAX_FILE and entry.compress_size >= 0, 'SDK_ZIP_ENTRY')
        total += entry.file_size
        require(total <= MAX_EXPANDED, 'SDK_ZIP_EXPANDED_LIMIT')
    require(total == FIXED['expanded_bytes'], 'SDK_ZIP_EXPANDED_SIZE')
    require({'emulator/emulator', 'emulator/source.properties'} <= names, 'SDK_ZIP_REQUIRED_FILES')


def verify_and_extract(archive, target, facts):
    require(archive.is_file() and not archive.is_symlink() and archive.resolve() == archive,
            'SDK_ARCHIVE_ALIAS')
    original = archive.stat()
    require(original.st_size == FIXED['bytes'], 'SDK_ARCHIVE_SIZE')
    sha1, sha256 = hashlib.sha1(), hashlib.sha256()
    with archive.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            sha1.update(block); sha256.update(block)
        require(sha1.hexdigest() == FIXED['sha1'], 'SDK_ARCHIVE_CHECKSUM')
        require(hashlib.sha256(directory_bytes(stream, original.st_size)).hexdigest() == FIXED['directory_sha256'],
                'SDK_ARCHIVE_DIRECTORY_HASH')
    facts.update(archive_bytes=original.st_size, archive_sha1=sha1.hexdigest(), archive_sha256=sha256.hexdigest())
    require(not target.exists() and not target.is_symlink() and target.parent.resolve() == target.parent,
            'SDK_EXTRACT_EXISTS_OR_ALIAS')
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        inventory(entries)
        # Validate all entry CRCs before creating any runtime file.
        require(bundle.testzip() is None, 'SDK_ZIP_CRC')
        target.mkdir()
        for entry in entries:
            output = target.joinpath(*PurePosixPath(entry.filename).parts)
            require(output.is_relative_to(target), 'SDK_EXTRACT_ESCAPE')
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            require(output.parent.resolve() == output.parent and not output.exists() and not output.is_symlink(),
                    'SDK_EXTRACT_ALIAS')
            count = 0
            with bundle.open(entry) as source, output.open('xb') as destination:
                while block := source.read(1024 * 1024):
                    count += len(block)
                    require(count <= entry.file_size, 'SDK_EXTRACT_OVERSIZE')
                    destination.write(block)
            require(count == entry.file_size, 'SDK_EXTRACT_INCOMPLETE')
            output.chmod((entry.external_attr >> 16) & 0o755 or 0o644)
    require(archive.stat().st_size == original.st_size and archive.stat().st_mtime_ns == original.st_mtime_ns
            and archive.stat().st_ino == original.st_ino, 'SDK_ARCHIVE_CHANGED')
    with archive.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == facts['archive_sha256'], 'SDK_ARCHIVE_CHANGED')
    launcher = target / 'emulator/emulator'
    with launcher.open('rb') as stream:
        header = stream.read(20)
    require(len(header) == 20 and header[:7] == b'\x7fELF\x02\x01\x01' and header[18:20] == b'\x3e\x00',
            'SDK_LAUNCHER_ABI')
    properties = target / 'emulator/source.properties'
    require(properties.stat().st_size <= 16384, 'SDK_PROPERTIES_LIMIT')
    version = re.findall(rb'^Pkg\.Revision\s*=\s*([^\r\n]+)', properties.read_bytes(), re.M)
    require(version == [FIXED['version'].encode()], 'SDK_INSTALLED_REVISION')
    facts.update(CRC_all_entries_verified=True, entries=len(entries), expanded_bytes=FIXED['expanded_bytes'],
                 launcher_ABI='ELF64-little-endian-x86_64', installed_revision=FIXED['version'])


def prepare_owned(*, opener=None):
    """One pre-AVD attempt; an existing namespace is never resumed or deleted."""
    root, owner = hosted_owner()
    root.mkdir()
    identity = root.stat()
    owner_bytes = json.dumps(owner, sort_keys=True).encode()
    result = dict(owner=owner, fixed_version=FIXED['version'], stage='intent', passed=False,
                  automatic_retry=False, download={}, verification={},
                  Android_runtime_APK_or_timeout_verified=False,
                  original_failed_runner_SDK_archive_cause_known=False)
    try:
        with (root / 'owner.json').open('xb') as stream:
            stream.write(owner_bytes)
        result['stage'] = 'download'
        archive = root / 'emulator.zip'
        download(archive, result['download'], opener=opener)
        result['stage'] = 'verify-and-extract'
        target = root / 'runtime'
        verify_and_extract(archive, target, result['verification'])
        archive.unlink()
        result.update(passed=True, stage='prepared-before-AVD', runtime_relative_path=str(target.relative_to(root.parent)),
                      archive_staging_removed=True, owned_runtime_retained_for_this_AVD_only=True)
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is ArchiveError and str(error) in SAFE_CODES else 'SDK_PREPARATION_IO_OR_ZIP_ERROR'
        result['passed'] = False
        partial = root / 'emulator.zip'
        if partial.is_file() and not partial.is_symlink() and partial.resolve() == partial:
            try:
                require(partial.stat().st_size <= FIXED['bytes'], 'SDK_DOWNLOAD_OVERSIZE')
                with partial.open('rb') as stream:
                    result['failed_download_file'] = dict(bytes=partial.stat().st_size,
                        sha256=hashlib.file_digest(stream, 'sha256').hexdigest(), accepted_as_complete=False)
            except Exception:
                result['failed_download_file_metadata_unavailable'] = True
        try:
            current = root.lstat()
            require(root.resolve() == root and not root.is_symlink() and
                    (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'SDK_CLEANUP_OWNER_CHANGED')
            require((root / 'owner.json').read_bytes() == owner_bytes, 'SDK_CLEANUP_OWNER_CHANGED')
            shutil.rmtree(root)
            result['failed_staging_removed'] = True
        except Exception:
            result['failed_staging_removed'] = False
            result['cleanup_unknown_no_retry'] = True
    return result
