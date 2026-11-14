"""Owned Linux platform-tools prepare and descriptor binding; no device/server entry.

The vendor release tag is source provenance, not a reproducible-build attestation.
The one version probe cannot admit a managed server, authentication or Native work.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
import time
import urllib.request
import weakref
import zipfile

VERSION = '35.0.2'
BUILD = '12147458'
ARCHIVE = 'https://dl.google.com/android/repository/platform-tools_r35.0.2-linux.zip'
ARCHIVE_BYTES = 7472902
ARCHIVE_SHA256 = 'acfdcccb123a8718c46c46c059b2f621140194e5ec1ac9d81715be3d6ab6cd0a'
ADB_BYTES = 7951672
ADB_SHA256 = 'c46d072d4b4ba6d2f961ef6d0f01ffc35ed8093f1c263296659ebf56bccfc777'
SOURCE_REVISION = 'ce9ea51f30f69f7560db692f7cb4d5d5502c3653'
SOURCE_TREE = 'd7fd6a6ebdc3409c59bdcbfdee82f887e07488dc'
SOURCE_BASE = 'https://android.googlesource.com/platform/packages/modules/adb/+/'
SOURCE_TAG = SOURCE_BASE + 'refs/tags/platform-tools-35.0.2?format=JSON'
SOURCE_COMMANDLINE = SOURCE_BASE + SOURCE_REVISION + '/client/commandline.cpp?format=TEXT'
SOURCE_COMMANDLINE_SHA256 = '25860fdc31b5759067700f1548841e87feb403b7217529336eaaf3e54118439b'
FILES = {
    'NOTICE.txt': 1107973, 'adb': ADB_BYTES, 'etc1tool': 305104,
    'fastboot': 2543392, 'hprof-conv': 13392, 'make_f2fs': 264440,
    'make_f2fs_casefold': 264424, 'mke2fs': 990840, 'mke2fs.conf': 1157,
    'source.properties': 38, 'sqlite3': 3433480, 'lib64/libc++.so': 1463416,
}
_OWNERS = {}
_LOCK = threading.Lock()
ERROR = 'SIGNED_PLATFORM_TOOLS_PREPARE_UNAVAILABLE'


def require(value):
    if not value:
        raise RuntimeError(ERROR) from None


def closed_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result)
            result[k] = v
        return result
    def constant(_):
        raise RuntimeError(ERROR)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError(ERROR)


def fetch(url, limit, exact=None):
    """One normal TLS request, bounded immutable RAM buffer; no retry or redirect."""
    start = last = time.monotonic_ns()
    request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
    chunks, count = [], 0
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
        require(type(response.status) is int and response.status == 200 and response.geturl() == url)
        require(response.headers.get('Content-Encoding') in (None, 'identity')
                and response.headers.get('Content-Range') is None)
        if exact is not None:
            require(response.headers.get('Content-Length') == str(exact))
        while True:
            block = response.read(min(1024 * 1024, limit + 1 - count))
            now = time.monotonic_ns()
            require(last <= now <= start + 120_000_000_000)
            last = now
            if not block:
                break
            count += len(block)
            require(count <= limit)
            chunks.append(block)
    require(exact is None or count == exact)
    return b''.join(chunks)


def verify_source(tag_raw, commandline_encoded):
    require(tag_raw.startswith(b")]}'\n"))
    tag = closed_json(tag_raw[5:])
    require(type(tag) is dict and tag.get('commit') == SOURCE_REVISION and tag.get('tree') == SOURCE_TREE)
    raw = base64.b64decode(commandline_encoded, validate=False)
    require(len(raw) == 84919 and hashlib.sha256(raw).hexdigest() == SOURCE_COMMANDLINE_SHA256)
    require(b'!strcmp(argv[0], "--version") || !strcmp(argv[0], "version")' in raw
            and b'fprintf(stdout, "%s", adb_version().c_str());\n        return 0;' in raw)
    return {'kind': 'official-adb-release-tag', 'tag': 'platform-tools-35.0.2',
            'revision': SOURCE_REVISION, 'tree': SOURCE_TREE,
            'tag_url': SOURCE_TAG, 'tag_response_sha256': hashlib.sha256(tag_raw).hexdigest(),
            'commandline_url': SOURCE_COMMANDLINE, 'commandline_sha256': SOURCE_COMMANDLINE_SHA256,
            'binary_reproducible_build_or_full_transitive_manifest_verified': False}


def verify_archive(raw):
    require(type(raw) is bytes and len(raw) == ARCHIVE_BYTES
            and hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA256)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        entries = bundle.infolist()
        require(len(entries) == len(FILES))
        found = set()
        for entry in entries:
            name = entry.orig_filename
            require(name == entry.filename and name.startswith('platform-tools/')
                    and PurePosixPath(name).as_posix() == name and '\\' not in name)
            relative = name[len('platform-tools/'):]
            require(relative in FILES and relative not in found and not entry.is_dir()
                    and entry.file_size == FILES[relative] and entry.compress_type in (0, 8)
                    and not entry.flag_bits & 1 and entry.create_system == 3
                    and entry.external_attr >> 16 == stat.S_IFREG | (0o644 if relative in
                        ('NOTICE.txt', 'mke2fs.conf', 'source.properties') else 0o755))
            found.add(relative)
        require(found == set(FILES) and bundle.testzip() is None)
        data = {name: bundle.read('platform-tools/' + name) for name in FILES}
    require(data['source.properties'] == b'Pkg.UserSrc=false\nPkg.Revision=35.0.2\n')
    adb = data['adb']
    require(adb[:7] == b'\x7fELF\x02\x01\x01' and adb[18:20] == b'\x3e\x00'
            and hashlib.sha256(adb).hexdigest() == ADB_SHA256
            and b'\0' + BUILD.encode() + b'\0' in adb)
    return data


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def sdk_identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class PreparedPlatformTools:
    """Actual downloader/file owner. A receipt, dict or copied object cannot bind SDK fds."""
    def __init__(self):
        self._origin = weakref.ref(self)
        self._state = 'NEW'
        self._root = self._owner = self._environment = self._source = self._binding = None
        self._created, self._fds = [], []
        self._close_uncertain = []
        self._pid = self._pidfd = None
        self._child_unknown = False
        self._sdk = None
        self._phase = 'NEW'

    def _original(self):
        reference = getattr(self, '_origin', None)
        require(type(reference) is weakref.ReferenceType and reference() is self
                and _OWNERS.get(id(self)) is self)

    def _close_fd(self, fd):
        self._fds.remove(fd)  # Never retry a possibly reused descriptor number.
        try:
            require(os.close(fd) is None)
        except BaseException:
            self._close_uncertain.append(fd)
            raise RuntimeError(ERROR) from None

    def _guard(self):
        self._original()
        require(self._state in ('PREPARING', 'READY', 'BOUND', 'CLOSING'))
        require(os.getpid() == self._owner['parent_pid'] and os.geteuid() == self._owner['uid'])
        require(tuple(os.environ.get(k) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA'))
                == (self._owner['run'], self._owner['attempt'], self._owner['source']))
        for path, before in self._created:
            require(not path.is_symlink() and path.resolve() == path
                    and identity(path.lstat()) == before)

    def _directory(self, path):
        require(not path.exists() and not path.is_symlink() and path.parent.resolve() == path.parent)
        path.mkdir(mode=0o700)
        self._created.append((path, identity(path.lstat())))

    def _write(self, path, raw, executable=False):
        require(path.parent.resolve() == path.parent and not path.exists() and not path.is_symlink())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self._fds.append(fd)
        self._created.append((path, identity(os.fstat(fd))))
        try:
            require(os.write(fd, raw) == len(raw))
            os.fsync(fd)
            os.fchmod(fd, 0o500 if executable else 0o400)
            self._created[-1] = (path, identity(os.fstat(fd)))
        finally:
            current = os.fstat(fd)
            require(identity(path.lstat()) == identity(current)
                    and identity(current)[:2] == self._created[-1][1][:2])
            self._created[-1] = (path, identity(current))
            self._close_fd(fd)

    def _refresh_directories(self):
        # Directory timestamps change only during this owner's explicit creation.
        updated = []
        for path, before in self._created:
            if stat.S_ISDIR(before[2]):
                current = identity(path.lstat())
                require(current[:4] == before[:4] and not path.is_symlink()
                        and {x.name for x in path.iterdir()} ==
                            {p.name for p, _ in self._created if p.parent == path})
                updated.append((path, current))
            else:
                updated.append((path, before))
        self._created = updated

    def prepare(self):
        self._phase = 'HOST_CAPABILITIES'
        self._capabilities = dict(linux=sys.platform == 'linux',
            x86_64=sys.platform == 'linux' and os.uname().machine == 'x86_64',
            python313=sys.version_info >= (3, 13),
            native_closefrom=hasattr(os, 'POSIX_SPAWN_CLOSEFROM'),
            pidfd=callable(getattr(os, 'pidfd_open', None)))
        require(self._state == 'NEW' and all(self._capabilities.values()))
        require(_LOCK.acquire(False))
        try:
            require(not _OWNERS)
            _OWNERS[id(self)] = self
        finally:
            _LOCK.release()
        self._state = 'PREPARING'
        self._phase = 'HOST_ENVIRONMENT'
        env = tuple(os.environ.get(k) for k in ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS',
                    'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'GITHUB_WORKSPACE',
                    'ANDROID_HOME', 'ANDROID_SDK_ROOT'))
        require(env[:3] == ('true', 'github-hosted', 'Linux')
                and re.fullmatch(r'[1-9][0-9]{0,19}', env[3] or '') is not None
                and re.fullmatch(r'[1-9][0-9]{0,3}', env[4] or '') is not None
                and re.fullmatch(r'[0-9a-f]{40}', env[5] or '') is not None
                and type(env[6]) is str and type(env[7]) is str)
        workspace, sdk = Path(env[6]), Path(env[7])
        self._phase = 'HOST_SOURCE_PATHS'
        for path in (workspace, sdk):
            require(path.is_absolute() and path.is_dir() and path.resolve() == path and not path.is_symlink())
            for parent in (path, *path.parents):
                require(not parent.is_symlink())
        require(env[8] is None or env[8] == env[7])
        boot = Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()
        require(re.fullmatch(r'[0-9a-f-]{36}', boot) is not None)
        self._environment = env
        self._owner = dict(run=env[3], attempt=env[4], source=env[5], uid=os.geteuid(),
                           boot_id=boot, parent_pid=os.getpid())
        self._phase = 'OWNED_HOME_PARENT'
        parent = Path(os.environ.get('HOME', ''))
        require(parent.is_absolute() and parent.is_dir() and parent.resolve() == parent
                and parent.stat().st_uid == os.geteuid())
        for path in (parent, *parent.parents):
            require(not path.is_symlink() and not path.stat().st_mode & 0o7022)
        self._root = parent / ('opennexus-platform-tools-prepare-' + env[3] + '-' + env[4])
        self._directory(self._root)
        self._phase = 'SOURCE_PROVENANCE'
        self._source = verify_source(fetch(SOURCE_TAG, 8192), fetch(SOURCE_COMMANDLINE, 120000))
        self._phase = 'FIXED_ARCHIVE'
        raw = fetch(ARCHIVE, ARCHIVE_BYTES, ARCHIVE_BYTES)
        files = verify_archive(raw)
        self._phase = 'OWNED_INSTALL'
        self._directory(self._root / 'platform-tools')
        self._directory(self._root / 'platform-tools/lib64')
        for name, data in files.items():
            self._write(self._root / 'platform-tools' / name, data,
                        executable=name not in ('NOTICE.txt', 'mke2fs.conf', 'source.properties'))
        self._directory(self._root / 'platforms')
        self._directory(self._root / 'platforms/android-36')
        self._phase = 'API36_COPY'
        jar = sdk / 'platforms/android-36/android.jar'
        properties = jar.parent / 'source.properties'
        require(properties.resolve() == properties and not properties.is_symlink()
                and properties.stat().st_size <= 16384)
        properties_raw = properties.read_bytes()
        require(re.findall(rb'^AndroidVersion.ApiLevel\s*=\s*([^\r\n]+)', properties_raw, re.M) == [b'36'])
        fd = os.open(jar, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self._fds.append(fd)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 4 <= before.st_size <= 64 * 1024 * 1024 and jar.resolve() == jar)
            data = os.pread(fd, before.st_size + 1, 0)
            require(len(data) == before.st_size and data[:4] == b'PK\x03\x04'
                    and identity(os.fstat(fd)) == identity(before) == identity(jar.lstat()))
            self._write(self._root / 'platforms/android-36/android.jar', data)
            self._api36 = dict(source_identity=list(identity(before)), bytes=len(data),
                               sha256=hashlib.sha256(data).hexdigest(),
                               properties_sha256=hashlib.sha256(properties_raw).hexdigest())
        finally:
            self._close_fd(fd)
        self._refresh_directories()
        self._guard()
        self._state = 'READY'
        self._phase = 'SDK_READY'

    def bind_sdk(self, sdk):
        import android_release_preconfig_sdk as source
        self._original()
        self._phase = 'SDK_DESCRIPTOR_BINDING'
        require(self._state == 'READY' and type(sdk) is source.OriginalPreconfigSdk
                and source._OWNERS.get(id(sdk)) is sdk and sdk._state == 'READY'
                and sdk._handle_owner is sdk and sdk._sdk == str(self._root)
                and sdk._platform_tools_source is None)
        self._guard()
        matches = [(fd, expected) for _, _, fd, expected, kind in sdk._edges if kind == 'adb']
        require(len(matches) == 1)
        fd, before = matches[0]
        raw = os.pread(fd, ADB_BYTES + 1, 0)
        require(len(raw) == ADB_BYTES and hashlib.sha256(raw).hexdigest() == ADB_SHA256
                and sdk_identity(os.fstat(fd)) == before)
        self._binding = (sdk, fd, before)
        self._sdk = sdk
        sdk._platform_tools_source = self
        self._state = 'BOUND'
        self.verify_sdk(sdk)

    def verify_sdk(self, sdk):
        self._guard()
        require(self._state == 'BOUND' and self._binding is not None
                and sdk is self._sdk and self._binding[0] is sdk
                and sdk._platform_tools_source is self and sdk._sdk == str(self._root))
        _, fd, before = self._binding
        require(sdk_identity(os.fstat(fd)) == before and os.get_inheritable(fd) is False)
        require(hashlib.sha256(os.pread(fd, ADB_BYTES + 1, 0)).hexdigest() == ADB_SHA256)

    def probe_version_once(self):
        self.verify_sdk(self._sdk)
        require(self._pid is None and not self._child_unknown)
        self._phase = 'VERSION_PROBE'
        null = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
        self._fds.append(null)
        out_r, out_w = os.pipe2(os.O_CLOEXEC)
        self._fds.extend((out_r, out_w))
        err_r, err_w = os.pipe2(os.O_CLOEXEC)
        self._fds.extend((err_r, err_w))
        for f in (out_r, out_w, err_r, err_w):
            value = os.fstat(f)
            require(stat.S_ISFIFO(value.st_mode) and value.st_uid == os.geteuid()
                    and stat.S_IMODE(value.st_mode) == 0o600 and os.get_inheritable(f) is False)
        os.set_blocking(out_r, False)
        os.set_blocking(err_r, False)
        fd = self._binding[1]
        executable = '/proc/' + str(os.getpid()) + '/fd/' + str(fd)
        actions = [(os.POSIX_SPAWN_DUP2, null, 0), (os.POSIX_SPAWN_DUP2, out_w, 1),
                   (os.POSIX_SPAWN_DUP2, err_w, 2), (os.POSIX_SPAWN_CLOSEFROM, 3)]
        start = last = time.monotonic_ns()
        self._child_unknown = True  # Native exception cannot prove that no child was created.
        pid = os.posix_spawn(executable, (executable, 'version'), os.environ.copy(), file_actions=actions)
        self._pid = pid  # Strong owner slot before any post-spawn operation.
        require(type(pid) is int and pid > 0)
        self._pidfd = os.pidfd_open(pid, 0)
        self._fds.append(self._pidfd)
        require(type(self._pidfd) is int and self._pidfd >= 3)
        for f in (null, out_w, err_w):
            self._close_fd(f)
        for attempt in range(101):
            now = time.monotonic_ns()
            require(last <= now <= start + 5_000_000_000)
            last = now
            value = os.waitpid(pid, os.WNOHANG)
            require(type(value) is tuple and len(value) == 2 and all(type(x) is int for x in value))
            if value[0] == pid:
                require(os.WIFEXITED(value[1]) and os.WEXITSTATUS(value[1]) == 0)
                self._child_unknown = False
                break
            require(value == (0, 0) and attempt < 100)
            time.sleep(min(.05, (start + 5_000_000_000 - now) / 1_000_000_000))
        require(not self._child_unknown)
        out, err = os.read(out_r, 4097), os.read(err_r, 513)
        require(len(out) <= 4096 and err == b'' and os.read(out_r, 1) == b'')
        lines = out.splitlines()
        require(len(lines) == 4 and lines[0] == b'Android Debug Bridge version 1.0.41'
                and lines[1] == b'Version 35.0.2-12147458'
                and lines[2] == ('Installed as ' + str(self._root / 'platform-tools/adb')).encode()
                and lines[3].startswith(b'Running on Linux ') and len(lines[3]) <= 256)
        self.verify_sdk(self._sdk)
        self._probe = dict(exit_code=0, publisher_build_id=BUILD, bounded_polls=attempt + 1,
                           stdout_bytes=len(out), stderr_bytes=0, actual_linux_version_probe=True)

    def close(self):
        self._original()
        require(not self._child_unknown and not self._close_uncertain
                and (self._sdk is None or self._sdk._state == 'CLOSED'
                or self._sdk._state == 'FAILED' and not self._sdk._fds
                and not self._sdk._registered and not self._sdk._uncertain and not self._sdk._open_unknown))
        self._state = 'CLOSING'
        self._refresh_directories()
        self._guard()
        while self._fds:
            self._close_fd(self._fds[-1])
        for path, before in reversed(self._created):
            require(identity(path.lstat()) == before and not path.is_symlink())
            if stat.S_ISDIR(before[2]):
                path.rmdir()
            else:
                path.unlink()
            for i, (parent, value) in enumerate(self._created):
                if parent == path.parent and stat.S_ISDIR(value[2]):
                    self._created[i] = (parent, identity(parent.lstat()))
        self._created.clear()
        require(self._root is None or not self._root.exists())
        self._state = 'CLOSED'
        require(_LOCK.acquire(False))
        try:
            require(_OWNERS.get(id(self)) is self)
            del _OWNERS[id(self)]
        finally:
            _LOCK.release()


def controlled_verify():
    """One hosted Linux prepare; no AVD, server, key, device or product commands."""
    import android_release_preconfig_sdk as source
    task, sdk = PreparedPlatformTools(), None
    original = {k: os.environ.get(k) for k in ('ANDROID_HOME', 'ANDROID_SDK_ROOT')}
    proof = dict(passed=False, actual_linux_prepare=False, source_provenance=None,
                 factory_Native_ACK_A0_A6=False, server_device_key_commands_requested=False,
                 SDK_internal_write_or_no_generation_enforced=False,
                 cleanup_complete=False, child_unknown=False)
    try:
        task.prepare()
        os.environ['ANDROID_HOME'] = os.environ['ANDROID_SDK_ROOT'] = str(task._root)
        sdk = source.OriginalPreconfigSdk()
        sdk.pin()
        task.bind_sdk(sdk)
        sdk.verify()
        task.probe_version_once()
        proof.update(passed=True, actual_linux_prepare=True, owner=task._owner,
            artifact=dict(url=ARCHIVE, bytes=ARCHIVE_BYTES, sha256=ARCHIVE_SHA256,
                          version=VERSION, publisher_build_id=BUILD, adb_sha256=ADB_SHA256),
            source_provenance=task._source, api36=task._api36, probe=task._probe,
            same_sdk_descriptor_binding=True, adb_fd_identity=list(task._binding[2]))
    except BaseException:
        proof['failure'] = ERROR  # No private exception text or arbitrary SDK streams.
        proof['failure_stage'] = task._phase
        if hasattr(task, '_capabilities'):
            proof['host_capabilities'] = task._capabilities
        proof['child_unknown'] = task._child_unknown
    finally:
        try:
            if not task._child_unknown:
                if sdk is not None:
                    if sdk._state in ('NEW', 'READY', 'CLOSED'):
                        sdk.close()
                    require(not sdk._fds and not sdk._registered and not sdk._uncertain
                            and not sdk._open_unknown)
                if task._owner is not None:
                    task.close()
                proof['cleanup_complete'] = True
        except BaseException:
            proof['passed'] = False
            proof['cleanup_failure'] = ERROR
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    workspace = Path(os.environ['GITHUB_WORKSPACE'])
    target = workspace / '.build/platform-tools-prepare-proof.json'
    require(target.parent.resolve() == target.parent and not target.exists() and not target.is_symlink())
    with target.open('x', encoding='utf-8') as output:
        json.dump(proof, output, sort_keys=True, indent=2)
        output.write('\n')
    print('PLATFORM_TOOLS_PREPARE_OK' if proof['passed'] and proof['cleanup_complete'] else 'PLATFORM_TOOLS_PREPARE_FAILED')
    return 0 if proof['passed'] and proof['cleanup_complete'] else 1


if __name__ == '__main__':
    require(sys.argv[1:] == ['verify'])
    # SDK verification imports this canonical module to check exact owner types.
    # Construct its owner here, rather than a distinct __main__ class instance.
    from android_release_platform_tools import controlled_verify as verify
    raise SystemExit(verify())
