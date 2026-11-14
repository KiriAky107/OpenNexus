"""RAM syscall-order models; no actual sandbox, SDK child or filesystem fixture."""
import errno
import io
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

import android_release_platform_tools as subject
from test_android_release_preconfig_sdk import PrivateValue


class ReachedExec(BaseException):
    pass


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        values = [a.value if hasattr(a, 'value') else
                  a._obj.value if hasattr(a, '_obj') else a for a in args]
        return self.function(*values)


class Model:
    def __init__(self):
        self.events, self.abi, self.fail = [], 5, None
        self.denial, self.flags, self.exec_returns = errno.EACCES, 0, False
        self.stdout_regular = False
        self.library = SimpleNamespace(syscall=Function(self.syscall),
            prctl=Function(self.prctl), fcntl=Function(self.fcntl))
        self.os = SimpleNamespace(uname=lambda: SimpleNamespace(machine='x86_64'),
            execve=self.execve, geteuid=lambda: 1001, fstat=self.fstat,
            readlink=self.readlink, open=self.open, close=self.close,
            set_inheritable=self.inherit, environ={}, supports_fd={self.execve},
            O_ACCMODE=3, O_RDONLY=0, O_WRONLY=1, O_CREAT=64, O_EXCL=128,
            O_NOFOLLOW=256, O_CLOEXEC=512)

    def syscall(self, number, *args):
        self.events.append(('syscall', number, *args))
        if self.fail == number:
            return -1
        if number == 444:
            return self.abi if args[-1] == 1 else 9
        return 0

    def prctl(self, number, *args):
        self.events.append(('prctl', number, *args))
        if self.fail == number:
            return -1
        return 1 if number == 39 else 0

    def fcntl(self, fd, command, argument):
        self.events.append(('fcntl', fd, command, argument))
        return self.flags if fd == 3 else 0

    def fstat(self, fd):
        self.events.append(('fstat', fd))
        mode = stat.S_IFCHR if fd == 0 else stat.S_IFREG if fd == 3 or self.stdout_regular else stat.S_IFIFO
        return os.stat_result((mode | 0o600, 1, 1, 1, 1001, 1001, subject.ADB_BYTES, 1, 1, 1))

    def readlink(self, name):
        self.events.append(('readlink', name))
        return '/dev/null' if name.endswith('/0') else '/owned/sdk/platform-tools/adb'

    def open(self, path, flags, mode):
        self.events.append(('open', path, flags, mode))
        if self.denial is None:
            return 10
        raise PermissionError(self.denial, '')

    def close(self, fd):
        self.events.append(('close', fd))

    def inherit(self, fd, value):
        self.events.append(('inherit', fd, value))

    def execve(self, fd, args, env):
        self.events.append(('execve', fd, args, env))
        if not self.exec_returns:
            raise ReachedExec()


class VersionLandlock(unittest.TestCase):
    def setUp(self):
        self.model = Model()
        self.patches = [patch.object(subject, 'os', self.model.os),
            patch.object(subject, 'sys', SimpleNamespace(platform='linux', version_info=(3, 13))),
            patch.object(subject, 'Path', PurePosixPath),
            patch.object(subject.ctypes, 'CDLL', return_value=self.model.library)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def fails(self, call=subject._confined_version_child):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_PLATFORM_TOOLS_PREPARE_UNAVAILABLE$'):
            call()

    def no_exec(self):
        self.assertFalse(any(e[0] == 'execve' for e in self.model.events))

    def test_unsupported_host_gate_precedes_native_library_or_io(self):
        with patch.object(subject, 'sys', SimpleNamespace(platform='win32')), \
                patch.object(subject.ctypes, 'CDLL') as load:
            self.fails(subject._landlock_api)
            load.assert_not_called()
        self.assertEqual(self.model.events, [])

    def test_insufficient_abi_rejects_after_readonly_query_without_policy_or_io(self):
        self.model.abi = 4
        self.fails()
        self.assertEqual(self.model.events, [('syscall', 444, None, 0, 1)])

    def test_private_or_bool_abi_is_not_coerced_or_used(self):
        for value in (True, PrivateValue()):
            self.model.events.clear()
            self.model.abi = value
            self.fails(subject._landlock_api)
            self.assertEqual(self.model.events, [('syscall', 444, None, 0, 1)])

    def test_exact_write_mask_closed_fds_denial_and_held_sdk_exec_order(self):
        with self.assertRaises(ReachedExec):
            subject._confined_version_child()
        e = self.model.events
        self.assertEqual(e[0:2], [('syscall', 444, None, 0, 1), ('syscall', 436, 4, 0xffffffff, 0)])
        self.assertIn(('syscall', 444, 0xfff2, 8, 0), e)
        self.assertIn(('prctl', 38, 1, 0, 0, 0), e)
        self.assertLess(e.index(('prctl', 39, 0, 0, 0, 0)), e.index(('syscall', 446, 9, 0)))
        self.assertFalse(any(x[0] == 'syscall' and x[1] == 445 for x in e))
        self.assertEqual(e[-2:], [('inherit', 3, False), ('execve', 3, ('/proc/self/fd/3', 'version'), {})])
        attempt = next(x for x in e if x[0] == 'open')
        self.assertEqual(attempt[1], PurePosixPath('/owned/sdk/platform-tools/.landlock-write-denial-probe'))
        self.assertLess(e.index(('close', 9)), e.index(attempt))

    def test_privilege_restriction_failure_never_executes_sdk_or_opens_probe(self):
        self.model.fail = 38
        self.fails()
        self.no_exec()
        self.assertFalse(any(e[0] == 'open' for e in self.model.events))

    def test_restrict_self_failure_has_no_sdk_exec_or_denial_probe(self):
        self.model.fail = 446
        self.fails()
        self.no_exec()
        self.assertFalse(any(e[0] == 'open' for e in self.model.events))

    def test_close_range_failure_stops_before_descriptor_or_policy_operations(self):
        self.model.fail = 436
        self.fails()
        self.assertEqual(len(self.model.events), 2)

    def test_inherited_writable_sdk_fd_is_refused(self):
        self.model.flags = 1
        self.fails()
        self.no_exec()
        self.assertFalse(any(e[0] == 'syscall' and e[1] == 446 for e in self.model.events))

    def test_inherited_regular_file_stdout_is_refused(self):
        self.model.stdout_regular = True
        self.fails()
        self.no_exec()

    def test_wrong_denial_errno_does_not_count_as_enforcement(self):
        self.model.denial = errno.EPERM
        self.fails()
        self.no_exec()

    def test_unexpected_successful_create_aborts_without_sdk_exec_or_retry(self):
        self.model.denial = None
        self.fails()
        self.no_exec()
        self.assertEqual(sum(e[0] == 'open' for e in self.model.events), 1)

    def test_returned_exec_is_not_success(self):
        self.model.exec_returns = True
        self.fails()


class ChildEntryPrivacy(unittest.TestCase):
    def test_private_child_entry_exports_only_static_exit_code(self):
        code = compile(Path(subject.__file__).read_bytes(), subject.__file__, 'exec')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', [subject.__file__, '_confined_version_child']), \
                patch.object(sys, 'platform', 'win32'), \
                patch.object(os, '_exit', side_effect=ReachedExec) as stop, \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(ReachedExec):
                exec(code, {'__name__': '__main__'})
        stop.assert_called_once_with(91)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))


if __name__ == '__main__':
    unittest.main()
