"""Own anonymous pre-spawn config pipes; no child or admission is created.

Linux capabilities are required explicitly. This primitive only prepares the
original input and retains private handles. A future spawning owner must claim
them before sending; no handoff, actor adapter, reader or destructor is supplied.
SAME bytes means the Python buffer, not zero-copy kernel data or readback ACK.
"""
import os
import stat
import sys

try:
    import fcntl
except ImportError:
    fcntl = None

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_CHANNELS_UNAVAILABLE'


class OriginalAssociationConfigChannels:
    def __init__(self, original_input):
        self._state = 'NEW'
        self._fds, self._uncertain_close = [], []
        self._pipes, self._identities = (), {}
        self._releasing = False
        self._input = None
        self._require(type(original_input) is bytes and 0 < len(original_input) <= 2048)
        self._input = original_input

    def _release(self):
        if self._releasing:
            return
        self._releasing = True
        try:
            while self._fds:
                fd = self._fds.pop()  # Never retry close: the number may be reused.
                try:
                    if os.close(fd) is not None:
                        self._uncertain_close.append(fd)
                except BaseException:
                    self._uncertain_close.append(fd)
        finally:
            self._releasing = False

    def _fail(self):
        self._state = 'FAILED'
        self._input, self._pipes, self._identities = None, (), {}
        self._release()  # No child exists; only this owner's local descriptors.
        raise RuntimeError(_ERROR) from None

    def _require(self, condition):
        if not condition:
            self._fail()

    def _io(self, function, *args):
        self._require(self._state == 'PROVISIONING')
        value = function(*args)
        self._require(self._state == 'PROVISIONING')
        return value

    def _pipe(self):
        self._require(self._state == 'PROVISIONING')
        pair = os.pipe2(os.O_CLOEXEC)
        self._require(type(pair) is tuple and len(pair) == 2)
        prior = set(self._fds)
        # Register native returned handles before any post-open guard or stat.
        for fd in pair:
            if type(fd) is int and fd >= 0 and fd not in self._fds:
                self._fds.append(fd)
        self._require(self._state == 'PROVISIONING' and
            all(type(fd) is int and fd >= 3 and fd not in prior for fd in pair)
            and pair[0] != pair[1])
        return pair

    def _inspect(self, fd, *, reading, blocking):
        info = self._io(os.fstat, fd)
        values = (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink)
        self._require(all(type(v) is int and v >= 0 for v in values)
            and info.st_ino > 0 and stat.S_ISFIFO(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == self._uid and info.st_nlink == 1)
        self._require(self._io(os.get_inheritable, fd) is False
            and self._io(os.get_blocking, fd) is blocking)
        flags = self._io(fcntl.fcntl, fd, fcntl.F_GETFL)
        self._require(type(flags) is int and flags >= 0
            and flags & os.O_ACCMODE == (os.O_RDONLY if reading else os.O_WRONLY))
        return values

    def provision(self):
        self._require(self._state == 'NEW')
        self._state = 'PROVISIONING'
        try:
            self._provision()
        except BaseException:
            self._fail()

    def _provision(self):
        self._require(os.name == 'posix' and sys.platform == 'linux' and fcntl is not None)
        self._require(all(callable(getattr(os, name, None)) for name in
            ('pipe2', 'fstat', 'geteuid', 'fpathconf', 'get_inheritable',
             'get_blocking', 'set_blocking', 'write', 'close'))
            and callable(getattr(fcntl, 'fcntl', None)))
        self._require(all(type(getattr(os, name, None)) is int for name in
            ('O_CLOEXEC', 'O_ACCMODE', 'O_RDONLY', 'O_WRONLY'))
            and type(getattr(fcntl, 'F_GETFL', None)) is int)
        self._uid = self._io(os.geteuid)
        self._require(type(self._uid) is int and self._uid >= 0)
        pairs = tuple(self._pipe() for _ in range(3))
        identities = []
        for index, pair in enumerate(pairs):
            for reading, fd in zip((True, False), pair):
                self._identities[fd] = self._inspect(fd, reading=reading, blocking=True)
            self._require(self._identities[pair[0]] == self._identities[pair[1]])
            identity = self._identities[pair[0]][:2]
            self._require(identity not in identities)
            identities.append(identity)
            parent = pair[1] if index == 0 else pair[0]
            self._require(self._io(os.set_blocking, parent, False) is None)
        for index, pair in enumerate(pairs):
            for reading, fd in zip((True, False), pair):
                self._require(self._inspect(fd, reading=reading,
                    blocking=(reading if index == 0 else not reading)) == self._identities[fd])
        size = self._io(os.fpathconf, pairs[0][1], 'PC_PIPE_BUF')
        self._require(type(size) is int and size >= len(self._input))
        # A single nonblocking <=PIPE_BUF write on the fresh owned input pipe.
        # A partial/unknown result is failure, never a request to resend bytes.
        written = self._io(os.write, pairs[0][1], self._input)
        self._require(type(written) is int and written == len(self._input))
        writer = pairs[0][1]
        self._fds.remove(writer)
        try:
            closed = os.close(writer)
        except BaseException:
            self._uncertain_close.append(writer)
            raise
        if closed is not None:
            self._uncertain_close.append(writer)
        self._require(self._state == 'PROVISIONING' and closed is None)
        del self._identities[writer]
        for index, pair in enumerate(pairs):
            for reading, fd in zip((True, False), pair):
                if fd == writer:
                    continue
                blocking = reading if index == 0 else not reading
                self._require(self._inspect(fd, reading=reading, blocking=blocking)
                    == self._identities[fd])
        self._pipes = ((pairs[0][0],), pairs[1], pairs[2])
        self._state = 'READY'

    def close(self):
        if self._state == 'CLOSED':
            return
        self._require(self._state in ('NEW', 'READY'))
        self._state = 'CLOSING'
        self._input, self._pipes, self._identities = None, (), {}
        self._release()
        self._require(self._state == 'CLOSING' and not self._uncertain_close)
        self._state = 'CLOSED'
