"""RAM SDK descriptor-tree model; no actual Linux executable/child acceptance."""
import builtins
from contextlib import contextmanager, ExitStack
from copy import copy, deepcopy
import hashlib
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sync_association_config_executable as executable
from android_release_sync_association_config_host import _ARGUMENTS


class Private:
    def __str__(self):
        raise AssertionError('private coercion')

    def __fspath__(self):
        raise AssertionError('private path coercion')


class Sdk:
    name = 'posix'
    O_RDONLY, O_NOFOLLOW, O_DIRECTORY, O_CLOEXEC, O_NONBLOCK = 0, 1, 2, 4, 8

    def __init__(self):
        self.live, self.events, self.next_fd = {}, [], 20
        self.hook, self.pid, self.close_failure = None, 4242, False
        self.inheritable, self.proc_target = False, '/owned/sdk/platform-tools/adb'
        self.header = b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00'
        names = ['/', '/owned', '/owned/sdk', '/owned/sdk/platform-tools', self.proc_target]
        self.nodes = {name: SimpleNamespace(st_dev=1, st_ino=i + 1, st_uid=1000,
            st_mode=(stat.S_IFREG | 0o755) if i == 4 else (stat.S_IFDIR | 0o755),
            st_nlink=1, st_size=128, st_mtime_ns=1, st_ctime_ns=1)
            for i, name in enumerate(names)}
        self.supports_dir_fd = {self.open, self.stat}
        self.supports_follow_symlinks = {self.stat}

    def event(self, name, *args):
        self.events.append((name, *args))
        if self.hook:
            self.hook(name, *args)

    def path(self, name, parent):
        return name if parent is None else self.live[parent].rstrip('/') + '/' + name

    def stat(self, name, *, dir_fd=None, follow_symlinks=True):
        self.event('stat', name, dir_fd, follow_symlinks)
        if name.startswith('/proc/'):
            return self.nodes[self.live[int(name.rsplit('/', 1)[-1])]]
        return copy(self.nodes[self.path(name, dir_fd)])

    def open(self, name, flags, *, dir_fd=None):
        fd = self.next_fd; self.next_fd += 1
        self.live[fd] = self.path(name, dir_fd)
        self.event('open', name, flags, dir_fd, fd)
        return fd

    def fstat(self, fd):
        self.event('fstat', fd)
        return copy(self.nodes[self.live[fd]])

    def geteuid(self):
        self.event('geteuid')
        return 1000

    def getpid(self):
        self.event('getpid')
        return self.pid

    def get_inheritable(self, fd):
        self.event('inheritable', fd)
        return self.inheritable

    def pread(self, fd, length, offset):
        self.event('pread', fd, length, offset)
        return self.header

    def readlink(self, name):
        self.event('readlink', name)
        return self.proc_target

    def close(self, fd):
        self.live.pop(fd)
        self.event('close', fd)
        if self.close_failure:
            raise RuntimeError(Private())


@contextmanager
def model():
    sdk = Sdk()
    with patch.object(executable, 'os', sdk), patch.object(executable, 'sys', SimpleNamespace(platform='linux')):
        yield sdk


class Executable(unittest.TestCase):
    def unavailable(self, action):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_EXECUTABLE_UNAVAILABLE$'):
            action()

    def cleaned(self, sdk):
        self.assertEqual(sdk.live, {})
        closed = [x[1] for x in sdk.events if x[0] == 'close']
        self.assertEqual(len(closed), len(set(closed)))

    def test_constructor_zero_io_fixed_arguments_and_no_public_launch_or_raw_getter(self):
        with model() as sdk:
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
            self.assertEqual(sdk.events, [])
            self.assertEqual([n for n in dir(owner) if not n.startswith('_')], ['close', 'pin', 'verify'])
            self.assertNotIn('__del__', type(owner).__dict__)
            self.assertNotIn('__exit__', type(owner).__dict__)
            for value in (Private(), None, '', '/', 'relative', '//owned', '/owned/', '/owned/../sdk',
                    '/owned/./sdk', '/owned//sdk', '/owned/secret\n', '/a/' + 'x' * 97, '/' + '/'.join(['a'] * 33)):
                self.unavailable(lambda: executable.OriginalAssociationConfigExecutable(value))
            self.assertEqual(sdk.events, [])
            owner.close()

    def test_actual_windows_refuses_before_any_native_io(self):
        owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
        with ExitStack() as stack:
            if sys.platform != 'win32':
                stack.enter_context(patch.object(executable, 'sys', SimpleNamespace(platform='win32')))
            stack.enter_context(patch.object(executable.os, 'open', side_effect=AssertionError('unexpected native IO')))
            stack.enter_context(patch.object(executable.os, 'close', side_effect=AssertionError('unexpected native close')))
            self.unavailable(owner.pin)
        self.assertEqual(owner._fds, [])

    def test_missing_capabilities_refuse_before_open(self):
        for missing in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC', 'O_NONBLOCK', 'pread', 'readlink', 'dir_fd'):
            with model() as sdk:
                if missing == 'dir_fd':
                    sdk.supports_dir_fd = set()
                else:
                    setattr(sdk, missing, None)
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
                self.unavailable(owner.pin)
                self.assertEqual(sdk.events, [])

    def test_pin_uses_exact_native_argv_original_parent_alias_and_twenty_sdk_bytes_once(self):
        with model() as sdk:
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
            self.assertIsNone(owner.pin())
            self.assertEqual(owner._state, 'READY')
            self.assertEqual(owner._arguments, ('/owned/sdk/platform-tools/adb', '-s', 'emulator-5562', *_ARGUMENTS))
            self.assertEqual(owner._executable, '/proc/4242/fd/24')
            self.assertEqual(owner._fds, [20, 21, 22, 23, 24])
            self.assertEqual([x for x in sdk.events if x[0] == 'pread'], [('pread', 24, 20, 0)])
            opened = [x for x in sdk.events if x[0] == 'open']
            self.assertEqual(opened[0], ('open', '/', 7, None, 20))
            self.assertEqual(opened[-1], ('open', 'adb', 13, 23, 24))
            self.assertTrue(all(x[3] is False for x in sdk.events if x[0] == 'stat' and not x[1].startswith('/proc/')))
            self.assertIsNone(owner.verify())
            self.assertEqual(len([x for x in sdk.events if x[0] == 'pread']), 1)
            owner.close(); self.cleaned(sdk)

    def test_named_chain_file_ownership_type_mode_links_and_limits_fail_without_launch(self):
        cases = [('st_mode', stat.S_IFLNK | 0o777), ('st_uid', 2000), ('st_nlink', 2),
            ('st_mode', stat.S_IFREG | 0o777), ('st_mode', stat.S_IFREG | 0o4755),
            ('st_mode', stat.S_IFREG | 0o644), ('st_size', 19), ('st_size', 128 * 1024 * 1024 + 1), ('st_ino', True)]
        for key, value in cases:
            with model() as sdk:
                setattr(sdk.nodes['/owned/sdk/platform-tools/adb'], key, value)
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
                self.unavailable(owner.pin); self.cleaned(sdk)
                self.assertFalse(any(x[0] == 'pread' for x in sdk.events))
        with model() as sdk:
            sdk.nodes['/owned'].st_mode = stat.S_IFLNK | 0o777
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
            self.unavailable(owner.pin); self.cleaned(sdk)
            self.assertEqual(len([x for x in sdk.events if x[0] == 'open']), 1)

    def test_header_unknown_private_short_or_wrong_abi_closes_only_new_local_descriptors(self):
        for header in (Private(), b'', b'#!' + bytes(18), b'\x7fELF\x01\x01\x01' + bytes(13),
                b'\x7fELF\x02\x01\x01' + bytes(11) + b'\xb7\x00'):
            with model() as sdk:
                sdk.header = header
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
                self.unavailable(owner.pin); self.cleaned(sdk)
                self.assertEqual(len([x for x in sdk.events if x[0] == 'pread']), 1)

    def test_post_open_identity_inheritance_or_private_failure_registers_fd_before_refusal(self):
        for mode in ('changed', 'inheritable', 'private'):
            with model() as sdk:
                def hook(event, *args):
                    if event == 'fstat' and args[0] == 24:
                        sdk.hook = None
                        if mode == 'changed': sdk.nodes[sdk.live[24]].st_ino += 1
                        if mode == 'inheritable': sdk.inheritable = True
                        if mode == 'private': raise RuntimeError(Private())
                sdk.hook = hook
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
                self.unavailable(owner.pin); self.cleaned(sdk)
                self.assertIn(('close', 24), sdk.events)

    def test_verify_rejects_changed_name_parent_pid_proc_alias_or_metadata_without_reread(self):
        for mode in ('identity', 'pid', 'proc', 'inheritable'):
            with model() as sdk:
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk'); owner.pin()
                if mode == 'identity': sdk.nodes['/owned/sdk'].st_ino += 1
                if mode == 'pid': sdk.pid += 1
                if mode == 'proc': sdk.proc_target += '-other'
                if mode == 'inheritable': sdk.inheritable = True
                self.unavailable(owner.verify); self.cleaned(sdk)
                self.assertEqual(len([x for x in sdk.events if x[0] == 'pread']), 1)

    def test_copy_or_deepcopy_never_closes_original_fds(self):
        for factory in (copy, deepcopy):
            with model() as sdk:
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk'); owner.pin()
                cloned = factory(owner); before = list(sdk.events)
                self.unavailable(cloned.close)
                self.unavailable(cloned.verify)
                self.assertEqual(sdk.events, before)
                owner.close(); self.cleaned(sdk)

    def test_unknown_pid_or_private_proc_target_refused_before_equality_or_next_proc_stat(self):
        class Foreign:
            compared = False
            def __eq__(self, other):
                Foreign.compared = True
                raise AssertionError('private equality callback')
        for mode in ('bool-pid', 'private-pid', 'private-proc'):
            with model() as sdk:
                sdk.pid = 1
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk'); owner.pin()
                before = len(sdk.events)
                if mode == 'bool-pid': sdk.pid = True
                if mode == 'private-pid': sdk.pid = Foreign()
                if mode == 'private-proc': sdk.proc_target = Foreign()
                self.unavailable(owner.verify); self.cleaned(sdk)
                self.assertFalse(Foreign.compared)
                following = sdk.events[before:]
                self.assertFalse(any(x[0] == 'stat' and x[1].startswith('/proc/') for x in following))
                if mode != 'private-proc':
                    self.assertFalse(any(x[0] == 'readlink' for x in following))

    def test_reentry_during_open_header_or_proc_read_burns_permission_without_second_pin(self):
        for trigger in ('open', 'pread', 'readlink'):
            with model() as sdk:
                owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
                def hook(event, *args):
                    if event == trigger:
                        sdk.hook = None
                        self.unavailable(owner.pin)
                sdk.hook = hook
                self.unavailable(owner.pin); self.cleaned(sdk)
                before = list(sdk.events)
                self.unavailable(owner.pin)
                self.assertEqual(sdk.events, before)

    def test_duplicate_pin_closes_once_without_reopen_or_second_header(self):
        with model() as sdk:
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk'); owner.pin()
            self.unavailable(owner.pin); self.cleaned(sdk)
            self.assertEqual(len([x for x in sdk.events if x[0] == 'open']), 5)
            self.assertEqual(len([x for x in sdk.events if x[0] == 'pread']), 1)

    def test_uncertain_close_is_retained_without_descriptor_retry(self):
        with model() as sdk:
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk'); owner.pin()
            sdk.close_failure = True
            self.unavailable(owner.close)
            self.assertEqual(set(owner._uncertain_close), {20, 21, 22, 23, 24})
            before = list(sdk.events)
            self.unavailable(owner.close)
            self.assertEqual(sdk.events, before); self.cleaned(sdk)

    def test_no_real_file_private_hash_sql_clock_process_or_automatic_cleanup(self):
        with model() as sdk, ExitStack() as stack:
            def prohibited(*args, **kwargs):
                raise AssertionError('forbidden side effect')
            for target, name in ((builtins, 'open'), (Path, 'read_bytes'), (Path, 'write_bytes'),
                    (Path, 'unlink'), (hashlib, 'sha256'), (sqlite3, 'connect'),
                    (subprocess, 'Popen'), (subprocess, 'run')):
                stack.enter_context(patch.object(target, name, prohibited))
            owner = executable.OriginalAssociationConfigExecutable('/owned/sdk')
            owner.pin(); owner.verify(); owner.close(); owner.close(); self.cleaned(sdk)
            # An uninspected same-metadata edit is not detected or claimed immutable.
            self.assertEqual(len([x for x in sdk.events if x[0] == 'pread']), 1)


if __name__ == '__main__':
    unittest.main()
