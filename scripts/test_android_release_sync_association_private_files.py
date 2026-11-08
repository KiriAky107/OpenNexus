"""Small real-file checks with explicitly controlled POSIX descriptor semantics.

On Windows the bridge emulates directory fds/no-follow/modes. File exclusive
creation, write, hardlink counts and fsync use the real owned test filesystem.
This is not native Linux verification, an actor adapter or runtime acceptance.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import android_release_sync_association_private_files as subject


class PosixFiles:
    NOFOLLOW, DIRECTORY, CLOEXEC = 0x10000000, 0x20000000, 0x40000000

    def __init__(self):
        self.real_open, self.real_stat, self.real_fstat = os.open, os.stat, os.fstat
        self.real_close, self.real_mkdir = os.close, os.mkdir
        self.real_write, self.real_fsync = os.write, os.fsync
        self.handles, self.next_directory = {}, 1000000
        self.events, self.facts = [], {}
        self.after_open = self.on_write = self.on_fsync = None

    def path(self, path, dir_fd=None):
        return Path(path) if dir_fd is None else self.handles[dir_fd][0] / path

    def info(self, value, path):
        data = {k: getattr(value, k) for k in ('st_dev', 'st_ino', 'st_nlink', 'st_size')}
        data.update(st_uid=1234, st_mode=stat.S_IFMT(value.st_mode) |
                    (0o700 if stat.S_ISDIR(value.st_mode) else 0o600), st_file_attributes=0)
        data.update(self.facts.get(path, {}))
        return SimpleNamespace(**data)

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        path = self.path(path, dir_fd)
        self.events.append(('stat', path))
        return self.info(self.real_stat(path, follow_symlinks=follow_symlinks), path)

    def fstat(self, fd):
        path, directory = self.handles[fd]
        self.events.append(('fstat', path))
        value = self.real_stat(path, follow_symlinks=False) if directory else self.real_fstat(fd)
        # A path-replacement injection must not change the original pinned fd.
        facts = self.facts.pop(path, None)
        try:
            return self.info(value, path)
        finally:
            if facts is not None:
                self.facts[path] = facts

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        path = self.path(path, dir_fd)
        self.events.append(('open', path, flags, mode))
        if not flags & self.NOFOLLOW or not flags & self.CLOEXEC:
            raise AssertionError('missing controlled no-follow/close-on-exec')
        if flags & self.DIRECTORY:
            value = self.real_stat(path, follow_symlinks=False)
            if not stat.S_ISDIR(value.st_mode):
                raise OSError('controlled non-directory')
            fd = self.next_directory
            self.next_directory += 1
            self.handles[fd] = (path, True)
        else:
            if flags & (os.O_EXCL | os.O_CREAT) != os.O_EXCL | os.O_CREAT or mode != 0o600:
                raise AssertionError('missing exclusive private create')
            native_flags = flags & ~(self.NOFOLLOW | self.DIRECTORY | self.CLOEXEC)
            fd = self.real_open(path, native_flags, mode)
            self.handles[fd] = (path, False)
        if self.after_open:
            self.after_open(path, fd)
        return fd

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        path = self.path(path, dir_fd)
        self.events.append(('mkdir', path, mode))
        self.real_mkdir(path, mode)

    def write(self, fd, raw):
        self.events.append(('write', self.handles[fd][0], len(raw)))
        if self.on_write:
            return self.on_write(fd, raw)
        return self.real_write(fd, raw)

    def fsync(self, fd):
        self.events.append(('fsync', self.handles[fd][0]))
        if self.on_fsync:
            return self.on_fsync(fd)
        return self.real_fsync(fd)

    def close(self, fd):
        path, directory = self.handles.pop(fd)
        self.events.append(('close', path))
        if not directory:
            self.real_close(fd)

    def __enter__(self):
        self.stack = ExitStack()
        replacements = dict(open=self.open, stat=self.stat, fstat=self.fstat,
            mkdir=self.mkdir, write=self.write, fsync=self.fsync, close=self.close,
            O_NOFOLLOW=self.NOFOLLOW, O_DIRECTORY=self.DIRECTORY, O_CLOEXEC=self.CLOEXEC,
            geteuid=lambda: 1234, supports_dir_fd={self.open, self.stat, self.mkdir},
            supports_follow_symlinks={self.stat})
        for key, value in replacements.items():
            self.stack.enter_context(patch.object(os, key, value, create=True))
        return self

    def __exit__(self, *error):
        # Restore native APIs before the owning fixture removes its tree.
        self.stack.close()
        assert not self.handles, 'controlled owning handles leaked'


class PrivateFilesNoIO(unittest.TestCase):
    def setUp(self):
        # Lexical paths only: no directory is made, queried, or cleaned.
        base = Path(__file__).absolute().parent / 'controlled-never-created-private-files'
        self.tools, self.artifact = base / 'tools', base / 'artifact'

    def store(self):
        return subject.OriginalAssociationPrivateFiles(tools=self.tools, artifact=self.artifact)

    def fails(self, action):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_PRIVATE_FILES_UNAVAILABLE$'):
            action()

    def test_constructor_and_invalid_inputs_have_no_filesystem_effect(self):
        with patch.object(os, 'stat', side_effect=AssertionError), \
             patch.object(os, 'open', side_effect=AssertionError), \
             patch.object(os, 'mkdir', side_effect=AssertionError), \
             patch.object(Path, 'resolve', side_effect=AssertionError), \
             patch.object(time, 'monotonic', side_effect=AssertionError), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError):
            value = self.store()
            for tools, artifact in [(str(self.tools), self.artifact), (self.tools, self.tools),
                                    (self.tools, self.tools / 'child'), (Path('relative'), self.artifact),
                                    (self.tools / '..' / 'alias', self.artifact),
                                    (self.tools.joinpath(*(['deep'] * 65)), self.artifact)]:
                self.fails(lambda: subject.OriginalAssociationPrivateFiles(tools=tools, artifact=artifact))
            self.assertEqual(value._state, 'NEW')
            self.assertEqual(value._fds, [])
            value.close()

    def test_unsupported_capabilities_refuse_without_provision_io(self):
        value = self.store()
        with patch.object(os, 'O_NOFOLLOW', None, create=True), \
             patch.object(os, 'open', side_effect=AssertionError), \
             patch.object(os, 'stat', side_effect=AssertionError), \
             patch.object(os, 'mkdir', side_effect=AssertionError):
            self.fails(value.provision)
            self.fails(value.provision)
        self.assertEqual(value._fds, [])

    def test_native_windows_has_no_weakened_fallback(self):
        # Native host is Windows for this local batch, not bridged Linux success.
        if os.name == 'nt':
            value = self.store()
            with patch.object(os, 'open', side_effect=AssertionError), \
                 patch.object(os, 'stat', side_effect=AssertionError), \
                 patch.object(os, 'mkdir', side_effect=AssertionError):
                self.fails(value.provision)
            self.assertEqual(value._fds, [])
        else:
            self.assertTrue(os.open in os.supports_dir_fd)


class PrivateFiles(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ['OPENNEXUS_ASSOCIATION_FILES_TEST_ROOT'])
        self.assertTrue(root.is_absolute() and root.resolve() == root and not root.is_symlink())
        self.outer = root
        self.case = Path(tempfile.mkdtemp(prefix='private-files-case-', dir=root))
        self.tools, self.artifact = self.case / 'tools', self.case / 'artifact'
        self.tools.mkdir(); self.artifact.mkdir()
        self.task = self.tools / 'association-private-files'
        self.addCleanup(self.cleanup_case)

    def cleanup_case(self):
        self.assertTrue(self.case.parent == self.outer and self.case.resolve() == self.case
                        and not self.case.is_symlink())
        shutil.rmtree(self.case)
        self.assertFalse(self.case.exists())

    def store(self):
        return subject.OriginalAssociationPrivateFiles(tools=self.tools, artifact=self.artifact)

    def fails(self, action):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_PRIVATE_FILES_UNAVAILABLE$'):
            action()




    def test_real_exclusive_write_fsync_retains_same_original_bytes(self):
        raw = bytes(bytearray(b'{"controlled":"private"}'))
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            self.assertIsNone(value.write('completion', raw))
            entry = value._entries['completion']
            self.assertIs(entry[0], raw)
            self.assertIn(entry[1], value._fds)
            self.assertEqual([e[0] for e in io.events].count('fsync'), 1)
            value.close()
        self.assertEqual((self.task / subject._ROLES['completion'][0]).read_bytes(), raw)

    def test_fixed_roles_and_exact_byte_limits_without_getter_or_admission(self):
        value = self.store()
        with PosixFiles():
            value.provision()
            for role, (_, limit) in subject._ROLES.items():
                raw = b'x' * limit
                value.write(role, raw)
                self.assertIs(value._entries[role][0], raw)
            self.assertEqual({n for n in vars(type(value)) if not n.startswith('_')},
                             {'provision', 'write', 'close'})
            value.close()

    def test_existing_task_or_original_file_never_truncates_or_repairs(self):
        self.task.mkdir()
        value = self.store()
        with PosixFiles():
            self.fails(value.provision)
        self.assertTrue(self.task.is_dir())
        self.task.rmdir()
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            target = self.task / subject._ROLES['completion'][0]
            target.write_bytes(b'original')
            self.fails(lambda: value.write('completion', b'replacement'))
            self.assertFalse(any(e[0] == 'write' for e in io.events))
        self.assertEqual(target.read_bytes(), b'original')

    def test_duplicate_role_burns_store_without_second_open_or_write(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision(); value.write('completion', b'first')
            start = len(io.events)
            self.fails(lambda: value.write('completion', b'second'))
            self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))
            start = len(io.events)
            self.fails(lambda: value.write('stopped', b'new'))
            self.assertEqual(io.events[start:], [])
        self.assertEqual((self.task / subject._ROLES['completion'][0]).read_bytes(), b'first')

    def test_bad_role_or_bytes_refuse_without_coercion_or_later_io(self):
        class Private:
            def __str__(self): raise AssertionError('private coercion')
            def __bytes__(self): raise AssertionError('private coercion')
        for index, (role, raw) in enumerate([(Private(), b'x'), ('completion', Private()),
                ('../private', b'x'), ('completion', bytearray(b'x')), ('completion', b''),
                ('completion', b'x' * 32769), ('stop-command-intent', b'x' * 4097)]):
            tools = self.case / ('tools-' + str(index)); tools.mkdir()
            value = subject.OriginalAssociationPrivateFiles(tools=tools, artifact=self.artifact)
            with PosixFiles() as io:
                value.provision(); start = len(io.events)
                self.fails(lambda: value.write(role, raw))
                self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))

    def test_symlink_reparse_or_wrong_directory_facts_refuse_before_child_creation(self):
        for facts in [dict(st_mode=stat.S_IFLNK | 0o700), dict(st_file_attributes=0x400),
                      dict(st_mode=stat.S_IFREG | 0o600)]:
            value = self.store()
            with PosixFiles() as io:
                io.facts[self.tools] = facts
                self.fails(value.provision)
                self.assertFalse(any(e[0] == 'mkdir' for e in io.events))

    def test_descriptor_alias_and_root_replacement_refuse(self):
        value = self.store()
        with PosixFiles() as io:
            native = io.real_stat(self.tools)
            io.facts[self.artifact] = dict(st_dev=native.st_dev, st_ino=native.st_ino)
            # Bind both observations to the same controlled identity.
            original = io.fstat
            def alias(fd):
                v = original(fd)
                if io.handles[fd][0] == self.artifact:
                    v.st_dev, v.st_ino = native.st_dev, native.st_ino
                return v
            with patch.object(os, 'fstat', alias): self.fails(value.provision)
            self.assertFalse(any(e[0] == 'mkdir' for e in io.events))
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            io.facts[self.tools] = dict(st_ino=io.real_stat(self.tools).st_ino + 1)
            self.fails(lambda: value.write('completion', b'x'))
            self.assertFalse(any(e[0] == 'write' for e in io.events))

    def test_real_hardlink_to_retained_file_is_rejected_before_next_write(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision(); value.write('completion', b'original')
            target = self.task / subject._ROLES['completion'][0]
            os.link(target, self.case / 'controlled-hardlink')
            start = len(io.events)
            self.fails(lambda: value.write('stopped', b'next'))
            self.assertFalse(any(e[0] in ('open', 'write', 'fsync') for e in io.events[start:]))

    def test_file_replacement_and_private_permissions_refuse(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision(); value.write('completion', b'original')
            target = self.task / subject._ROLES['completion'][0]
            io.facts[target] = dict(st_ino=io.real_stat(target).st_ino + 1)
            self.fails(lambda: value.write('stopped', b'next'))
        self.task.joinpath(subject._ROLES['completion'][0]).unlink(); self.task.rmdir()
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            target = self.task / subject._ROLES['completion'][0]
            io.facts[target] = dict(st_mode=stat.S_IFREG | 0o666)
            self.fails(lambda: value.write('completion', b'wrong-mode'))
            self.assertFalse(any(e[0] == 'write' for e in io.events))

    def test_partial_syscall_writes_finish_same_open_once_before_fsync(self):
        value = self.store()
        raw = b'partial-controlled'
        with PosixFiles() as io:
            value.provision()
            io.on_write = lambda fd, data: io.real_write(fd, data[:3])
            value.write('completion', raw)
            self.assertIs(value._entries['completion'][0], raw)
            self.assertEqual(sum(e[0] == 'open' and e[1].name.endswith('.json') for e in io.events), 1)
            self.assertEqual(sum(e[0] == 'fsync' for e in io.events), 1)
            value.close()
        self.assertEqual((self.task / subject._ROLES['completion'][0]).read_bytes(), raw)

    def test_partial_error_preserves_original_and_never_reopens_or_fsyncs(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision(); calls = []
            def partial(fd, data):
                calls.append(1)
                if len(calls) == 1: return io.real_write(fd, data[:2])
                raise OSError('controlled private failure text')
            io.on_write = partial
            self.fails(lambda: value.write('completion', b'original'))
            self.assertEqual(value._entries, {})
            self.assertFalse(any(e[0] == 'fsync' for e in io.events))
            start = len(io.events)
            self.fails(lambda: value.write('completion', b'retry'))
            self.assertEqual(io.events[start:], [])
        self.assertEqual((self.task / subject._ROLES['completion'][0]).read_bytes(), b'or')

    def test_unknown_write_counts_and_fsync_failure_never_retain_success(self):
        for index, count in enumerate([0, True, -1, 100]):
            tools = self.case / ('tools-' + str(index)); tools.mkdir()
            value = subject.OriginalAssociationPrivateFiles(tools=tools, artifact=self.artifact)
            with PosixFiles() as io:
                value.provision(); io.on_write = lambda fd, raw: count
                self.fails(lambda: value.write('completion', b'x'))
                self.assertEqual(value._entries, {})
                self.assertFalse(any(e[0] == 'fsync' for e in io.events))
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            def fail(fd): raise OSError('private fsync error')
            io.on_fsync = fail
            self.fails(lambda: value.write('completion', b'written-not-confirmed'))
            self.assertEqual(value._entries, {})

    def test_reentry_after_open_closes_new_descriptor_without_write(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            io.after_open = lambda path, fd: value.close() if path.name.endswith('.json') else None
            self.fails(lambda: value.write('completion', b'x'))
            self.assertEqual(value._fds, [])
            self.assertFalse(any(e[0] == 'write' for e in io.events))

    def test_closed_store_and_fsync_reentry_do_not_perform_later_io(self):
        value = self.store()
        with PosixFiles() as io:
            value.provision()
            io.on_fsync = lambda fd: value.close()
            self.fails(lambda: value.write('completion', b'x'))
            before = len(io.events)
            value.close(); self.fails(lambda: value.write('stopped', b'x'))
            self.assertEqual(io.events[before:], [])
            self.assertEqual(value._entries, {})

    def test_writer_does_not_read_hash_query_SQL_clock_or_start_process(self):
        value = self.store()
        with PosixFiles():
            value.provision()
            with patch('builtins.open', side_effect=AssertionError), \
                 patch.object(Path, 'open', side_effect=AssertionError), \
                 patch.object(os, 'read', side_effect=AssertionError), \
                 patch.object(hashlib, 'sha256', side_effect=AssertionError), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError), \
                 patch.object(time, 'monotonic', side_effect=AssertionError), \
                 patch.object(subprocess, 'Popen', side_effect=AssertionError):
                self.assertIsNone(value.write('completion', b'opaque-private-not-semantic-approval'))
                value.close()


if __name__ == '__main__':
    unittest.main()
