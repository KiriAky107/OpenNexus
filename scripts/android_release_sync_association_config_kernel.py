"""Original local config command ownership; no factory/GUI/Native admission.

Only explicit run creates/polls a child. Unknown attempts retain strong original
ownership without polling, reading, waiting, killing or cleanup on destruction.
Native creation is not guaranteed interruptible within the producer deadline.
"""
import os
import stat
import sys
import threading
import time
from types import MethodType, FunctionType
import weakref
try:
    import fcntl
except ImportError:
    fcntl = None

import android_release_sync_association_config_host as config
import android_release_sync_association_config_resources as resources
from android_release_sync_association_config_native_intent import OriginalAssociationConfigNativeIntent, _ROLE

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_KERNEL_UNAVAILABLE'
_OWNERS = {}
_LOCK = threading.Lock()


class OriginalAssociationConfigKernel:
    def __init__(self, actor, native_intent):
        self._origin = weakref.ref(self)
        self._state = 'NEW'
        self._registered = self._attempted = False
        self._pipe_fds, self._pin_fds, self._pidfds, self._uncertain = [], [], [], []
        self._pipes, self._pipe_ids, self._pin_edges = (), {}, []
        self._pid = self._first_exit = self._first_seen = None
        self._actor = self._native = self._owner = self._pin = None
        self._sleep = time.sleep  # Capture, do not sample or wait in constructor.
        self._require(type(actor) is config.OriginalAssociationConfigStaging
            and type(native_intent) is OriginalAssociationConfigNativeIntent)
        self._actor, self._native = actor, native_intent

    @staticmethod
    def _original(value):
        reference = getattr(value, '_origin', None)
        return type(reference) is weakref.ReferenceType and reference() is value

    def _issued(self):
        if not self._original(self):
            self._state = 'FAILED'
            self._pipe_fds, self._pin_fds, self._pidfds = [], [], []
            self._registered = False
            raise RuntimeError(_ERROR) from None

    def _reserve(self):
        self._require(_LOCK.acquire(False) is True)
        try:
            self._require(not _OWNERS)
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

    def _close_fd(self, group, fd):
        group.remove(fd)  # Never retry a possibly reused descriptor number.
        try:
            if self._close(fd) is None:
                return True
        except BaseException:
            pass
        self._uncertain.append(fd)
        return False

    def _release(self):
        groups = []
        if self._owner is not None and self._owner._channels._handle_owner is self:
            groups.append(self._pipe_fds)
        if self._pin is not None and self._pin._handle_owner is self:
            groups.append(self._pin_fds)
        groups.append(self._pidfds)
        for group in groups:
            while group:
                if not self._close_fd(group, group[-1]):
                    return False
        return not self._uncertain

    def _fail(self):
        self._state = 'FAILED'
        if not self._attempted:
            if self._release():
                dropped = self._owner is None or self._owner._channels._handle_owner is not self or self._owner._drop()
                if dropped:
                    self._drop()
        # A spawn attempt may have a child even when no PID was returned.
        raise RuntimeError(_ERROR) from None

    def _require(self, value):
        if not value:
            self._fail()

    def _alive(self):
        self._require(self._state in ('PREPARING', 'CONSUMING', 'SENDING', 'SPAWNING', 'POLLING', 'READING')
            and self._actor._state == 'SENDING'
            and self._actor._send is self._send
            and self._actor._intent is self._sources[0]
            and self._actor._input is self._sources[1]
            and self._native._state == ('RECORDED' if self._state == 'PREPARING' else 'CONSUMED')
            and self._native._clock_source is self._owner._clock_source)
        clock = self._actor._ports.monotonic_ms
        self._require(type(clock) is MethodType and clock.__self__ is self._owner
            and clock.__func__ is resources.OriginalAssociationConfigResources.monotonic_ms)

    def _clock(self):
        self._alive()
        original_state = self._state
        value = self._clock_method()
        self._require(self._state == original_state)
        self._alive()
        self._require(type(value) is int and self._last <= value <= self._deadline)
        self._last = value
        return value

    def _capabilities(self):
        self._require(sys.platform == 'linux' and sys.version_info >= (3, 13) and os.name == 'posix'
            and fcntl is not None and callable(getattr(fcntl, 'fcntl', None))
            and type(getattr(fcntl, 'F_GETFL', None)) is int
            and all(type(getattr(os, n, None)) is int for n in
                ('POSIX_SPAWN_DUP2', 'POSIX_SPAWN_CLOSEFROM', 'WNOHANG', 'O_ACCMODE', 'O_RDONLY', 'O_WRONLY'))
            and all(callable(getattr(os, n, None)) for n in
                ('posix_spawn', 'pidfd_open', 'waitpid', 'read', 'close', 'fstat', 'get_blocking', 'get_inheritable', 'getpid')))
        self._spawn, self._pidfd_open, self._waitpid, self._read, self._close = (
            os.posix_spawn, os.pidfd_open, os.waitpid, os.read, os.close)

    def _snapshot(self):
        actor, native = self._actor, self._native
        self._require(self._original(actor) and self._original(native)
            and actor._state == 'SENDING' and native._state == 'RECORDED')
        owner, pin = native._resources, native._executable
        self._require(self._original(owner) and self._original(pin)
            and owner._state == pin._state == 'READY'
            and resources._PRESPAWN_OWNERS.get(id(owner)) is owner)
        entry, sources, stamp = native._entry, native._sources, native._stamp
        store = owner._journals._store
        self._require(type(store._entries) is dict and type(store._fds) is list
            and all(type(v) is int for v in store._fds)
            and type(entry) is tuple and len(entry) == 4
            and entry is store._entries.get(_ROLE) and store._state == 'READY'
            and type(entry[0]) is bytes and 0 < len(entry[0]) <= 4096
            and type(entry[1]) is int and entry[1] in store._fds
            and type(entry[2]) is str and entry[2] == 'signed-association-config-native-command-intent.private.json'
            and type(entry[3]) is tuple and len(entry[3]) == 4 and all(type(v) is int for v in entry[3])
            and type(sources) is tuple and len(sources) == 3
            and type(sources[0]) is bytes and type(sources[1]) is bytes
            and 0 < len(sources[0]) <= 4096 and 0 < len(sources[1]) <= 2048
            and type(sources[2]) is tuple and all(type(v) is str for v in sources[2])
            and actor._intent is sources[0] and actor._input is sources[1]
            and pin._arguments is sources[2]
            and type(stamp) is tuple and len(stamp) == 6
            and type(stamp[0]) is tuple and len(stamp[0]) == 4
            and owner._journals._intent is stamp[0]
            and stamp[0] is store._entries.get('config-command-intent')
            and stamp[0][0] is sources[0] and owner._input is sources[1]
            and type(owner._fds) is list and all(type(v) is int for v in owner._fds)
            and type(pin._fds) is list and all(type(v) is int for v in pin._fds)
            and type(stamp[1]) is tuple and all(type(v) is int for v in stamp[1])
            and type(stamp[2]) is tuple and all(type(v) is int for v in stamp[2])
            and tuple(owner._fds) == stamp[1] and tuple(pin._fds) == stamp[2]
            and type(pin._parent_pid) is int and type(stamp[3]) is int and pin._parent_pid > 0
            and type(pin._executable) is str and type(stamp[4]) is str
            and pin._parent_pid == stamp[3] and pin._executable == stamp[4]
            and native._clock_source is owner._clock_source
            and type(owner._last) is int and type(stamp[5]) is int and stamp[5] <= owner._last)
        send = actor._send
        clock = actor._ports.monotonic_ms
        self._require(type(send) is tuple and len(send) == 4 and type(send[0]) is FunctionType
            and type(send[1]) is list and all(type(v) is str for v in send[1])
            and tuple(send[1]) == config._ARGUMENTS and send[2] is sources[1]
            and type(send[3]) is int and owner._last <= send[3] <= config.common.MAX
            and actor._deadline == send[3]
            and type(clock) is MethodType and clock.__self__ is owner
            and clock.__func__ is resources.OriginalAssociationConfigResources.monotonic_ms)
        self._owner, self._pin, self._send, self._sources, self._entry = owner, pin, send, sources, entry
        self._clock_method, self._deadline, self._last = clock, send[3], owner._last
        self._arguments, self._path, self._parent_pid = pin._arguments, pin._executable, pin._parent_pid

    def _pipe_guard(self):
        owner = self._owner
        self._require(type(owner._fds) is list and len(owner._fds) == 5
            and len(set(owner._fds)) == 5 and type(owner._pipes) is tuple and len(owner._pipes) == 3)
        reading = {owner._pipes[0][0], owner._pipes[1][0], owner._pipes[2][0]}
        for fd in owner._fds:
            self._require(type(fd) is int and fd >= 3)
            value = os.fstat(fd)
            identity = (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_nlink)
            self._require(all(type(v) is int and v >= 0 for v in identity)
                and identity == owner._identities[fd] and stat.S_ISFIFO(value.st_mode)
                and os.get_inheritable(fd) is False
                and os.get_blocking(fd) is (fd not in (owner._pipes[1][0], owner._pipes[2][0])))
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            self._require(type(flags) is int and flags & os.O_ACCMODE == (os.O_RDONLY if fd in reading else os.O_WRONLY))

    def run(self):
        self._issued()
        self._require(self._state == 'NEW')
        self._state = 'PREPARING'
        try:
            return self._run()
        except BaseException:
            self._fail()

    def _run(self):
        self._capabilities()  # Unsupported hosts do not consume or touch handles.
        self._snapshot()
        self._reserve()
        self._clock()
        self._pin.verify()
        self._pipe_guard()
        actual_parent = os.getpid()
        self._require(type(actual_parent) is int and actual_parent == self._parent_pid)
        self._clock()
        self._state = 'CONSUMING'
        self._native._state = 'CONSUMED'
        self._owner._consume_for_kernel(self)
        self._pin._consume_for_kernel(self)
        self._require(self._owner._channels._handle_owner is self and self._pin._handle_owner is self
            and self._owner._state == self._pin._state == 'TRANSFERRED'
            and len(self._pipe_fds) == 5 and not self._owner._fds and not self._pin._fds)
        self._state = 'SENDING'
        self._require(self._send[0](self._send[1], self._sources[1]) is None)
        self._require(self._clock() < self._deadline)
        actions = ((os.POSIX_SPAWN_DUP2, self._pipes[0][0], 0),
            (os.POSIX_SPAWN_DUP2, self._pipes[1][1], 1),
            (os.POSIX_SPAWN_DUP2, self._pipes[2][1], 2), (os.POSIX_SPAWN_CLOSEFROM, 3))
        self._state = 'SPAWNING'
        self._attempted = True
        self._pid = self._spawn(self._path, self._arguments, None, file_actions=actions)
        # Store the returned original PID before ANY later check or syscall.
        self._require(type(self._pid) is int and self._pid > 0)
        self._alive()
        self._clock()
        fd = self._pidfd_open(self._pid, 0)
        self._require(type(fd) is int and fd >= 0 and fd not in self._pipe_fds + self._pin_fds)
        self._pidfds.append(fd)
        self._require(fd >= 3)
        self._alive()
        self._clock()
        for fd in (self._pipes[0][0], self._pipes[1][1], self._pipes[2][1]):
            self._require(self._close_fd(self._pipe_fds, fd))
        self._state = 'POLLING'
        for index in range(201):
            self._clock()
            result = self._waitpid(self._pid, os.WNOHANG)
            self._require(type(result) is tuple and len(result) == 2 and all(type(v) is int for v in result))
            if result == (self._pid, 0):
                self._first_exit = result
                self._first_seen = self._clock()
                break
            self._require(result == (0, 0) and index < 200)
            remaining = self._deadline - self._clock()
            self._require(remaining > 0)
            self._require(self._sleep(min(0.05, remaining / 1000)) is None)
        self._require(self._first_exit is not None)
        self._state = 'READING'
        for fd in (self._pipes[1][0], self._pipes[2][0]):
            self._alive()
            raw = self._read(fd, 1)
            self._require(type(raw) is bytes and len(raw) == 0)
            self._alive()
        self._clock()
        self._require(self._release() and self._owner._drop() and self._drop())
        self._state = 'DONE'
        return dict(returncode=0, unknown_result=False, stdout=b'', stderr=b'')
