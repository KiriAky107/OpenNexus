"""Original host SDK descriptors for the private preconfig command factory.

This pre-child owner derives its location from the running hosted environment,
not caller metadata. It does not construct commands, own an ADB server, attest
SDK artifact bytes by default, admit a device or transfer handles to a child.
An actual fixed platform-tools prepare can bind and recheck these same fds.
That optional binding is not managed-server or device admission. An original
host resource owner can consume its pre-child close authority. Unknown opens
and uncertain closes retain their strong owner without automatic cleanup.
"""
import os
import re
import stat
import sys
import threading
import weakref

_ERROR = 'SIGNED_PRECONFIG_SDK_UNAVAILABLE'
_OWNERS = {}
_LOCK = threading.Lock()
_ENV = ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'GITHUB_RUN_ID',
        'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'ANDROID_HOME', 'ANDROID_SDK_ROOT')


class OriginalPreconfigSdk:
    def __init__(self):
        self._origin = weakref.ref(self)
        self._handle_owner = self
        self._state = 'NEW'
        self._fds, self._edges, self._aliases, self._uncertain = [], [], [], []
        self._registered = self._releasing = self._open_unknown = False
        self._inflight = 0
        self._context = self._sdk = self._parent_pid = self._uid = None
        self._platform_tools_source = None

    def _issued(self):
        reference = getattr(self, '_origin', None)
        if type(reference) is not weakref.ReferenceType or reference() is not self:
            self._state = 'FAILED'
            self._fds, self._edges, self._aliases = [], [], []
            self._registered = False
            raise RuntimeError(_ERROR) from None
        if self._handle_owner is not self:
            raise RuntimeError(_ERROR) from None

    def _reserve(self):
        self._need(_LOCK.acquire(False) is True)
        try:
            self._need(not _OWNERS)
            _OWNERS[id(self)] = self
            self._registered = True
        finally:
            _LOCK.release()

    def _drop(self):
        if not self._registered:
            return
        if not _LOCK.acquire(False):
            return
        try:
            if _OWNERS.get(id(self)) is self:
                del _OWNERS[id(self)]
                self._registered = False
        finally:
            _LOCK.release()

    def _release(self):
        if (self._handle_owner is not self or self._inflight or self._releasing
                or self._uncertain or self._open_unknown):
            return
        self._releasing = True
        try:
            while self._fds:
                fd = self._fds.pop()  # Never retry a possibly reused number.
                try:
                    known = os.close(fd) is None
                except BaseException:
                    known = False
                if not known:
                    self._uncertain.append(fd)
                    return
            if not self._uncertain and not self._open_unknown:
                self._drop()
        finally:
            self._releasing = False

    def _fail(self):
        self._state = 'FAILED'
        self._release()  # Only original pre-child descriptors exist here.
        raise RuntimeError(_ERROR) from None

    def _need(self, value):
        if not value:
            self._fail()

    def _step(self, function, *args, **kwargs):
        phase = self._state
        self._need(phase in ('PINNING', 'VERIFYING'))
        self._inflight += 1
        try:
            value = function(*args, **kwargs)
        finally:
            self._inflight -= 1
        self._need(self._state == phase)
        return value

    def _capabilities(self):
        self._need(sys.platform == 'linux' and os.name == 'posix'
            and all(type(getattr(os, n, None)) is int and getattr(os, n) > 0
                for n in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC', 'O_NONBLOCK'))
            and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.stat in os.supports_follow_symlinks
            and all(callable(getattr(os, n, None)) for n in
                ('geteuid', 'getpid', 'pread', 'fstat', 'get_inheritable', 'readlink', 'close')))

    def _environment(self):
        # Read only selected nonsecret source fields; never copy/export host env.
        values = []
        for index, key in enumerate(_ENV):
            value = self._step(os.environ.get, key)
            self._need(type(value) is str or index == 7 and value is None)
            if index < 3:
                self._need(value == ('true', 'github-hosted', 'Linux')[index])
            elif index < 6:
                self._need(re.fullmatch((r'[1-9][0-9]{0,19}', r'[1-9][0-9]{0,3}',
                    r'[0-9a-f]{40}')[index - 3], value) is not None)
            elif index == 6:
                self._need(1 < len(value) <= 512 and value.startswith('/') and not value.endswith('/'))
                parts = value[1:].split('/')
                self._need(1 <= len(parts) <= 32 and all(x not in ('.', '..')
                    and re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', x) is not None for x in parts))
            else:
                self._need(value is None or value == values[6])
            values.append(value)
        return tuple(values)

    def _identity(self, value, kind):
        self._need(type(value) is os.stat_result)
        identity = (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
                    value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        self._need(all(type(v) is int and v >= 0 for v in identity)
            and value.st_uid in (0, self._uid) and value.st_nlink > 0
            and not value.st_mode & 0o7022)
        if kind == 'directory':
            self._need(stat.S_ISDIR(value.st_mode))
        else:
            self._need(stat.S_ISREG(value.st_mode) and value.st_uid == self._uid
                and value.st_nlink == 1)
            if kind == 'adb':
                self._need(value.st_mode & 0o100 and 20 <= value.st_size <= 128 * 1024 * 1024)
            else:
                self._need(kind == 'api36' and 4 <= value.st_size <= 256 * 1024 * 1024)
        return identity

    def _open(self, name, parent, kind):
        kwargs = {} if parent is None else {'dir_fd': parent}
        expected = self._identity(self._step(os.stat, name, follow_symlinks=False, **kwargs), kind)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        flags |= os.O_DIRECTORY if kind == 'directory' else os.O_NONBLOCK
        self._need(self._state == 'PINNING' and len(self._fds) < 40)
        self._inflight += 1
        self._open_unknown = True
        try:
            fd = os.open(name, flags, **kwargs)
            self._need(type(fd) is int and fd >= 0 and fd not in self._fds)
            self._fds.append(fd)  # Retain actual returned fd BEFORE post-open guards.
            self._open_unknown = False
        finally:
            self._inflight -= 1
        self._need(self._state == 'PINNING' and fd >= 3)
        actual = self._identity(self._step(os.fstat, fd), kind)
        self._need(actual == expected and self._step(os.get_inheritable, fd) is False
            and all(edge[3][:2] != actual[:2] for edge in self._edges))
        self._edges.append((parent, name, fd, expected, kind))
        return fd

    def _verify(self):
        self._need(self._environment() == self._context)
        for parent, name, fd, expected, kind in self._edges:
            kwargs = {} if parent is None else {'dir_fd': parent}
            current = self._identity(self._step(os.stat, name, follow_symlinks=False, **kwargs), kind)
            self._need(current == expected
                and self._identity(self._step(os.fstat, fd), kind) == expected
                and self._step(os.get_inheritable, fd) is False)
        pid = self._step(os.getpid)
        self._need(type(pid) is int and pid == self._parent_pid)
        for alias, path, identity in self._aliases:
            target = self._step(os.readlink, alias)
            self._need(type(target) is str and target == path
                and self._identity(self._step(os.stat, alias),
                    'adb' if path.endswith('/adb') else 'api36') == identity)
        if self._platform_tools_source is not None:
            from android_release_platform_tools import PreparedPlatformTools
            self._need(type(self._platform_tools_source) is PreparedPlatformTools)
            self._platform_tools_source.verify_sdk(self)

    def pin(self):
        self._issued()
        self._need(self._state == 'NEW')
        self._state = 'PINNING'
        try:
            self._capabilities()  # Unsupported host: no env/FS/registry effects.
            self._reserve()  # Strong original owner before any descriptor opens.
            self._context = self._environment()
            self._sdk = self._context[6]
            self._uid, self._parent_pid = self._step(os.geteuid), self._step(os.getpid)
            self._need(type(self._uid) is int and self._uid >= 0
                and type(self._parent_pid) is int and self._parent_pid > 0)
            parent = self._open('/', None, 'directory')
            for name in self._sdk[1:].split('/'):
                parent = self._open(name, parent, 'directory')
            for parts, kind, header in ((('platform-tools', 'adb'), 'adb', b'\x7fELF\x02\x01\x01'),
                    (('platforms', 'android-36', 'android.jar'), 'api36', b'PK\x03\x04')):
                directory = parent
                for name in parts[:-1]:
                    directory = self._open(name, directory, 'directory')
                fd = self._open(parts[-1], directory, kind)
                raw = self._step(os.pread, fd, 20 if kind == 'adb' else 4, 0)
                self._need(type(raw) is bytes and len(raw) == (20 if kind == 'adb' else 4)
                    and raw.startswith(header) and (kind != 'adb' or raw[18:20] == b'\x3e\x00'))
                path = self._sdk + '/' + '/'.join(parts)
                self._aliases.append(('/proc/' + str(self._parent_pid) + '/fd/' + str(fd),
                                      path, self._edges[-1][3]))
            self._verify()
            self._state = 'READY'
        except BaseException:
            self._fail()

    def verify(self):
        self._issued()
        self._need(self._state == 'READY')
        self._state = 'VERIFYING'
        try:
            self._verify()
            self._state = 'READY'
        except BaseException:
            self._fail()

    def close(self):
        self._issued()
        if self._state == 'CLOSED':
            return
        self._need(self._state in ('NEW', 'READY'))
        self._state = 'CLOSING'
        self._release()
        self._need(self._state == 'CLOSING' and not self._fds and not self._uncertain
            and not self._open_unknown and not self._registered)
        self._state = 'CLOSED'

    def _consume_for_host(self, host):
        from android_release_preconfig_host_resources import OriginalPreconfigHostResources, _OWNERS as _HOST_OWNERS
        self._issued()
        if (type(host) is not OriginalPreconfigHostResources
                or not OriginalPreconfigHostResources._original(host)
                or _HOST_OWNERS.get(id(host)) is not host or host._state != 'CONSUMING'
                or host._sdk is not self):
            raise RuntimeError(_ERROR) from None
        host._stable()
        if not _LOCK.acquire(False):
            raise RuntimeError(_ERROR) from None
        try:
            if (_HOST_OWNERS.get(id(host)) is not host or self._state != 'READY'
                    or _OWNERS.get(id(self)) is not self or self._registered is not True
                    or self._handle_owner is not self):
                raise RuntimeError(_ERROR) from None
            host._stable()
            vacant_fds, vacant_edges, vacant_aliases = [], [], []
            host._fds, host._edges, host._aliases = self._fds, self._edges, self._aliases
            self._handle_owner = host  # Original refs precede this authority move.
            self._fds, self._edges, self._aliases = vacant_fds, vacant_edges, vacant_aliases
            self._state = 'TRANSFERRED'
            # Keep the original SDK slot reserved while the host owns its fds.
        finally:
            _LOCK.release()

    def _retire_host_owner(self, host):
        from android_release_preconfig_host_resources import OriginalPreconfigHostResources, _OWNERS as _HOST_OWNERS
        if (type(host) is not OriginalPreconfigHostResources
                or not OriginalPreconfigHostResources._original(host)
                or _HOST_OWNERS.get(id(host)) is not host or host._state != 'CLOSING'
                or host._sdk is not self or self._handle_owner is not host
                or self._state != 'TRANSFERRED' or host._fds or host._uncertain):
            raise RuntimeError(_ERROR) from None
        if not _LOCK.acquire(False):
            raise RuntimeError(_ERROR) from None
        try:
            if _OWNERS.get(id(self)) is not self or self._registered is not True:
                raise RuntimeError(_ERROR) from None
            del _OWNERS[id(self)]
            self._registered = False
        finally:
            _LOCK.release()
