"""Consume original SDK close authority into pre-child host ownership.

This is a descriptor owner and original clock source, not a managed ADB server,
environment attestation, child registry, factory admission or command adapter.
Unknown states retain both strong owners; teardown never polls or closes them.
The optional native loopback listener is pre-child IO only. It does not attest
a private/authenticated ADB endpoint, SDK compatibility or a command deadline.
Existing auth descriptors are acquired without reading key bytes. They do not
enforce a later child's no-generation/write policy or prevent path reopening.
"""
import ctypes
import os
import re
import stat
import sys
import threading
import time
import weakref

import android_release_preconfig_sdk as sdk_source

_ERROR = 'SIGNED_PRECONFIG_HOST_RESOURCES_UNAVAILABLE'
_MAX = (1 << 63) - 1
_OWNERS = {}
_LOCK = threading.Lock()


class _InetAddress(ctypes.Structure):
    _fields_ = [('family', ctypes.c_ushort), ('port', ctypes.c_ushort),
                ('address', ctypes.c_ubyte * 4), ('padding', ctypes.c_ubyte * 8)]


def _native_listener_api():
    # Called only after the actual Linux/x86-64 gate, never at construction.
    library = ctypes.CDLL(None, use_errno=True)
    signatures = (
        ('socket', [ctypes.c_int, ctypes.c_int, ctypes.c_int]),
        ('bind', [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]),
        ('listen', [ctypes.c_int, ctypes.c_int]),
        ('getsockname', [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]),
        ('getsockopt', [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]),
    )
    functions = []
    for name, arguments in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, ctypes.c_int
        functions.append(function)
    return library, tuple(functions)


class OriginalPreconfigHostResources:
    def __init__(self, sdk):
        self._origin = weakref.ref(self)
        self._handle_owner = self
        self._state, self._registered = 'NEW', False
        self._sdk = None
        self._fds, self._edges, self._aliases, self._uncertain = [], [], [], []
        self._sources = self._stamp = self._last = self._close = None
        self._clock_source = time.monotonic_ns  # Capture, never sample here.
        self._listener_attempted = self._listener_open_unknown = False
        self._listener_released = False
        self._listener_fds = []
        self._listener_fd = self._listener_port = None
        self._listener_native = self._listener_snapshot = self._listener_binding = None
        self._auth_attempted = self._auth_open_unknown = self._auth_released = False
        self._auth_fds, self._auth_edges = [], []
        self._auth_home = self._auth_binding = self._auth_snapshot = None
        self._need(type(sdk) is sdk_source.OriginalPreconfigSdk)
        self._sdk = sdk

    @staticmethod
    def _original(value):
        reference = getattr(value, '_origin', None)
        return type(reference) is weakref.ReferenceType and reference() is value

    def _issued(self):
        if not self._original(self):
            self._state, self._registered = 'FAILED', False
            self._fds, self._edges, self._aliases = [], [], []
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
            return True
        if not _LOCK.acquire(False):
            return False
        try:
            if _OWNERS.get(id(self)) is not self:
                return False
            del _OWNERS[id(self)]
            self._registered = False
            return True
        finally:
            _LOCK.release()

    def _fail(self):
        self._state = 'FAILED'
        captured = type(self._sources) is tuple and len(self._sources) == 5 and self._fds is self._sources[0]
        if not captured and (self._sdk is None or getattr(self._sdk, '_handle_owner', None) is not self):
            self._drop()  # Never close descriptors still owned by the SDK source.
        # Once consumed, retain original known/uncertain handles without cleanup.
        raise RuntimeError(_ERROR) from None

    def _need(self, value):
        if not value:
            self._fail()

    def _ready_source(self):
        sdk = self._sdk
        self._need(self._original(sdk) and type(sdk._state) is str and sdk._state == 'READY'
            and sdk._handle_owner is sdk and sdk._registered is True
            and sdk_source._OWNERS.get(id(sdk)) is sdk
            and type(sdk._inflight) is int and sdk._inflight == 0 and sdk._releasing is False
            and sdk._open_unknown is False and type(sdk._uncertain) is list and not sdk._uncertain
            and type(sdk._fds) is list and 3 <= len(sdk._fds) <= 40
            and all(type(v) is int and v >= 3 for v in sdk._fds)
            and len(set(sdk._fds)) == len(sdk._fds)
            and type(sdk._edges) is list and len(sdk._edges) == len(sdk._fds)
            and type(sdk._aliases) is list and len(sdk._aliases) == 2
            and type(sdk._context) is tuple and len(sdk._context) == 8
            and all(type(v) is str for v in sdk._context[:7])
            and (sdk._context[7] is None or type(sdk._context[7]) is str)
            and type(sdk._sdk) is str and sdk._sdk == sdk._context[6]
            and type(sdk._parent_pid) is int and sdk._parent_pid > 0
            and type(sdk._uid) is int and sdk._uid >= 0)
        for edge in sdk._edges:
            self._need(type(edge) is tuple and len(edge) == 5
                and (edge[0] is None or type(edge[0]) is int and edge[0] in sdk._fds)
                and type(edge[1]) is str and type(edge[2]) is int and edge[2] in sdk._fds
                and type(edge[3]) is tuple and len(edge[3]) == 8
                and all(type(v) is int and v >= 0 for v in edge[3])
                and type(edge[4]) is str and edge[4] in ('directory', 'adb', 'api36'))
        self._need(tuple(e[2] for e in sdk._edges) == tuple(sdk._fds))
        for alias in sdk._aliases:
            self._need(type(alias) is tuple and len(alias) == 3
                and type(alias[0]) is str and type(alias[1]) is str
                and type(alias[2]) is tuple and len(alias[2]) == 8
                and all(type(v) is int and v >= 0 for v in alias[2]))

    def _snapshot(self):
        self._ready_source()
        sdk = self._sdk
        self._sources = (sdk._fds, sdk._edges, sdk._aliases, sdk._context, sdk._sdk)
        self._stamp = (tuple(sdk._fds), tuple(sdk._edges), tuple(sdk._aliases), sdk._parent_pid, sdk._uid)

    def _stable(self):
        self._ready_source()
        sdk = self._sdk
        self._need(self._state in ('CLAIMING', 'CONSUMING')
            and _OWNERS.get(id(self)) is self and self._registered is True
            and sdk._fds is self._sources[0] and sdk._edges is self._sources[1]
            and sdk._aliases is self._sources[2] and sdk._context is self._sources[3]
            and sdk._sdk is self._sources[4]
            and (tuple(sdk._fds), tuple(sdk._edges), tuple(sdk._aliases),
                sdk._parent_pid, sdk._uid) == self._stamp)

    def _owned(self):
        self._need(type(self._sources) is tuple and len(self._sources) == 5
            and type(self._stamp) is tuple and len(self._stamp) == 5)
        self._need(self._original(self._sdk) and self._registered is True
            and _OWNERS.get(id(self)) is self and self._sdk._handle_owner is self
            and type(self._sdk._state) is str and self._sdk._state == 'TRANSFERRED'
            and self._sdk._registered is True
            and sdk_source._OWNERS.get(id(self._sdk)) is self._sdk
            and self._sdk._context is self._sources[3] and self._sdk._sdk is self._sources[4]
            and type(self._sdk._parent_pid) is int and self._sdk._parent_pid == self._stamp[3]
            and type(self._sdk._uid) is int and self._sdk._uid == self._stamp[4]
            and type(self._sdk._fds) is list and not self._sdk._fds
            and type(self._sdk._edges) is list and not self._sdk._edges
            and type(self._sdk._aliases) is list and not self._sdk._aliases
            and self._fds is self._sources[0] and self._edges is self._sources[1]
            and self._aliases is self._sources[2] and not self._uncertain
            and all(type(v) is int and v >= 3 for v in self._fds)
            and tuple(self._fds) == self._stamp[0]
            and len(self._edges) == len(self._stamp[1])
            and all(a is b for a, b in zip(self._edges, self._stamp[1]))
            and len(self._aliases) == len(self._stamp[2])
            and all(a is b for a, b in zip(self._aliases, self._stamp[2]))
            and (self._last is None or type(self._last) is int and 0 <= self._last <= _MAX))
        self._listener_stable()
        self._auth_stable()

    def _auth_stable(self):
        self._need(type(self._auth_attempted) is bool and type(self._auth_open_unknown) is bool
            and type(self._auth_released) is bool and type(self._auth_fds) is list
            and type(self._auth_edges) is list)
        if self._auth_attempted:
            self._need(type(self._auth_binding) is tuple and len(self._auth_binding) == 3
                and self._auth_fds is self._auth_binding[0]
                and self._auth_edges is self._auth_binding[1]
                and self._auth_home is self._auth_binding[2]
                and len(self._auth_fds) <= 40
                and all(type(fd) is int and fd >= 0 and fd not in self._fds
                    and fd not in self._listener_fds for fd in self._auth_fds)
                and len(set(self._auth_fds)) == len(self._auth_fds))
            if self._state == 'AUTH_PINNING':
                self._need(self._auth_snapshot is None and self._auth_released is False)
            else:
                value = self._auth_snapshot
                self._need(type(value) is tuple and len(value) == 2
                    and type(value[0]) is tuple and type(value[1]) is tuple
                    and self._auth_open_unknown is False
                    and len(self._auth_edges) == len(value[1])
                    and all(a is b for a, b in zip(self._auth_edges, value[1]))
                    and (tuple(self._auth_fds) == value[0] if self._state != 'CLOSING' else
                        tuple(self._auth_fds) == value[0][:len(self._auth_fds)])
                    and (self._auth_released is False or self._state == 'CLOSING'
                        and not self._auth_fds))
        else:
            self._need(self._auth_open_unknown is False and self._auth_released is False
                and not self._auth_fds and not self._auth_edges and self._auth_home is None
                and self._auth_binding is None and self._auth_snapshot is None)

    def _auth_guard(self):
        self._issued()
        self._need(self._state == 'AUTH_PINNING')
        self._owned()

    def _auth_step(self, function, *arguments, **keywords):
        self._auth_guard()
        value = function(*arguments, **keywords)
        self._auth_guard()
        return value

    def _auth_identity(self, value, kind):
        self._need(type(value) is os.stat_result)
        identity = (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
                    value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        self._need(all(type(v) is int and v >= 0 for v in identity)
            and value.st_nlink > 0 and value.st_uid in (0, self._stamp[4])
            and not value.st_mode & 0o7022)
        if kind == 'key':
            self._need(stat.S_ISREG(value.st_mode) and value.st_uid == self._stamp[4]
                and value.st_mode & 0o7777 == 0o600 and value.st_nlink == 1
                and 1 <= value.st_size <= 65536)
        else:
            self._need(stat.S_ISDIR(value.st_mode))
            if kind in ('home', 'android'):
                self._need(value.st_uid == self._stamp[4])
            if kind == 'android':
                self._need(value.st_mode & 0o7777 == 0o700)
        return identity

    def _auth_open(self, name, parent, kind):
        keywords = {} if parent is None else {'dir_fd': parent}
        identity = self._auth_identity(self._auth_step(os.stat, name,
            follow_symlinks=False, **keywords), kind)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        flags |= os.O_NONBLOCK if kind == 'key' else os.O_DIRECTORY
        self._auth_guard()
        self._auth_open_unknown = True
        fd = os.open(name, flags, **keywords)
        if (type(fd) is int and fd >= 0 and fd not in self._fds
                and fd not in self._listener_fds and fd not in self._auth_fds):
            self._auth_fds.append(fd)  # Retain BEFORE post-open phase/owner checks.
            self._auth_open_unknown = False
        self._need(type(fd) is int and fd >= 3 and self._auth_open_unknown is False
            and fd in self._auth_fds)
        self._auth_guard()
        actual = self._auth_identity(self._auth_step(os.fstat, fd), kind)
        self._need(actual == identity and self._auth_step(os.get_inheritable, fd) is False
            and all(edge[3][:2] != actual[:2] for edge in self._auth_edges))
        self._auth_edges.append((parent, name, fd, identity, kind))
        return fd

    def pin_existing_auth(self):
        """Acquire existing auth path descriptors without reading any key bytes.

        This does not exclude later SDK mkdir/key generation, writes or path
        reopens, prove key validity/authorization, or admit an executable child.
        There is no caller path, fallback HOME, file creation or clock sample.
        """
        self._issued()
        self._need(self._state == 'READY')
        self._owned()
        self._need(self._auth_attempted is False)
        self._auth_binding = (self._auth_fds, self._auth_edges, None)
        self._auth_attempted, self._state = True, 'AUTH_PINNING'
        try:
            self._need(sys.platform == 'linux' and os.name == 'posix' and os is sdk_source.os
                and self._close is os.close
                and all(type(getattr(os, k, None)) is int and getattr(os, k) > 0
                    for k in ('O_NOFOLLOW', 'O_CLOEXEC', 'O_DIRECTORY', 'O_NONBLOCK'))
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
                and os.stat in os.supports_follow_symlinks)
            for key, expected in zip(sdk_source._ENV, self._sources[3]):
                value = self._auth_step(os.environ.get, key)
                self._need((type(value) is str or value is None) and value == expected)
            for function, expected in ((os.getpid, self._stamp[3]), (os.geteuid, self._stamp[4])):
                value = self._auth_step(function)
                self._need(type(value) is int and value == expected)
            home = self._auth_step(os.environ.get, 'HOME')
            self._need(type(home) is str and 1 < len(home) <= 512
                and home.startswith('/') and not home.endswith('/'))
            parts = home[1:].split('/')
            self._need(1 <= len(parts) <= 32 and all(re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', x)
                is not None and x not in ('.', '..') for x in parts))
            self._auth_home = home
            self._auth_binding = (self._auth_fds, self._auth_edges, home)
            parent = self._auth_open('/', None, 'directory')
            for index, name in enumerate(parts):
                parent = self._auth_open(name, parent, 'home' if index == len(parts) - 1 else 'directory')
            parent = self._auth_open('.android', parent, 'android')
            self._auth_open('adbkey', parent, 'key')
            for parent, name, fd, identity, kind in self._auth_edges:
                keywords = {} if parent is None else {'dir_fd': parent}
                self._need(self._auth_identity(self._auth_step(os.stat, name,
                    follow_symlinks=False, **keywords), kind) == identity
                    and self._auth_identity(self._auth_step(os.fstat, fd), kind) == identity
                    and self._auth_step(os.get_inheritable, fd) is False)
            value = self._auth_step(os.environ.get, 'HOME')
            self._need(type(value) is str and value == home)
            self._auth_snapshot = (tuple(self._auth_fds), tuple(self._auth_edges))
            self._state = 'READY'
            self._owned()
        except BaseException:
            self._fail()

    def _listener_stable(self):
        self._need(type(self._listener_attempted) is bool
            and type(self._listener_open_unknown) is bool
            and type(self._listener_released) is bool
            and type(self._listener_fds) is list)
        if self._state == 'BINDING':
            self._need(self._listener_attempted is True and self._listener_snapshot is None
                and self._listener_released is False
                and type(self._listener_binding) is tuple and len(self._listener_binding) == 2
                and self._listener_fds is self._listener_binding[0]
                and self._listener_native is self._listener_binding[1]
                and len(self._listener_fds) <= 1
                and all(type(v) is int and v >= 0 and v not in self._fds
                        for v in self._listener_fds))
        elif self._listener_attempted:
            value = self._listener_snapshot
            self._need(type(value) is tuple and len(value) == 4
                and self._listener_open_unknown is False
                and self._listener_fds is value[0]
                and type(self._listener_fd) is int and self._listener_fd >= 3
                and self._listener_fd == value[1]
                and (self._listener_fds == [value[1]] if not self._listener_released else
                     self._state == 'CLOSING' and not self._listener_fds)
                and self._listener_fd not in self._fds
                and type(self._listener_port) is int and 1 <= self._listener_port <= 65535
                and self._listener_port != 5037 and self._listener_port == value[2]
                and self._listener_native is value[3]
                and type(self._listener_binding) is tuple and len(self._listener_binding) == 2
                and self._listener_binding[0] is self._listener_fds
                and self._listener_binding[1] is self._listener_native)
        else:
            self._need(not self._listener_fds and self._listener_open_unknown is False
                and self._listener_released is False and self._listener_binding is None
                and self._listener_fd is None and self._listener_port is None
                and self._listener_native is None and self._listener_snapshot is None)

    def _listener_guard(self):
        self._issued()
        self._need(self._state == 'BINDING')
        self._owned()

    def _listener_zero(self, function, *arguments):
        self._listener_guard()
        value = function(*arguments)
        self._need(type(value) is int and value == 0)
        self._listener_guard()

    def bind_loopback_listener(self):
        """Own one actual bound/listening native socket; no server or admission.

        There is no caller endpoint, raw fd getter, child, SDK command, packet
        read/write, clock sample or retry. A later actual child owner must first
        consume this object's close authority. This is not a full host budget.
        """
        self._issued()
        self._need(self._state == 'READY')
        self._owned()
        self._need(self._listener_attempted is False)
        self._listener_binding = (self._listener_fds, None)
        self._listener_attempted, self._state = True, 'BINDING'
        try:
            # No environment, native library or socket operations on Windows.
            self._need(sys.platform == 'linux' and os.name == 'posix'
                and getattr(sys, 'byteorder', None) == 'little'
                and ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(_InetAddress) == 16
                and all(callable(getattr(os, k, None)) for k in
                        ('uname', 'getpid', 'geteuid', 'get_blocking', 'get_inheritable', 'fstat'))
                and type(getattr(os, 'uname_result', None)) is type
                and self._close is os.close)
            self._listener_guard()
            machine = os.uname()
            self._need(type(machine) is os.uname_result and machine.machine == 'x86_64')
            self._listener_guard()
            for key, expected in zip(sdk_source._ENV, self._sources[3]):
                value = os.environ.get(key)
                self._need((type(value) is str or value is None) and value == expected)
                self._listener_guard()
            for function, expected in ((os.getpid, self._stamp[3]), (os.geteuid, self._stamp[4])):
                value = function()
                self._need(type(value) is int and value == expected)
                self._listener_guard()
            native = _native_listener_api()
            self._need(type(native) is tuple and len(native) == 2
                and type(native[1]) is tuple and len(native[1]) == 5
                and all(callable(f) for f in native[1]))
            self._listener_native = native
            self._listener_binding = (self._listener_fds, native)
            self._listener_guard()
            create, bind, listen, name, option = native[1]
            self._listener_open_unknown = True
            fd = create(2, 1 | 0x80000 | 0x800, 0)  # Linux STREAM|CLOEXEC|NONBLOCK.
            if type(fd) is int and fd >= 0 and fd not in self._fds and fd not in self._listener_fds:
                self._listener_fds.append(fd)  # Retain BEFORE every post-call gate.
                self._listener_fd = fd
                self._listener_open_unknown = False
            elif type(fd) is int and fd < 0:
                self._listener_open_unknown = False
            self._need(type(fd) is int and fd >= 3 and self._listener_fd == fd)
            self._listener_guard()
            current = os.fstat(fd)
            self._need(type(current) is os.stat_result
                and type(current.st_uid) is int and current.st_uid == self._stamp[4]
                and type(current.st_mode) is int and stat.S_ISSOCK(current.st_mode))
            self._listener_guard()
            self._need(os.get_inheritable(fd) is False)
            self._listener_guard()
            self._need(os.get_blocking(fd) is False)
            self._listener_guard()
            address = _InetAddress()
            address.family, address.port = 2, 0
            address.address[:] = (127, 0, 0, 1)
            self._listener_zero(bind, fd, ctypes.byref(address), ctypes.sizeof(address))
            self._listener_zero(listen, fd, 8)
            actual, length = _InetAddress(), ctypes.c_uint(ctypes.sizeof(_InetAddress))
            self._listener_zero(name, fd, ctypes.byref(actual), ctypes.byref(length))
            port = int.from_bytes(bytes(actual)[2:4], 'big')
            self._need(length.value == 16 and actual.family == 2
                and tuple(actual.address) == (127, 0, 0, 1) and not any(actual.padding)
                and 1 <= port <= 65535 and port != 5037)
            # Linux SO_TYPE/ACCEPTCONN/REUSEADDR/REUSEPORT, closed exact results.
            for key, expected in ((3, 1), (30, 1), (2, 0), (15, 0)):
                value, length = ctypes.c_int(), ctypes.c_uint(ctypes.sizeof(ctypes.c_int))
                self._listener_zero(option, fd, 1, key, ctypes.byref(value), ctypes.byref(length))
                self._need(length.value == 4 and value.value == expected)
            self._listener_guard()
            self._listener_port = port
            self._listener_snapshot = (self._listener_fds, fd, port, self._listener_native)
            self._state = 'READY'
            self._owned()
        except BaseException:
            self._fail()

    def claim(self):
        self._issued()
        self._need(self._state == 'NEW')
        self._state = 'CLAIMING'
        try:
            self._need(sys.platform == 'linux' and os.name == 'posix' and os is sdk_source.os
                and callable(getattr(os, 'close', None)))
            self._snapshot()
            self._reserve()  # Strong original source refs before authority move.
            self._stable()
            self._close = os.close
            self._state = 'CONSUMING'
            self._need(self._sdk._consume_for_host(self) is None)
            self._owned()
            self._state = 'READY'
        except BaseException:
            self._fail()

    def monotonic_ms(self):
        self._issued()
        self._need(self._state == 'READY')
        self._owned()
        self._need(self._clock_source is time.monotonic_ns)
        self._state = 'CLOCK'
        try:
            ns = self._clock_source()
            self._need(self._state == 'CLOCK' and type(ns) is int
                and 0 <= ns <= _MAX * 1_000_000 + 999_999)
            self._owned()
            value = ns // 1_000_000
            self._need(self._last is None or value >= self._last)
            self._last, self._state = value, 'READY'
            return value
        except BaseException:
            self._fail()

    def close(self):
        self._issued()
        if self._state == 'CLOSED':
            return
        self._need(self._state in ('NEW', 'READY'))
        if self._state == 'NEW':
            self._state = 'CLOSED'
            return
        self._owned()
        self._state = 'CLOSING'
        if self._listener_attempted:
            fd = self._listener_fd
            snapshot = self._listener_snapshot
            self._need(self._listener_open_unknown is False
                and self._listener_fds == [fd] and fd not in self._fds)
            self._listener_fds.pop()
            try:
                known = self._close(fd) is None
            except BaseException:
                known = False
            if not known or self._state != 'CLOSING' or self._listener_fds:
                self._uncertain.append(fd)
                self._fail()
            self._need(self._listener_snapshot is snapshot)
            self._listener_released = True
            self._owned()  # No later close after a changed source or listener binding.
        if self._auth_attempted:
            snapshot = self._auth_snapshot
            for fd in reversed(snapshot[0]):
                self._need(self._auth_snapshot is snapshot and self._state == 'CLOSING')
                self._owned()
                self._need(self._state == 'CLOSING' and self._auth_fds[-1] == fd)
                self._auth_fds.pop()
                try:
                    known = self._close(fd) is None
                except BaseException:
                    known = False
                if not known or self._state != 'CLOSING':
                    self._uncertain.append(fd)
                    self._fail()
            self._need(self._auth_snapshot is snapshot)
            self._auth_released = True
            self._owned()
        original_fds = self._stamp[0]
        for index, fd in enumerate(reversed(original_fds)):
            self._need(self._state == 'CLOSING'
                and self._sdk._handle_owner is self and self._registered is True
                and _OWNERS.get(id(self)) is self
                and sdk_source._OWNERS.get(id(self._sdk)) is self._sdk
                and all(type(v) is int for v in self._fds)
                and tuple(self._fds) == original_fds[:len(original_fds) - index])
            self._fds.pop()
            try:
                known = self._close(fd) is None
            except BaseException:
                known = False
            if not known or self._state != 'CLOSING':
                self._uncertain.append(fd)
                self._fail()
        self._need(all(type(v) is int for v in self._fds) and not self._fds)
        try:
            self._need(self._sdk._retire_host_owner(self) is None)
            self._need(self._state == 'CLOSING' and self._drop())
            self._listener_native = self._listener_snapshot = None
            self._listener_binding = None
            self._listener_fd = self._listener_port = None
            self._auth_home = self._auth_binding = self._auth_snapshot = None
            self._state = 'CLOSED'
        except BaseException:
            self._fail()
