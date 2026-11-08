"""Private exclusive byte preservation, not association runtime admission.

Requires POSIX descriptor-relative/no-follow semantics; unsupported hosts refuse.
No actor adapter, process, device, clock, hash, raw getter or CLI is supplied.
The owning fixture/config/operation factory and unknown process policy are still
separate requirements. Object identity is not a security boundary or readback ACK.
"""
import os
from pathlib import Path
import stat


_ROLES = {
    'instrument-command-intent': ('instrument-command-intent.private.json', 4096),
    'stop-semantic-intent': ('stop-semantic-intent.private.json', 4096),
    'stop-command-intent': ('stop-command-intent.private.json', 4096),
    'completion': ('signed-association-bootstrap-completion.private.json', 32768),
    'stopped': ('signed-association-owned-stop.private.json', 32768),
}
_ERROR = 'SIGNED_ASSOCIATION_PRIVATE_FILES_UNAVAILABLE'


class OriginalAssociationPrivateFiles:
    """One fresh private directory with fixed, once-attempted writer roles.

    Bytes remain opaque and private. This mechanical writer does not approve
    their meaning or prove any original managed child, file read or Native ACK.
    """
    def __init__(self, *, tools, artifact):
        self._state = 'NEW'
        self._fds, self._edges, self._entries, self._attempted = [], [], {}, set()
        native = type(Path())
        self._require(type(tools) is native and type(artifact) is native)
        self._require(all(p.is_absolute() and '..' not in p.parts and len(p.parts) <= 64
                          and len(str(p)) <= 4096 for p in (tools, artifact)))
        self._require(not tools.is_relative_to(artifact) and not artifact.is_relative_to(tools))
        self._tools, self._artifact = tools, artifact
        self._task = self._uid = None

    def _release(self):
        descriptors, self._fds = self._fds, []
        self._entries.clear()
        failed = False
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                failed = True
        return failed

    def _die(self):
        self._state = 'FAILED'
        self._release()  # Only local owned handles; never unlink or process IO.
        raise RuntimeError(_ERROR) from None

    def _require(self, condition):
        if not condition:
            self._die()

    def _io(self, function, *args, **kwargs):
        self._require(self._state in ('PROVISIONING', 'WRITING'))
        value = function(*args, **kwargs)
        self._require(self._state in ('PROVISIONING', 'WRITING'))
        return value

    def _identity(self, value, *, directory):
        fields = (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_nlink)
        self._require(all(type(v) is int and v >= 0 for v in fields) and value.st_ino > 0)
        attributes = getattr(value, 'st_file_attributes', 0)
        self._require(type(attributes) is int and not attributes & 0x400)
        self._require(stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
        self._require(value.st_nlink >= 1 if directory else value.st_nlink == 1)
        return (value.st_dev, value.st_ino, value.st_uid, value.st_mode)

    def _opened(self, path, flags, *, parent=None, mode=None):
        kwargs = {} if parent is None else {'dir_fd': parent}
        self._require(self._state in ('PROVISIONING', 'WRITING'))
        fd = os.open(path, flags, **kwargs) if mode is None else os.open(path, flags, mode, **kwargs)
        self._require(type(fd) is int and fd >= 0 and fd not in self._fds)
        self._fds.append(fd)
        self._require(self._state in ('PROVISIONING', 'WRITING'))
        return fd

    def _directory(self, name, parent=None):
        kwargs = {} if parent is None else {'dir_fd': parent}
        original = self._io(os.stat, name, follow_symlinks=False, **kwargs)
        expected = self._identity(original, directory=True)
        fd = self._opened(name, self._directory_flags, parent=parent)
        actual = self._io(os.fstat, fd)
        self._require(self._identity(actual, directory=True) == expected)
        self._edges.append((parent, name, fd, expected))
        return fd

    def _chain(self, path):
        fd = self._directory(path.anchor)
        identities = [self._edges[-1][3][:2]]
        for part in path.parts[1:]:
            fd = self._directory(part, fd)
            identities.append(self._edges[-1][3][:2])
        return fd, identities

    def _guard(self):
        for parent, name, fd, expected in self._edges:
            kwargs = {} if parent is None else {'dir_fd': parent}
            current = self._io(os.stat, name, follow_symlinks=False, **kwargs)
            pinned = self._io(os.fstat, fd)
            self._require(self._identity(current, directory=True) == expected
                          and self._identity(pinned, directory=True) == expected)
        for raw, fd, name, expected in self._entries.values():
            self._file(fd, name, expected=expected, size=len(raw))

    def _file(self, fd, name, *, expected=None, size=0):
        current = self._io(os.stat, name, dir_fd=self._task, follow_symlinks=False)
        pinned = self._io(os.fstat, fd)
        identity = self._identity(pinned, directory=False)
        self._require(self._identity(current, directory=False) == identity
                      and identity[2] == self._uid and stat.S_IMODE(identity[3]) == 0o600
                      and type(pinned.st_size) is int and pinned.st_size == size
                      and type(current.st_size) is int and current.st_size == size)
        self._require(expected is None or expected == identity)
        return identity

    def provision(self):
        self._require(self._state == 'NEW')
        self._state = 'PROVISIONING'
        try:
            # No path-following or permission fallback on unsupported hosts.
            self._require(all(type(getattr(os, k, None)) is int and getattr(os, k) > 0
                              for k in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC')))
            self._require(all(f in os.supports_dir_fd for f in (os.open, os.stat, os.mkdir))
                          and os.stat in os.supports_follow_symlinks and callable(getattr(os, 'geteuid', None)))
            self._directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
            self._uid = self._io(os.geteuid)
            self._require(type(self._uid) is int and self._uid >= 0)
            tools, tool_ids = self._chain(self._tools)
            _, artifact_ids = self._chain(self._artifact)
            self._require(tool_ids[-1] not in artifact_ids and artifact_ids[-1] not in tool_ids)
            self._require(self._io(self._tools.resolve, strict=True) == self._tools
                          and self._io(self._artifact.resolve, strict=True) == self._artifact)
            self._guard()
            made = self._io(os.mkdir, 'association-private-files', 0o700, dir_fd=tools)
            self._require(made is None)
            self._task = self._directory('association-private-files', tools)
            task = self._io(os.fstat, self._task)
            self._require(task.st_uid == self._uid and stat.S_IMODE(task.st_mode) == 0o700)
            self._guard()
            self._state = 'READY'
        except Exception:
            self._die()

    def write(self, role, raw):
        self._require(self._state == 'READY')
        self._state = 'WRITING'
        try:
            self._require(type(role) is str and role in _ROLES and role not in self._attempted)
            self._attempted.add(role)
            name, limit = _ROLES[role]
            self._require(type(raw) is bytes and 0 < len(raw) <= limit)
            self._guard()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = self._opened(name, flags, parent=self._task, mode=0o600)
            identity = self._file(fd, name)
            view, position = memoryview(raw), 0
            while position < len(raw):
                count = self._io(os.write, fd, view[position:])
                self._require(type(count) is int and 0 < count <= len(raw)-position)
                position += count
            self._require(self._io(os.fsync, fd) is None)
            self._guard()
            self._file(fd, name, expected=identity, size=len(raw))
            # SAME bytes object, no reconstruction, readback or public getter.
            self._entries[role] = (raw, fd, name, identity)
            self._state = 'READY'
        except Exception:
            self._die()

    def close(self):
        self._state = 'CLOSED'
        if self._release():
            self._state = 'FAILED'
            raise RuntimeError(_ERROR) from None
