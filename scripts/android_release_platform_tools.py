"""Owned Linux platform-tools prepare and descriptor binding; no device/server entry.

The vendor release tag is source provenance, not a reproducible-build attestation.
The one version probe cannot admit a managed server, authentication or Native work.
"""
import base64
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
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
# Linux v6.10 UAPI: WRITE_FILE, REMOVE/MAKE/REFER/TRUNCATE and IOCTL_DEV.
# The version child additionally handles reads and execution, granting only the
# original held SDK file execute access. General command behavior is not covered.
# https://docs.kernel.org/userspace-api/landlock.html
_LANDLOCK_WRITES = (1 << 16) - 1 - (1 << 0) - (1 << 2) - (1 << 3)
_LANDLOCK_VERSION_ACCESS = _LANDLOCK_WRITES | (1 << 0) | (1 << 2) | (1 << 3)
# Fixed Linux v6.10 seccomp/filter/audit UAPI and x86_64 syscall table.
# Landlock denies new read opens; seccomp also denies reads/maps of inherited fd3.
# Neither mechanism proves in-memory key generation or general factory behavior.
_SECCOMP_ALLOW = 0x7fff0000
_SECCOMP_DENY = 0x00050000 | errno.EPERM
_SECCOMP_KILL = 0x80000000
# A bounded self-relocation table may exist in a static image. No interpreter,
# DT_NEEDED, search-path, audit/filter or unrecognized dynamic tag is supported.
_STATIC_DYNAMIC_TAGS = frozenset(range(2, 15)) | frozenset(range(16, 24)) | frozenset(
    (0x6ffffff0, 0x6ffffff9, 0x6ffffffa, 0x6ffffffb, 0x6ffffffc))
_VERSION_INTERPRETER = '/lib64/ld-linux-x86-64.so.2'
_VERSION_INTERPRETER_FILE = '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
_VERSION_DIRECT_LIBRARIES = (
    b'libdl.so.2', b'libpthread.so.0', b'libm.so.6', b'librt.so.1',
    b'libgcc_s.so.1', b'libc.so.6', b'ld-linux-x86-64.so.2',
)
# These tags introduce search paths or additional loader inputs beyond DT_NEEDED.
_VERSION_EXTRA_LOAD_TAGS = (15, 29, 0x6ffffefa, 0x6ffffefb, 0x6ffffefc,
                            0x7ffffffd, 0x7fffffff)


class _SockFilter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint16), ('jt', ctypes.c_uint8),
                ('jf', ctypes.c_uint8), ('k', ctypes.c_uint32)]


class _SockFprog(ctypes.Structure):
    _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(_SockFilter))]


class _LandlockPath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]


class _VersionRuntimeUnsupported(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(ERROR)


def _version_image_segments(raw, *, shared_object=False):
    require(type(raw) is bytes and 64 <= len(raw) <= 64 * 1024 * 1024)
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    ident, kind, machine, version, entry, offset = header[:6]
    require(ident[:7] == b'\x7fELF\x02\x01\x01' and kind in (2, 3)
            and machine == 62 and version == 1 and header[8] == 64
            and header[9] == 56 and 1 <= header[10] <= 128
            and 64 <= offset <= len(raw) - header[10] * 56)
    require(not shared_object or kind == 3)
    executable_entry, interpreters, loads, dynamics = False, [], [], []
    for index in range(header[10]):
        segment, flags, start, address, _, size, memory, _ = struct.unpack_from(
            '<IIQQQQQQ', raw, offset + index * 56)
        require(start <= len(raw) and size <= len(raw) - start)
        if segment == 1:
            require(size <= memory)
            loads.append((start, address, size))
            if flags & 1 and size and (shared_object or address <= entry < address + size):
                executable_entry = True
        elif segment == 2:
            dynamics.append((start, address, size))
        elif segment == 3:
            interpreters.append((start, size))
    require(executable_entry)
    return interpreters, loads, dynamics


def _require_static_version_image(raw):
    """Used on the actual hash-bound SDK fd before any version child setup."""
    interpreters, loads, dynamics = _version_image_segments(raw)
    if interpreters:
        # Kernel open_exec of PT_INTERP needs a separately owned execute/read
        # source. Never silently extend the held-SDK-only execute rule.
        raise _VersionRuntimeUnsupported(1)
    require(len(dynamics) <= 1)
    for start, address, size in dynamics:
        require(16 <= size <= 65536 and size % 16 == 0
                and any(base <= start and start + size <= base + length
                    and address == virtual + start - base for base, virtual, length in loads))
        terminated = False
        for offset in range(start, start + size, 16):
            tag, value = struct.unpack_from('<qQ', raw, offset)
            if terminated:
                require(tag == 0 and value == 0)
            elif tag == 0:
                require(value == 0)
                terminated = True
            elif tag == 1:
                raise _VersionRuntimeUnsupported(2)
            elif tag not in _STATIC_DYNAMIC_TAGS:
                raise _VersionRuntimeUnsupported(3)
        require(terminated)


def _version_dynamic_table(raw, loads, dynamics):
    require(len(dynamics) == 1)
    start, address, size = dynamics[0]
    require(16 <= size <= 65536 and size % 16 == 0
            and any(base <= start and start + size <= base + length
                    and address == virtual + start - base for base, virtual, length in loads))
    entries, strings, sizes = [], [], []
    for offset in range(start, start + size, 16):
        tag, value = struct.unpack_from('<qQ', raw, offset)
        if tag == 0:
            require(value == 0 and not any(raw[offset + 16:start + size]))
            break
        entries.append((tag, value))
        if tag == 5:
            strings.append(value)
        elif tag == 10:
            sizes.append(value)
    else:
        raise RuntimeError(ERROR)
    require(len(strings) == len(sizes) == 1 and 1 <= sizes[0] <= 65536)
    locations = [base + strings[0] - virtual for base, virtual, length in loads
                 if virtual <= strings[0] and strings[0] + sizes[0] <= virtual + length]
    require(len(locations) == 1)
    return entries, raw[locations[0]:locations[0] + sizes[0]]


def _version_dynamic_name(table, offset):
    require(0 < offset < len(table))
    end = table.find(b'\0', offset, min(len(table), offset + 128))
    require(end > offset)
    return table[offset:end]


def _require_fixed_version_runtime(raw):
    interpreters, loads, dynamics = _version_image_segments(raw)
    require(len(interpreters) == 1)
    start, size = interpreters[0]
    require(raw[start:start + size] == _VERSION_INTERPRETER.encode('ascii') + b'\0')
    entries, table = _version_dynamic_table(raw, loads, dynamics)
    needed = tuple(_version_dynamic_name(table, offset) for tag, offset in entries if tag == 1)
    require(needed == _VERSION_DIRECT_LIBRARIES)


def _version_library_dependencies(raw, name, *, loader=False):
    interpreters, loads, dynamics = _version_image_segments(raw, shared_object=not loader)
    # libc can declare an interpreter for direct invocation. Dependencies will
    # be bound for reads only. Reject any other declaration and loader nesting.
    require(not interpreters or (not loader and len(interpreters) == 1 and all(
        raw[start:start + size] == _VERSION_INTERPRETER.encode('ascii') + b'\0'
        for start, size in interpreters)))
    entries, table = _version_dynamic_table(raw, loads, dynamics)
    require(all(tag not in _VERSION_EXTRA_LOAD_TAGS for tag, _ in entries))
    names = [_version_dynamic_name(table, offset) for tag, offset in entries if tag == 14]
    require(names == [name])
    needed = tuple(_version_dynamic_name(table, offset) for tag, offset in entries if tag == 1)
    require(len(needed) == len(set(needed)) and all(
        dependency != name and dependency in _VERSION_DIRECT_LIBRARIES for dependency in needed))
    return needed


def _version_seccomp_filter():
    """A closed default-deny program, not a caller-supplied policy or port."""
    load, equal, bits, ret = 0x20, 0x15, 0x45, 0x06
    allow, deny = (ret, 0, 0, _SECCOMP_ALLOW), (ret, 0, 0, _SECCOMP_DENY)
    program = [(load, 0, 0, 4), (equal, 1, 0, 0xc000003e),
               (ret, 0, 0, _SECCOMP_KILL), (load, 0, 0, 0),
               (bits, 0, 1, 0x40000000), (ret, 0, 0, _SECCOMP_KILL)]
    # Read/memory/loader/exit operations only. No metadata mutations, sockets,
    # process creation, ioctl, ptrace, namespace or policy-changing syscalls.
    for number in (4, 5, 6, 8, 10, 11, 12, 13, 14, 15, 21,
                   24, 25, 28, 35, 39, 60, 63, 79, 89, 97, 99, 102, 104,
                   107, 108, 131, 186, 202, 204, 217, 218, 228, 230, 231,
                   262, 267, 273, 318, 332, 334):
        program.extend(((equal, 0, 1, number), allow))

    def exact(argument, value):
        offset = 16 + argument * 8
        return [(load, 0, 0, offset + 4), (equal, 1, 0, 0), deny,
                (load, 0, 0, offset), (equal, 1, 0, value), deny]

    def branch(number, body):
        require(len(body) <= 255)
        program.extend([(equal, 0, len(body), number), *body])

    # Landlock cannot revoke an already-open readable file. Kernel fd arguments
    # are ints, so also reject fd3 when the unused upper32 bits are nonzero.
    for number, argument in ((0, 0), (17, 0), (9, 4)):
        branch(number, [(load, 0, 0, 16 + argument * 8),
                        (equal, 0, 1, 3), deny, allow])

    # Writes are limited to the two original output pipes. fd3 cannot be closed
    # or replaced before the descriptor-bound exec; duplication is not allowed.
    for number in (1, 20):
        branch(number, [(load, 0, 0, 16), (equal, 2, 0, 1),
                        (equal, 1, 0, 2), deny, allow])
    branch(3, [(load, 0, 0, 16), (equal, 0, 1, 3), deny, allow])
    for number, argument in ((2, 1), (257, 2)):
        # Only O_RDONLY with optional O_CLOEXEC. openat2's pointed-to flags and
        # all other flags are rejected, without interpreting path pointers.
        offset = 16 + argument * 8
        branch(number, [(load, 0, 0, offset + 4), (equal, 1, 0, 0), deny,
                        (load, 0, 0, offset), (bits, 0, 1, 0xfff7ffff), deny, allow])
    branch(72, [(load, 0, 0, 28), (equal, 1, 0, 0), deny,
                (load, 0, 0, 24), (equal, 2, 0, 1), (equal, 1, 0, 3), deny, allow])
    branch(158, [*exact(0, 0x1002), allow])  # ARCH_SET_FS for the dynamic loader.
    branch(302, [*exact(0, 0), *exact(2, 0), allow])  # Read own rlimit only.
    branch(322, [*exact(0, 3), *exact(4, 0x1000), allow])  # AT_EMPTY_PATH, held adb.
    program.append(deny)
    require(len(program) <= 4096)
    return tuple(program)


def _seccomp_api(library):
    """Read-only capability/layout gate before prepare IO; never filter the parent."""
    require(sys.byteorder == 'little' and ctypes.sizeof(ctypes.c_void_p) == 8
            and ctypes.sizeof(_SockFilter) == 8 and ctypes.sizeof(_SockFprog) == 16
            and _SockFprog.filter.offset == 8)
    action = ctypes.c_uint32(0x00050000)  # SECCOMP_RET_ERRNO, no errno data.
    result = library.syscall(317, ctypes.c_uint(2), ctypes.c_uint(0), ctypes.byref(action))
    require(type(result) is int and result == 0 and action.value == 0x00050000)


def _restrict_version_metadata(library):
    """Install on this child, then require denied, non-mutating invalid-fd probes."""
    _seccomp_api(library)
    instructions = _version_seccomp_filter()
    blocks = (_SockFilter * len(instructions))(*(_SockFilter(*v) for v in instructions))
    program = _SockFprog(len(blocks), blocks)
    result = library.syscall(317, ctypes.c_uint(1), ctypes.c_uint(0), ctypes.byref(program))
    require(type(result) is int and result == 0)
    # Invalid fd/null paths cannot change metadata even if installation failed.
    # EPERM (rather than EBADF/EFAULT) is required from the installed filter.
    probes = ((91, (-1, 0)), (93, (-1, -1, -1)),
              (190, (-1, ctypes.c_void_p(), ctypes.c_void_p(), 0, 0)),
              (280, (-1, ctypes.c_void_p(), ctypes.c_void_p(), 0)),
              (2, (ctypes.c_void_p(), 1, 0)),
              (257, (-100, ctypes.c_void_p(), 0x40, 0)),
              (257, (-100, ctypes.c_void_p(), 0x200, 0)))
    for number, arguments in probes:
        ctypes.set_errno(0)
        result = library.syscall(number, *arguments)
        require(type(result) is int and result == -1 and ctypes.get_errno() == errno.EPERM)
    # Zero length/null arguments cannot read SDK bytes or create a mapping even
    # if enforcement is broken. Each must fail at the filter with EPERM.
    null = ctypes.c_void_p()
    for number, arguments in (
            (0, (ctypes.c_int(3), null, ctypes.c_size_t(0))),
            (17, (ctypes.c_int(3), null, ctypes.c_size_t(0), ctypes.c_long(0))),
            (9, (null, ctypes.c_size_t(0), ctypes.c_int(1), ctypes.c_int(2),
                 ctypes.c_int(3), ctypes.c_long(0)))):
        ctypes.set_errno(0)
        result = library.syscall(number, *arguments)
        require(type(result) is int and result == -1 and ctypes.get_errno() == errno.EPERM)


def require(value):
    if not value:
        raise RuntimeError(ERROR) from None


def _landlock_api():
    """Read-only ABI query. Never restrict the preparing parent process."""
    require(sys.platform == 'linux' and os.uname().machine == 'x86_64'
            and sys.version_info >= (3, 13) and os.execve in os.supports_fd
            and sys.byteorder == 'little' and ctypes.sizeof(_LandlockPath) == 12
            and _LandlockPath.parent_fd.offset == 8)
    library = ctypes.CDLL(None, use_errno=True)
    library.syscall.restype = ctypes.c_long
    library.syscall.argtypes = [ctypes.c_long]
    library.prctl.restype = ctypes.c_int
    library.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    library.fcntl.restype = ctypes.c_int
    library.fcntl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    abi = library.syscall(444, ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    require(type(abi) is int and 5 <= abi <= 64)
    return library, abi


def _confined_version_child():
    """Fixed private child entry: fd3 is adb, fd4 was this source; no caller argv."""
    library, _ = _landlock_api()
    require(os.geteuid() != 0)
    # Close Python's script/bootstrap handles before exec. Preserve only readonly
    # stdin and adb plus the two owned output pipes; no writable file capability.
    require(library.syscall(436, ctypes.c_uint(4), ctypes.c_uint(0xffffffff), ctypes.c_uint(0)) == 0)
    require(stat.S_ISCHR(os.fstat(0).st_mode) and os.readlink('/proc/self/fd/0') == '/dev/null')
    for fd in (0, 3):
        flags = library.fcntl(fd, 3, 0)  # F_GETFL
        require(type(flags) is int and flags >= 0 and flags & os.O_ACCMODE == os.O_RDONLY)
    for fd in (1, 2):
        value = os.fstat(fd)
        require(stat.S_ISFIFO(value.st_mode) and value.st_uid == os.geteuid()
                and stat.S_IMODE(value.st_mode) == 0o600)
    value = os.fstat(3)
    require(stat.S_ISREG(value.st_mode) and value.st_uid == os.geteuid()
            and value.st_nlink == 1 and value.st_size == ADB_BYTES)
    sdk_path = Path(os.readlink('/proc/self/fd/3'))
    require(sdk_path.is_absolute() and sdk_path.name == 'adb'
            and sdk_path.parent.name == 'platform-tools')
    rules = ctypes.c_uint64(_LANDLOCK_VERSION_ACCESS)
    rule_fd = library.syscall(444, ctypes.byref(rules), ctypes.c_size_t(8), ctypes.c_uint(0))
    require(type(rule_fd) is int and rule_fd >= 4)
    executable = _LandlockPath(1, 3)  # EXECUTE only; no read permission or directory rule.
    result = library.syscall(445, ctypes.c_int(rule_fd), ctypes.c_int(1),
                             ctypes.byref(executable), ctypes.c_uint(0))
    require(type(result) is int and result == 0)
    require(library.prctl(38, 1, 0, 0, 0) == 0 and library.prctl(39, 0, 0, 0, 0) == 1)
    require(library.syscall(446, ctypes.c_int(rule_fd), ctypes.c_uint(0)) == 0)
    require(os.close(rule_fd) is None)
    # One declared owned destination, absent before spawn. A successful create
    # aborts without SDK exec; the parent retains its unknown/partial lifetime.
    try:
        os.open(sdk_path.parent / '.landlock-write-denial-probe',
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except PermissionError as error:
        require(type(error.errno) is int and error.errno == errno.EACCES)
    else:
        raise RuntimeError(ERROR)
    for path, flags in (
            (sdk_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC),
            (Path('/proc/self/fd/3'), os.O_RDONLY | os.O_CLOEXEC),
            (sdk_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)):
        try:
            os.open(path, flags)
        except PermissionError as error:
            require(type(error.errno) is int and error.errno == errno.EACCES)
        else:
            raise RuntimeError(ERROR)
    os.set_inheritable(3, False)
    _restrict_version_metadata(library)
    os.execve(3, ('/proc/self/fd/3', 'version'), os.environ.copy())
    raise RuntimeError(ERROR)  # An exec that returned is not SDK completion.


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
        self._host = self._host_binding = self._host_stamp = None
        self._probe_programs = []
        self._version_interpreter = None
        self._version_runtime_libraries = None
        self._runtime_pin_attempted = False
        self._runtime_open_unknown = False
        self._phase = 'NEW'

    def _original(self):
        reference = getattr(self, '_origin', None)
        require(type(reference) is weakref.ReferenceType and reference() is self
                and _OWNERS.get(id(self)) is self)

    def _close_fd(self, fd):
        self._fds.remove(fd)  # Never retry a possibly reused descriptor number.
        try:
            require(self._cleanup_call(os.close, fd) is None)
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
            require(not self._cleanup_call(path.is_symlink)
                    and self._cleanup_call(path.resolve) == path
                    and identity(self._cleanup_call(path.lstat)) == before)

    def _cleanup_call(self, function, *args):
        # These are this owner's concrete file operations, never a caller port.
        transferred = self._host is not None and self._sdk._handle_owner is self._host
        if transferred:
            self._host_origin(self._host)
            require(self._state == 'CLOSING' and self._host._state == 'CLOSING')
        value = function(*args)
        if transferred:
            self._host_origin(self._host)
            require(self._state == 'CLOSING' and self._host._state == 'CLOSING')
        return value

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
                current = identity(self._cleanup_call(path.lstat))
                require(current[:4] == before[:4] and not self._cleanup_call(path.is_symlink)
                        and {x.name for x in self._cleanup_call(lambda: tuple(path.iterdir()))} ==
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
        self._phase = 'LANDLOCK_ABI_GATE'
        native, self._landlock_abi = _landlock_api()  # Unsupported before prepare IO.
        self._phase = 'SECCOMP_CAPABILITY_GATE'
        _seccomp_api(native)
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
        self._original()
        require(sdk is self._sdk and sdk._handle_owner is sdk
                and sdk._state in ('READY', 'VERIFYING')
                and self._state == 'BOUND' and self._binding is not None
                and sdk is self._sdk and self._binding[0] is sdk
                and sdk._platform_tools_source is self and sdk._sdk == str(self._root))
        self._guard()
        _, fd, before = self._binding
        require(sdk_identity(os.fstat(fd)) == before and os.get_inheritable(fd) is False)
        require(hashlib.sha256(os.pread(fd, ADB_BYTES + 1, 0)).hexdigest() == ADB_SHA256)

    def _pin_version_runtime(self, start, last):
        self._original()
        require(self._host is None and self._state == 'BOUND'
                and self._phase == 'VERSION_INTERPRETER_BINDING'
                and self._version_interpreter is None and not self._runtime_pin_attempted)
        self._runtime_pin_attempted = True

        def check_budget():
            nonlocal last
            now = time.monotonic_ns()
            require(last <= now <= start + 5_000_000_000)
            last = now

        def open_component(name, parent, flags):
            check_budget()
            self._runtime_open_unknown = True
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | flags,
                             dir_fd=parent)
            except OSError:
                self._runtime_open_unknown = False
                raise RuntimeError(ERROR) from None
            require(type(fd) is int and fd >= 0 and fd not in self._fds
                    and fd not in self._sdk._fds)
            self._fds.append(fd)
            self._runtime_open_unknown = False
            require(fd >= 3 and os.get_inheritable(fd) is False)
            check_budget()
            return fd

        logical, canonical = Path(_VERSION_INTERPRETER), Path(_VERSION_INTERPRETER_FILE)
        check_budget()
        require(logical.resolve(strict=True) == canonical)
        parent = None
        for part in canonical.parts[:-1]:
            parent = open_component(part, parent, os.O_DIRECTORY)
            info = os.fstat(parent)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        def bind_file(path, *, loader=False):
            fd = open_component(path.name, parent, os.O_NONBLOCK)
            info = os.fstat(fd)
            before = identity(info)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and (not loader or info.st_mode & stat.S_IXUSR) and not info.st_mode & 0o7022
                    and 64 <= info.st_size <= 16 * 1024 * 1024)
            check_budget()
            raw = os.pread(fd, info.st_size + 1, 0)
            require(type(raw) is bytes and len(raw) == info.st_size)
            dependencies = _version_library_dependencies(raw, path.name.encode('ascii'), loader=loader)
            digest = hashlib.sha256(raw).hexdigest()
            check_budget()
            require(identity(path.lstat()) == identity(os.fstat(fd)) == before)
            check_budget()
            return fd, before, digest, dependencies

        interpreter = bind_file(canonical, loader=True)
        check_budget()
        require(logical.resolve(strict=True) == canonical)
        self._version_interpreter = interpreter
        self._phase = 'VERSION_RUNTIME_LIBRARY_BINDING'
        libraries = []
        for name in _VERSION_DIRECT_LIBRARIES:
            if name != canonical.name.encode('ascii'):
                path = canonical.with_name(name.decode('ascii'))
                libraries.append((path, bind_file(path)))
        owned = [(canonical, interpreter), *libraries]
        require(len({item[1][1][:2] for item in owned}) == len(owned))
        for path, (fd, before, _, _) in owned:
            check_budget()
            require(identity(path.lstat()) == identity(os.fstat(fd)) == before)
        require(logical.resolve(strict=True) == canonical)
        check_budget()
        self._version_runtime_libraries = tuple(binding for _, binding in libraries)

    def probe_version_once(self):
        require(self._host is None)
        self.verify_sdk(self._sdk)
        require(self._pid is None and not self._child_unknown
                and not self._runtime_pin_attempted)
        self._phase = 'VERSION_PROBE'
        start = last = time.monotonic_ns()
        _, abi = _landlock_api()
        require(abi == self._landlock_abi)
        self._phase = 'VERSION_STATIC_IMAGE_GATE'
        _, sdk_fd, before = self._binding
        raw = os.pread(sdk_fd, ADB_BYTES + 1, 0)
        require(type(raw) is bytes and len(raw) == ADB_BYTES
                and hashlib.sha256(raw).hexdigest() == ADB_SHA256
                and sdk_identity(os.fstat(sdk_fd)) == before)
        try:
            _require_static_version_image(raw)
        except _VersionRuntimeUnsupported as unsupported:
            require(type(unsupported) is _VersionRuntimeUnsupported
                    and type(unsupported.reason) is int and unsupported.reason in (1, 2, 3))
            self._phase = ('VERSION_IMAGE_PT_INTERP_UNSUPPORTED',
                           'VERSION_IMAGE_DT_NEEDED_UNSUPPORTED',
                           'VERSION_IMAGE_DYNAMIC_TAG_UNSUPPORTED')[unsupported.reason - 1]
            if unsupported.reason == 1:
                _require_fixed_version_runtime(raw)
                self._phase = 'VERSION_INTERPRETER_BINDING'
                self._pin_version_runtime(start, last)
                self._phase = 'VERSION_RUNTIME_BOUND_LOADER_FD_LAYOUT_REQUIRED'
            raise RuntimeError(ERROR) from None
        now = time.monotonic_ns()
        require(last <= now <= start + 5_000_000_000)
        last = now
        self._phase = 'VERSION_PROBE'
        programs = []
        for name in (sys.executable, __file__):
            path = Path(name).resolve(strict=True)
            require(path.is_absolute() and not path.is_symlink())
            program_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._fds.append(program_fd)
            require(type(program_fd) is int and program_fd >= 3
                    and os.get_inheritable(program_fd) is False)
            value = os.fstat(program_fd)
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1
                    and value.st_uid in (0, os.geteuid()) and 1 <= value.st_size <= 64 * 1024 * 1024
                    and identity(path.lstat()) == identity(value))
            raw = os.pread(program_fd, value.st_size + 1, 0)
            require(len(raw) == value.st_size and identity(os.fstat(program_fd)) == identity(value))
            programs.append((program_fd, path, identity(value), hashlib.sha256(raw).hexdigest()))
        require(len({v[0] for v in programs} | {self._binding[1]}) == 3
                and programs[0][2][:2] != programs[1][2][:2])
        self._probe_programs = programs
        denial = self._root / 'platform-tools/.landlock-write-denial-probe'
        require(not denial.exists() and not denial.is_symlink())
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
        executable = '/proc/' + str(os.getpid()) + '/fd/' + str(programs[0][0])
        actions = [(os.POSIX_SPAWN_DUP2, null, 0), (os.POSIX_SPAWN_DUP2, out_w, 1),
                   (os.POSIX_SPAWN_DUP2, err_w, 2), (os.POSIX_SPAWN_DUP2, fd, 3),
                   (os.POSIX_SPAWN_DUP2, programs[1][0], 4), (os.POSIX_SPAWN_CLOSEFROM, 5)]
        self.verify_sdk(self._sdk)
        for program_fd, path, before, digest in programs:
            require(identity(os.fstat(program_fd)) == before == identity(path.lstat())
                    and hashlib.sha256(os.pread(program_fd, before[5] + 1, 0)).hexdigest() == digest)
        home = os.environ.get('HOME')
        library = programs[0][1].parent.parent / 'lib'
        require(type(home) is str and Path(home) == self._root.parent
                and library.is_dir() and library.resolve() == library and not library.is_symlink())
        # Keep the actual HOME; do not inherit LD_PRELOAD/AUDIT, Python hooks,
        # credentials or unrelated SDK/server variables into this version child.
        environment = {'HOME': home, 'LANG': 'C', 'LC_ALL': 'C',
                       'PATH': os.defpath, 'LD_LIBRARY_PATH': str(library)}
        now = time.monotonic_ns()
        require(last <= now <= start + 5_000_000_000)
        last = now
        self._child_unknown = True  # Native exception cannot prove that no child was created.
        pid = os.posix_spawn(executable,
            (executable, '-I', '-B', '-S', '/proc/self/fd/4', '_confined_version_child'),
            environment, file_actions=actions)
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
        for program_fd, _, _, _ in programs:
            self._close_fd(program_fd)
        out, err = os.read(out_r, 4097), os.read(err_r, 513)
        require(len(out) <= 4096 and err == b'' and os.read(out_r, 1) == b'')
        lines = out.splitlines()
        require(len(lines) == 4 and lines[0] == b'Android Debug Bridge version 1.0.41'
                and lines[1] == b'Version 35.0.2-12147458'
                and lines[2] == ('Installed as ' + str(self._root / 'platform-tools/adb')).encode()
                and lines[3].startswith(b'Running on Linux ') and len(lines[3]) <= 256)
        self.verify_sdk(self._sdk)
        require(not denial.exists() and not denial.is_symlink())
        now = time.monotonic_ns()
        require(last <= now <= start + 5_000_000_000)
        self._probe = dict(exit_code=0, publisher_build_id=BUILD, bounded_polls=attempt + 1,
            stdout_bytes=len(out), stderr_bytes=0, actual_linux_version_probe=True,
            actual_version_landlock_write_actions_enforced=True, landlock_abi=abi,
            handled_access_fs=_LANDLOCK_VERSION_ACCESS, inherited_writable_regular_files=False,
            actual_version_metadata_syscalls_and_open_flags_enforced=True,
            seccomp_arch='x86_64', seccomp_default='EPERM',
            seccomp_x32_or_other_arch='KILL_PROCESS', seccomp_denial_probes=7,
            actual_version_path_read_and_reopen_denied=True,
            landlock_execute_allowed_only_held_sdk=True, read_path_denial_probes=3,
            inherited_sdk_read_denial_probes=3,
            static_ELF_no_interpreter_or_needed_libraries=True,
            static_ELF_dynamic_table_closed=True,
            isolated_version_environment=True,
            source_script_sha256=programs[1][3], interpreter_sha256=programs[0][3],
            general_no_generation_reopen_endpoint_or_factory_admission=False)

    def _consume_for_host(self, host):
        from android_release_preconfig_host_resources import OriginalPreconfigHostResources, _OWNERS as owners
        self._original()
        require(type(host) is OriginalPreconfigHostResources and host._original(host)
                and owners.get(id(host)) is host and host._registered is True
                and host._handle_owner is host and host._state == 'CONSUMING'
                and host._sdk is self._sdk and host._platform_tools_source is self
                and host._platform_tools_binding is None and self._host is None
                and not self._child_unknown and not self._close_uncertain
                and not self._runtime_pin_attempted)
        self._host = host
        try:
            host._stable()
            self.verify_sdk(self._sdk)  # Actual held adb fd/hash and owned prepare files.
            host._stable()
            self._host_binding = (host, self._sdk, self._binding, self._root,
                                  self._owner, self._source, self._environment)
            self._host_stamp = (tuple(self._fds), tuple(self._created))
            host._platform_tools_binding = self._host_binding
        except BaseException:
            self._state = 'HOST_UNKNOWN'
            raise RuntimeError(ERROR) from None
        # SDK._handle_owner is the single authority point for both owners.

    def _host_origin(self, host):
        from android_release_preconfig_host_resources import OriginalPreconfigHostResources, _OWNERS as owners
        from android_release_preconfig_sdk import OriginalPreconfigSdk
        self._original()
        require(type(host) is OriginalPreconfigHostResources and host._original(host)
                and owners.get(id(host)) is host and host._registered is True
                and host._handle_owner is host and self._host is host
                and host._sdk is self._sdk and type(self._sdk) is OriginalPreconfigSdk
                and self._sdk._handle_owner is host
                and type(self._sdk._state) is str and self._sdk._state == 'TRANSFERRED'
                and host._platform_tools_source is self
                and host._platform_tools_binding is self._host_binding
                and type(self._host_binding) is tuple and len(self._host_binding) == 7)
        require(all(a is b for a, b in zip(self._host_binding,
            (host, self._sdk, self._binding, self._root, self._owner, self._source, self._environment))))

    def _verify_host(self, host):
        """RAM ownership only; no extra file reads, hashes or clock samples."""
        import android_release_preconfig_sdk as source
        self._host_origin(host)
        require(type(self._state) is str and self._state == 'BOUND' and self._child_unknown is False
                and type(self._close_uncertain) is list and not self._close_uncertain
                and self._sdk._registered is True and source._OWNERS.get(id(self._sdk)) is self._sdk
                and type(self._fds) is list and all(type(fd) is int and fd >= 3 for fd in self._fds)
                and type(self._created) is list and all(type(e) is tuple and len(e) == 2
                    and type(e[0]) is type(self._root) and type(e[1]) is tuple and len(e[1]) == 8
                    and all(type(n) is int for n in e[1]) for e in self._created)
                and type(self._host_stamp) is tuple and len(self._host_stamp) == 2
                and type(self._host_stamp[0]) is tuple
                and all(type(fd) is int for fd in self._host_stamp[0])
                and tuple(self._fds) == self._host_stamp[0]
                and type(self._host_stamp[1]) is tuple and len(self._created) == len(self._host_stamp[1])
                and all(a is b for a, b in zip(self._created, self._host_stamp[1])))

    def _close_from_host(self, host):
        self._verify_host(host)
        require(host._state == 'CLOSING' and not host._fds and not host._uncertain
                and not host._auth_fds and not host._listener_fds
                and not host._auth_open_unknown and not host._listener_open_unknown)
        self._state = 'CLOSING'
        self._close_owned_files()
        self._host_origin(host)
        require(host._state == 'CLOSING')
        self._state = 'HOST_CLOSED'  # Keep this source slot until SDK retirement succeeds.

    def _retire_from_host(self, host):
        import android_release_preconfig_sdk as source
        self._host_origin(host)
        require(self._state == 'HOST_CLOSED' and host._state == 'CLOSING'
                and self._sdk._registered is False and source._OWNERS.get(id(self._sdk)) is None
                and not self._fds and not self._created and not self._close_uncertain)
        require(_LOCK.acquire(False))
        try:
            self._host_origin(host)
            require(host._state == 'CLOSING' and self._state == 'HOST_CLOSED')
            del _OWNERS[id(self)]
            self._state = 'CLOSED'
        finally:
            _LOCK.release()

    def close(self):
        self._original()
        require((self._sdk is None or self._sdk._handle_owner is self._sdk)
                and self._state not in ('HOST_UNKNOWN', 'HOST_CLOSED')
                and not self._child_unknown and not self._close_uncertain and not self._runtime_open_unknown
                and (self._sdk is None or self._sdk._state == 'CLOSED'
                or self._sdk._state == 'FAILED' and not self._sdk._fds
                and not self._sdk._registered and not self._sdk._uncertain and not self._sdk._open_unknown))
        self._state = 'CLOSING'
        self._close_owned_files()
        self._state = 'CLOSED'
        require(_LOCK.acquire(False))
        try:
            require(_OWNERS.get(id(self)) is self)
            del _OWNERS[id(self)]
        finally:
            _LOCK.release()

    def _close_owned_files(self):
        self._refresh_directories()
        self._guard()
        while self._fds:
            self._close_fd(self._fds[-1])
        self._version_interpreter = None
        self._version_runtime_libraries = None
        for path, before in reversed(self._created):
            require(identity(self._cleanup_call(path.lstat)) == before
                    and not self._cleanup_call(path.is_symlink))
            if stat.S_ISDIR(before[2]):
                self._cleanup_call(path.rmdir)
            else:
                self._cleanup_call(path.unlink)
            for i, (parent, value) in enumerate(self._created):
                if parent == path.parent and stat.S_ISDIR(value[2]):
                    self._created[i] = (parent, identity(self._cleanup_call(parent.lstat)))
        self._created.clear()
        require(self._root is None or not self._cleanup_call(self._root.exists))


def controlled_verify():
    """One hosted Linux prepare; no AVD, server, key, device or product commands."""
    import android_release_preconfig_sdk as source
    task, sdk, resource = PreparedPlatformTools(), None, None
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
        from android_release_preconfig_host_resources import OriginalPreconfigHostResources
        task._phase = 'HOST_RESOURCE_BINDING'
        resource = OriginalPreconfigHostResources(sdk)
        resource.claim()
        proof.update(passed=True, actual_linux_prepare=True, owner=task._owner,
            artifact=dict(url=ARCHIVE, bytes=ARCHIVE_BYTES, sha256=ARCHIVE_SHA256,
                          version=VERSION, publisher_build_id=BUILD, adb_sha256=ADB_SHA256),
            source_provenance=task._source, api36=task._api36, probe=task._probe,
            same_sdk_descriptor_binding=True, adb_fd_identity=list(task._binding[2]),
            actual_prechild_host_resource_binding=True)
    except BaseException:
        proof['failure'] = ERROR  # No private exception text or arbitrary SDK streams.
        proof['failure_stage'] = task._phase
        if hasattr(task, '_capabilities'):
            proof['host_capabilities'] = task._capabilities
        proof['child_unknown'] = task._child_unknown
        if task._runtime_open_unknown:
            proof['runtime_open_unknown'] = True
    finally:
        try:
            if not task._child_unknown and not task._runtime_open_unknown:
                if resource is not None:
                    if resource._state == 'READY':
                        resource.close()
                    require(resource._state == 'CLOSED' and task._state == 'CLOSED'
                            and sdk._registered is False and not sdk._fds)
                elif sdk is not None:
                    if sdk._state in ('NEW', 'READY', 'CLOSED'):
                        sdk.close()
                    require(not sdk._fds and not sdk._registered and not sdk._uncertain
                            and not sdk._open_unknown)
                if task._owner is not None and task._state != 'CLOSED':
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
    if sys.argv[1:] == ['_confined_version_child']:
        try:
            _confined_version_child()
        except BaseException:
            os._exit(91)  # No private exception or wrapper stderr export.
    require(sys.argv[1:] == ['verify'])
    # SDK verification imports this canonical module to check exact owner types.
    # Construct its owner here, rather than a distinct __main__ class instance.
    from android_release_platform_tools import controlled_verify as verify
    raise SystemExit(verify())
