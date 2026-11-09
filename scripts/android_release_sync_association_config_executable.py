"""Pre-spawn SDK executable pin; no child, command journal or admission.

The parent-fd executable pathname is only a candidate for a future owning kernel.
That kernel must revoke this object's close permission before spawning, retain
the actual parent fd, and journal the full native command. Metadata/header
checks do not make executable bytes immutable or attest the SDK/factory source.
"""
import os
import re
import stat
import sys
import weakref

from android_release_sync_association_config_host import _ARGUMENTS

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_EXECUTABLE_UNAVAILABLE'


class OriginalAssociationConfigExecutable:
    def __init__(self, sdk_root):
        self._origin = weakref.ref(self)  # No callback or automatic cleanup.
        self._state = 'NEW'
        self._fds, self._edges, self._uncertain_close = [], [], []
        self._releasing = False
        self._sdk = self._arguments = self._executable = self._parent_pid = None
        self._require(type(sdk_root) is str and 1 < len(sdk_root) <= 512
            and sdk_root.startswith('/') and not sdk_root.endswith('/'))
        parts = sdk_root[1:].split('/')
        self._require(1 <= len(parts) <= 32 and all(type(x) is str
            and x not in ('.', '..') and re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', x)
            for x in parts))
        self._sdk = sdk_root
        self._arguments = (sdk_root + '/platform-tools/adb', '-s', 'emulator-5562', *_ARGUMENTS)

    def _issued(self):
        reference = getattr(self, '_origin', None)
        if type(reference) is not weakref.ReferenceType or reference() is not self:
            self._state = 'FAILED'
            self._fds, self._edges = [], []  # A copy never owns original fds.
            self._arguments = self._executable = None
            raise RuntimeError(_ERROR) from None

    def _release(self):
        if self._releasing:
            return
        self._releasing = True
        try:
            while self._fds:
                fd = self._fds.pop()
                try:
                    if os.close(fd) is not None:
                        self._uncertain_close.append(fd)
                except BaseException:
                    self._uncertain_close.append(fd)
        finally:
            self._releasing = False

    def _fail(self):
        self._state = 'FAILED'
        self._release()  # No child can exist through this class.
        self._arguments = self._executable = None
        raise RuntimeError(_ERROR) from None

    def _require(self, value):
        if not value:
            self._fail()

    def _io(self, function, *args, **kwargs):
        self._require(self._state in ('PINNING', 'VERIFYING'))
        value = function(*args, **kwargs)
        self._require(self._state in ('PINNING', 'VERIFYING'))
        return value

    def _identity(self, value, directory):
        identity = (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        self._require(all(type(v) is int and v >= 0 for v in identity)
            and value.st_nlink > 0 and value.st_uid in (0, self._uid))
        if directory:
            self._require(stat.S_ISDIR(value.st_mode))
        else:
            self._require(stat.S_ISREG(value.st_mode) and value.st_uid == self._uid
                and value.st_nlink == 1 and 20 <= value.st_size <= 128 * 1024 * 1024
                and value.st_mode & 0o100 and not value.st_mode & 0o7022)
        return identity

    def _open(self, name, parent, directory):
        kwargs = {} if parent is None else {'dir_fd': parent}
        expected = self._identity(self._io(os.stat, name, follow_symlinks=False, **kwargs), directory)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        flags |= os.O_DIRECTORY if directory else os.O_NONBLOCK
        self._require(self._state == 'PINNING')
        fd = os.open(name, flags, **kwargs)
        self._require(type(fd) is int and fd >= 0 and fd not in self._fds)
        self._fds.append(fd)  # Register before post-open state/identity calls.
        self._require(self._state == 'PINNING' and fd >= 3)
        actual = self._identity(self._io(os.fstat, fd), directory)
        self._require(actual == expected and self._io(os.get_inheritable, fd) is False)
        self._edges.append((parent, name, fd, expected, directory))
        return fd

    def _verify(self):
        for parent, name, fd, expected, directory in self._edges:
            kwargs = {} if parent is None else {'dir_fd': parent}
            current = self._identity(self._io(os.stat, name, follow_symlinks=False, **kwargs), directory)
            self._require(current == expected
                and self._identity(self._io(os.fstat, fd), directory) == expected
                and self._io(os.get_inheritable, fd) is False)
        current_pid = self._io(os.getpid)
        self._require(type(current_pid) is int and current_pid > 0 and current_pid == self._parent_pid)
        # This deliberate proc alias names the ORIGINAL PARENT fd, not a child fd.
        target = self._io(os.readlink, self._executable)
        self._require(type(target) is str and target == self._arguments[0])
        self._require(self._identity(self._io(os.stat, self._executable), False) == self._edges[-1][3])

    def pin(self):
        self._issued()
        self._require(self._state == 'NEW')
        self._state = 'PINNING'
        try:
            self._require(sys.platform == 'linux' and os.name == 'posix'
                and all(type(getattr(os, name, None)) is int and getattr(os, name) > 0
                    for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC', 'O_NONBLOCK'))
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
                and os.stat in os.supports_follow_symlinks
                and all(callable(getattr(os, name, None)) for name in
                    ('geteuid', 'getpid', 'pread', 'fstat', 'get_inheritable', 'readlink', 'close')))
            self._uid = self._io(os.geteuid)
            self._require(type(self._uid) is int and self._uid >= 0)
            parent = self._open('/', None, True)
            for name in (*self._sdk[1:].split('/'), 'platform-tools'):
                parent = self._open(name, parent, True)
            fd = self._open('adb', parent, False)
            header = self._io(os.pread, fd, 20, 0)  # SDK header only, never private input.
            self._require(type(header) is bytes and len(header) == 20
                and header[:7] == b'\x7fELF\x02\x01\x01' and header[18:20] == b'\x3e\x00')
            self._parent_pid = self._io(os.getpid)
            self._require(type(self._parent_pid) is int and self._parent_pid > 0)
            self._executable = '/proc/' + str(self._parent_pid) + '/fd/' + str(fd)
            self._verify()
            self._state = 'READY'
        except BaseException:
            self._fail()

    def verify(self):
        self._issued()
        self._require(self._state == 'READY')
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
        self._require(self._state in ('NEW', 'READY'))
        self._state = 'CLOSING'
        self._release()
        self._require(self._state == 'CLOSING' and not self._uncertain_close)
        self._arguments = self._executable = None
        self._state = 'CLOSED'
