"""RAM syscall-order models; no actual sandbox, SDK child or filesystem fixture."""
import errno
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
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
                  getattr(a._obj, 'value', a._obj) if hasattr(a, '_obj') else a for a in args]
        return self.function(*values)


class Model:
    def __init__(self):
        self.events, self.abi, self.fail = [], 5, None
        self.denial, self.flags, self.exec_returns = errno.EACCES, 0, False
        self.read_denial = errno.EACCES
        self.stdout_regular = False
        self.library = SimpleNamespace(syscall=Function(self.syscall),
            prctl=Function(self.prctl), fcntl=Function(self.fcntl))
        self.os = SimpleNamespace(uname=lambda: SimpleNamespace(machine='x86_64'),
            execve=self.execve, geteuid=lambda: 1001, fstat=self.fstat,
            readlink=self.readlink, open=self.open, close=self.close,
            set_inheritable=self.inherit, environ={}, supports_fd={self.execve},
            O_ACCMODE=3, O_RDONLY=0, O_WRONLY=1, O_CREAT=64, O_EXCL=128,
            O_NOFOLLOW=256, O_CLOEXEC=512, O_DIRECTORY=1024)

    def syscall(self, number, *args):
        self.events.append(('syscall', number, *args))
        if self.fail == number:
            return -1
        if number == 444:
            return self.abi if args[-1] == 1 else 9
        if number in (91, 93, 190, 280, 2, 257, 0, 17, 9):
            subject.ctypes.set_errno(errno.EPERM)
            return -1
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

    def open(self, path, flags, mode=0o777):
        self.events.append(('open', path, flags, mode))
        denial = self.denial if flags & self.os.O_WRONLY else self.read_denial
        if denial is None:
            return 10
        raise PermissionError(denial, '')

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
            patch.object(subject, 'sys', SimpleNamespace(platform='linux', version_info=(3, 13), byteorder='little')),
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
        self.assertIn(('syscall', 444, 0xffff, 8, 0), e)
        self.assertIn(('prctl', 38, 1, 0, 0, 0), e)
        self.assertLess(e.index(('prctl', 39, 0, 0, 0, 0)), e.index(('syscall', 446, 9, 0)))
        rule = next(x for x in e if x[0:2] == ('syscall', 445))
        self.assertEqual((rule[2:4], rule[4].allowed_access, rule[4].parent_fd, rule[5]),
                         ((9, 1), 1, 3, 0))
        self.assertEqual(e[-1], ('execve', 3, ('/proc/self/fd/3', 'version'), {}))
        self.assertLess(e.index(('inherit', 3, False)), next(i for i, v in enumerate(e)
            if v[0:3] == ('syscall', 317, 1)))
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


class VersionMetadata(unittest.TestCase):
    """Evaluate the actual generated cBPF in RAM; no native sandbox or SDK child."""
    def decide(self, number, arguments=(), arch=0xc000003e):
        data = struct.pack('<iIQ6Q', number, arch, 0,
            *(tuple(v & ((1 << 64) - 1) for v in arguments) + (0,) * (6 - len(arguments))))
        program = subject._version_seccomp_filter()
        index, accumulator = 0, 0
        for _ in range(4096):
            code, yes, no, value = program[index]
            index += 1
            if code == 0x20:
                accumulator = struct.unpack_from('<I', data, value)[0]
            elif code == 0x15:
                index += yes if accumulator == value else no
            elif code == 0x45:
                index += yes if accumulator & value else no
            elif code == 0x06:
                return value
            else:
                self.fail('unhandled actual filter instruction')
        self.fail('actual filter did not terminate')

    def test_other_arch_and_x32_are_killed_before_allowlist(self):
        for arch in (0x40000003, 0, 0xc00000b7):
            self.assertEqual(self.decide(0, arch=arch), subject._SECCOMP_KILL)
        for number in (0x40000000, 0x40000142, -1):
            self.assertEqual(self.decide(number), subject._SECCOMP_KILL)

    def test_metadata_families_and_unknown_numbers_are_denied(self):
        for number in (90, 91, 92, 93, 94, 132, 188, 189, 190, 197, 198,
                       199, 235, 261, 268, 280, 452, 999):
            self.assertEqual(self.decide(number, (3,)), subject._SECCOMP_DENY)

    def test_process_network_policy_ioctl_and_async_io_are_denied(self):
        for number in (16, 41, 42, 56, 57, 58, 59, 101, 157, 165, 206,
                       272, 298, 304, 308, 317, 321, 425, 426, 427, 435, 437):
            self.assertEqual(self.decide(number), subject._SECCOMP_DENY)

    def test_open_flags_allow_readonly_cloexec_and_reject_each_other_bit(self):
        for number, slot in ((2, 1), (257, 2)):
            for flags in (0, 0x80000):
                args = [0] * 6; args[slot] = flags
                self.assertEqual(self.decide(number, args), subject._SECCOMP_ALLOW)
            for flags in [1 << i for i in range(64) if i != 19] + [-1]:
                args = [0] * 6; args[slot] = flags
                self.assertEqual(self.decide(number, args), subject._SECCOMP_DENY)

    def test_only_output_pipe_writes_and_original_sdk_close_is_blocked(self):
        for number in (1, 20):
            for fd in (0, 1, 2, 3, 4, -1):
                expected = subject._SECCOMP_ALLOW if fd in (1, 2) else subject._SECCOMP_DENY
                self.assertEqual(self.decide(number, (fd,)), expected)
        for fd in (0, 1, 2, 3, 4, 50):
            expected = subject._SECCOMP_DENY if fd == 3 else subject._SECCOMP_ALLOW
            self.assertEqual(self.decide(3, (fd,)), expected)

    def test_exec_requires_held_fd_and_exact_empty_path_flag(self):
        self.assertEqual(self.decide(322, (3, 0, 0, 0, 0x1000)), subject._SECCOMP_ALLOW)
        for fd, flags in ((4, 0x1000), (3 + (1 << 32), 0x1000), (3, 0),
                          (3, 0x1100), (3, 0x1000 + (1 << 32))):
            self.assertEqual(self.decide(322, (fd, 0, 0, 0, flags)), subject._SECCOMP_DENY)

    def test_fcntl_and_rlimit_are_readonly(self):
        for command in (0, 1, 2, 3, 4, 1030, (1 << 32) + 1):
            expected = subject._SECCOMP_ALLOW if command in (1, 3) else subject._SECCOMP_DENY
            self.assertEqual(self.decide(72, (3, command)), expected)
        self.assertEqual(self.decide(302), subject._SECCOMP_ALLOW)
        for args in ((1, 0, 0), (0, 0, 1), (0, 0, 1 << 32)):
            self.assertEqual(self.decide(302, args), subject._SECCOMP_DENY)

    def test_loader_read_memory_tls_and_exit_remain_available(self):
        for number in (0, 5, 9, 10, 11, 12, 13, 14, 17, 63, 89, 202, 218,
                       228, 231, 262, 273, 318, 332, 334):
            self.assertEqual(self.decide(number), subject._SECCOMP_ALLOW)
        self.assertEqual(self.decide(158, (0x1002,)), subject._SECCOMP_ALLOW)
        self.assertEqual(self.decide(158, (0x1003,)), subject._SECCOMP_DENY)

    def test_capability_query_is_readonly_and_rejects_unknown_or_private(self):
        for result in (0, 1, True, PrivateValue()):
            model = Model()
            model.library.syscall = Function(lambda *args: result)
            if type(result) is int and result == 0:
                subject._seccomp_api(model.library)
            else:
                with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
                    subject._seccomp_api(model.library)

    def test_child_installs_actual_generated_program_before_seven_denials(self):
        model = Model()
        captured = []
        def syscall(number, operation, flags, program):
            if operation == 1:
                captured.extend((v.code, v.jt, v.jf, v.k)
                    for v in program.filter[:program.len])
            return 0
        original = model.syscall
        model.library.syscall = Function(lambda number, *args:
            syscall(number, *args) if number == 317 else original(number, *args))
        subject._restrict_version_metadata(model.library)
        self.assertEqual(tuple(captured), subject._version_seccomp_filter())
        self.assertEqual([e[1] for e in model.events], [91, 93, 190, 280, 2, 257, 257, 0, 17, 9])
        self.assertFalse(any(e[0] in ('open', 'execve') for e in model.events))

    def test_install_failure_does_not_attempt_denial_probes_or_exec(self):
        model = Model()
        def syscall(number, operation, *args):
            model.events.append((number, operation))
            return -1 if operation == 1 else 0
        model.library.syscall = Function(syscall)
        with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
            subject._restrict_version_metadata(model.library)
        self.assertEqual(model.events, [(317, 2), (317, 1)])

    def test_wrong_errno_or_unexpected_success_stops_after_first_probe(self):
        for result, error in ((0, 0), (-1, errno.EBADF), (True, errno.EPERM),
                              (PrivateValue(), errno.EPERM)):
            model = Model()
            def syscall(number, *args):
                model.events.append(number)
                subject.ctypes.set_errno(error)
                return 0 if number == 317 else result
            model.library.syscall = Function(syscall)
            with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
                subject._restrict_version_metadata(model.library)
            self.assertEqual(model.events, [317, 317, 91])

    def test_actual_windows_rejects_before_loading_native_capabilities(self):
        with patch.object(subject.sys, 'platform', 'win32'), \
                patch.object(subject.ctypes, 'CDLL') as load:
            with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
                subject.PreparedPlatformTools().prepare()
            load.assert_not_called()


class VersionReadPaths(unittest.TestCase):
    setUp = VersionLandlock.setUp
    tearDown = VersionLandlock.tearDown
    fails = VersionLandlock.fails
    no_exec = VersionLandlock.no_exec
    decide = VersionMetadata.decide

    def test_rule_layout_and_exact_file_execute_permission_precede_restrict(self):
        self.assertEqual(subject.ctypes.sizeof(subject._LandlockPath), 12)
        self.assertEqual(subject._LandlockPath.parent_fd.offset, 8)
        with self.assertRaises(ReachedExec):
            subject._confined_version_child()
        e = self.model.events
        add = next(v for v in e if v[:2] == ('syscall', 445))
        self.assertEqual((add[2], add[3], add[4].allowed_access, add[4].parent_fd, add[5]),
                         (9, 1, 1, 3, 0))
        self.assertLess(e.index(add), e.index(('syscall', 446, 9, 0)))
        opens = [v for v in e if v[0] == 'open']
        self.assertEqual([str(v[1]) for v in opens[1:]],
            ['/owned/sdk/platform-tools/adb', '/proc/self/fd/3', '/owned/sdk/platform-tools'])
        self.assertEqual([v[2] for v in opens[1:]], [768, 512, 1792])
        install = next(i for i, v in enumerate(e) if v[:3] == ('syscall', 317, 1))
        self.assertTrue(all(e.index(v) < install for v in opens))

    def test_unknown_rule_layout_rejects_before_loading_native_library(self):
        with patch.object(subject.ctypes, 'sizeof', return_value=16), \
                patch.object(subject.ctypes, 'CDLL') as load:
            self.fails(subject._landlock_api)
            load.assert_not_called()
        self.assertEqual(self.model.events, [])

    def test_failed_or_private_rule_addition_has_no_restrict_open_or_exec(self):
        original = self.model.syscall
        for result in (-1, 1, True, PrivateValue()):
            self.model.events.clear()
            def syscall(number, *args):
                if number == 445:
                    self.model.events.append(('syscall', number, *args))
                    return result
                return original(number, *args)
            self.model.library.syscall = Function(syscall)
            self.fails()
            self.no_exec()
            self.assertFalse(any(e[0] in ('prctl', 'open') or
                e[:2] == ('syscall', 446) for e in self.model.events))

    def test_read_denial_requires_EACCES_and_never_retries_or_executes(self):
        for error in (errno.EPERM, None, True, PrivateValue()):
            self.model.events.clear()
            self.model.read_denial = error
            self.fails()
            self.no_exec()
            self.assertEqual(sum(e[0] == 'open' for e in self.model.events), 2)
            self.assertFalse(any(e[:3] == ('syscall', 317, 1) for e in self.model.events))

    def test_inherited_sdk_reads_and_maps_reject_low32_fd_aliases(self):
        for number, slot in ((0, 0), (17, 0), (9, 4)):
            for fd in (3, 3 + (1 << 32), 3 + (0xffffffff << 32)):
                args = [0] * 6; args[slot] = fd
                self.assertEqual(self.decide(number, args), subject._SECCOMP_DENY)
            for fd in (0, 1, 2, 4, -1):
                args = [0] * 6; args[slot] = fd
                self.assertEqual(self.decide(number, args), subject._SECCOMP_ALLOW)
        for number in (19, 40, 275, 278, 295, 310, 319, 326, 327, 425):
            self.assertEqual(self.decide(number, (3,)), subject._SECCOMP_DENY)

    def test_zero_length_read_probes_never_supply_sdk_buffers(self):
        subject._restrict_version_metadata(self.model.library)
        last = self.model.events[-3:]
        self.assertEqual([e[1] for e in last], [0, 17, 9])
        self.assertEqual(last[0][2:], (3, None, 0))
        self.assertEqual(last[1][2:], (3, None, 0, 0))
        self.assertEqual(last[2][2:], (None, 0, 1, 2, 3, 0))

    def test_wrong_inherited_read_probe_errno_or_private_result_aborts(self):
        original = self.model.syscall
        for result, error in ((0, 0), (-1, errno.EFAULT), (True, errno.EPERM),
                              (PrivateValue(), errno.EPERM)):
            self.model.events.clear()
            def syscall(number, *args):
                if number == 0:
                    self.model.events.append(('syscall', number, *args))
                    subject.ctypes.set_errno(error)
                    return result
                return original(number, *args)
            self.model.library.syscall = Function(syscall)
            self.fails()
            self.no_exec()
            self.assertEqual(self.model.events[-1][1], 0)


class VersionStaticImage(unittest.TestCase):
    @staticmethod
    def image(segment=1):
        raw = bytearray(256)
        struct.pack_into('<16sHHIQQQIHHHHHH', raw, 0,
            b'\x7fELF\x02\x01\x01' + b'\0' * 9, 2, 62, 1, 0x400080,
            64, 0, 0, 64, 56, 1, 0, 0, 0)
        struct.pack_into('<IIQQQQQQ', raw, 64, segment, 5, 0, 0x400000, 0,
                         len(raw), len(raw), 4096)
        return bytes(raw)

    def rejects(self, raw):
        with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
            subject._require_static_version_image(raw)

    def test_plain_static_image_and_invalid_runtime_only_segments(self):
        self.assertIsNone(subject._require_static_version_image(self.image()))
        self.rejects(self.image(2))
        self.rejects(self.image(3))

    def test_private_nonbytes_truncated_and_wrong_arch_never_coerce(self):
        for raw in (PrivateValue(), True, bytearray(self.image()), b'', self.image()[:63]):
            self.rejects(raw)
        for offset, value in ((4, 1), (5, 2), (6, 0), (18, 3), (20, 0)):
            raw = bytearray(self.image()); raw[offset] = value
            self.rejects(bytes(raw))

    def test_program_table_bounds_and_extended_counts_are_rejected(self):
        for offset, fmt, value in ((32, '<Q', 63), (32, '<Q', 250),
                (52, '<H', 63), (54, '<H', 55), (56, '<H', 0),
                (56, '<H', 129), (56, '<H', 0xffff)):
            raw = bytearray(self.image()); struct.pack_into(fmt, raw, offset, value)
            self.rejects(bytes(raw))

    def test_load_bounds_and_actual_file_backed_entry_are_required(self):
        for offset, value in ((24, 0x500000), (72, 257), (96, 257), (104, 255)):
            raw = bytearray(self.image()); struct.pack_into('<Q', raw, offset, value)
            self.rejects(bytes(raw))
        self.rejects(self.image(4))

    def owner_gate(self, raw, *, digest=None, clock=(100,), phase='VERSION_STATIC_IMAGE_GATE'):
        task = subject.PreparedPlatformTools()
        task._binding, task._landlock_abi = (None, 55, (1,)), 5
        native = SimpleNamespace(pread=Mock(return_value=raw), fstat=Mock(return_value=object()),
                                 posix_spawn=Mock())
        digest = hashlib.sha256(raw).hexdigest() if digest is None else digest
        with patch.object(subject.PreparedPlatformTools, 'verify_sdk', return_value=None), \
                patch.object(subject, 'os', native), patch.object(subject, 'Path') as path, \
                patch.object(subject, '_landlock_api', return_value=(None, 5)), \
                patch.object(subject, 'sdk_identity', return_value=(1,)), \
                patch.object(subject, 'ADB_BYTES', len(raw)), \
                patch.object(subject, 'ADB_SHA256', digest), \
                patch.object(subject.time, 'monotonic_ns', side_effect=clock) as sample:
            with self.assertRaisesRegex(RuntimeError, '^' + subject.ERROR + '$'):
                task.probe_version_once()
            path.assert_not_called()
            native.posix_spawn.assert_not_called()
            self.assertFalse(task._child_unknown)
            self.assertIsNone(task._pid)
            self.assertEqual(task._phase, phase)
            native.pread.assert_called_once_with(55, len(raw) + 1, 0)
            return sample.call_count

    def test_actual_owner_gate_rejects_runtime_segments_before_child_setup(self):
        self.assertEqual(self.owner_gate(self.image(3)), 1)

    def test_actual_owner_gate_checks_same_fd_bytes_hash_before_parsing(self):
        self.assertEqual(self.owner_gate(self.image(), digest='0' * 64), 1)

    def test_original_budget_includes_static_validation_without_reset_or_late_io(self):
        self.assertEqual(self.owner_gate(self.image(), clock=(100, 5_000_000_101)), 2)


class VersionDynamicImage(unittest.TestCase):
    owner_gate = VersionStaticImage.owner_gate
    rejects = VersionStaticImage.rejects

    @staticmethod
    def image(tags=((7, 0x400280), (8, 24), (9, 24), (0, 0)), *, interpreter=False):
        raw = bytearray(1024)
        struct.pack_into('<16sHHIQQQIHHHHHH', raw, 0,
            b'\x7fELF\x02\x01\x01' + b'\0' * 9, 3, 62, 1, 0x400100,
            64, 0, 0, 64, 56, 3 if interpreter else 2, 0, 0, 0)
        struct.pack_into('<IIQQQQQQ', raw, 64, 1, 5, 0, 0x400000, 0,
                         len(raw), len(raw), 4096)
        struct.pack_into('<IIQQQQQQ', raw, 120, 2, 6, 512, 0x400200, 0,
                         len(tags) * 16, len(tags) * 16, 8)
        for index, pair in enumerate(tags):
            struct.pack_into('<qQ', raw, 512 + index * 16, *pair)
        if interpreter:
            name = b'/lib64/ld-linux-x86-64.so.2\0'
            raw[800:800 + len(name)] = name
            struct.pack_into('<IIQQQQQQ', raw, 176, 3, 4, 800, 0x400320, 0,
                             len(name), len(name), 1)
        return bytes(raw)

    def test_closed_self_relocation_segment_is_not_an_external_runtime_request(self):
        self.assertIsNone(subject._require_static_version_image(self.image()))
        self.assertIsNone(subject._require_static_version_image(self.image(((0, 0), (0, 0)))))

    def test_actual_interpreter_or_needed_tags_have_exact_private_reason(self):
        for raw, reason in ((self.image(interpreter=True), 1),
                            (self.image(((1, 5), (0, 0))), 2)):
            with self.assertRaises(subject._VersionRuntimeUnsupported) as caught:
                subject._require_static_version_image(raw)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(caught.exception.args, (subject.ERROR,))

    def test_search_and_unknown_dynamic_tags_remain_unsupported(self):
        for tag in (15, 29, -1, 1 << 50):
            with self.assertRaises(subject._VersionRuntimeUnsupported) as caught:
                subject._require_static_version_image(self.image(((tag, 5), (0, 0))))
            self.assertEqual(caught.exception.reason, 3)

    def test_dynamic_segment_bounds_and_loaded_address_mapping_are_required(self):
        for offset, value in ((128, 1024), (136, 0x400201), (152, 0), (152, 17)):
            raw = bytearray(self.image()); struct.pack_into('<Q', raw, offset, value)
            self.rejects(bytes(raw))
        raw = bytearray(self.image()); struct.pack_into('<H', raw, 56, 3)
        raw[176:232] = raw[120:176]
        self.rejects(bytes(raw))

    def test_dynamic_null_termination_and_zero_padding_are_required(self):
        for tags in (((7, 0x400280),), ((0, 1),), ((0, 0), (7, 1)), ((0, 0), (1, 1))):
            self.rejects(self.image(tags))

    def test_actual_owner_maps_only_precise_unsupported_prechild_phase(self):
        for raw, phase in ((self.image(interpreter=True), 'VERSION_IMAGE_PT_INTERP_UNSUPPORTED'),
                (self.image(((1, 5), (0, 0))), 'VERSION_IMAGE_DT_NEEDED_UNSUPPORTED'),
                (self.image(((15, 5), (0, 0))), 'VERSION_IMAGE_DYNAMIC_TAG_UNSUPPORTED')):
            self.assertEqual(self.owner_gate(raw, phase=phase), 1)

    def test_private_bool_or_forged_reason_does_not_select_phase_or_continue(self):
        class ForeignUnsupported(subject._VersionRuntimeUnsupported):
            pass
        for error in [subject._VersionRuntimeUnsupported(v)
                      for v in (True, PrivateValue(), 0, 4, 1.0)] + [ForeignUnsupported(1)]:
            with patch.object(subject, '_require_static_version_image', side_effect=error):
                self.assertEqual(self.owner_gate(self.image()), 1)


if __name__ == '__main__':
    unittest.main()
