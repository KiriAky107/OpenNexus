"""RAM actor/descriptor/native-call model; no real process, SDK or acceptance."""
import builtins
from contextlib import contextmanager, ExitStack
from copy import copy
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sync_association_config_kernel as kernel
import android_release_sync_association_config_host as config
import android_release_sync_association_config_channels as channels
import android_release_sync_association_config_journals as journals
import android_release_sync_association_config_resources as resources
import android_release_sync_association_config_executable as executable
import android_release_sync_association_config_native_intent as native
import android_release_sync_association_private_files as files
from test_android_release_sync_association_config_host import Ports, context
from test_android_release_sync_association_config_resources import environment
from test_android_release_sync_association_config_executable import model


class Private:
    def __str__(self): raise AssertionError('private coercion')


class NativeOS:
    name = 'posix'
    POSIX_SPAWN_DUP2, POSIX_SPAWN_CLOSEFROM, WNOHANG = 2, 3, 1
    O_ACCMODE, O_RDONLY, O_WRONLY, F_GETFL = 3, 0, 1, 3

    def __init__(self, ram, sdk, clock):
        self.ram, self.sdk, self.clock = ram, sdk, clock
        self.events, self.pidfds, self.waits, self.reads = [], set(), [(4444, 0)], [b'', b'']
        self.pid, self.pidfd = 4444, 300
        self.spawn_error = self.pidfd_error = False
        self.before = self.on_spawn = self.on_pidfd = self.on_wait = None
        self.close_failure = None
        self.stalled, self.s = False, None

    def fstat(self, fd): return self.ram.fstat(fd)
    def get_blocking(self, fd): return self.ram.get_blocking(fd)
    def get_inheritable(self, fd): return self.ram.get_inheritable(fd)
    def fcntl(self, fd, operation): return self.ram.fcntl(fd, operation)
    def getpid(self): return self.sdk.getpid()

    def posix_spawn(self, path, arguments, env, *, file_actions):
        self.events.append(('spawn', path, arguments, env, file_actions))
        if self.on_spawn: self.on_spawn(self.s)
        if self.spawn_error: raise RuntimeError(Private())
        return self.pid

    def pidfd_open(self, pid, flags):
        self.events.append(('pidfd', pid, flags))
        if self.on_pidfd: self.on_pidfd(self.s)
        if self.pidfd_error: raise RuntimeError(Private())
        if type(self.pidfd) is int: self.pidfds.add(self.pidfd)
        return self.pidfd

    def waitpid(self, pid, options):
        self.events.append(('poll', pid, options))
        if self.on_wait: self.on_wait(self.s)
        value = self.waits.pop(0) if len(self.waits) > 1 else self.waits[0]
        if isinstance(value, BaseException): raise value
        return value

    def read(self, fd, size):
        self.events.append(('read', fd, size))
        value = self.reads.pop(0)
        if isinstance(value, BaseException): raise value
        return value

    def close(self, fd):
        self.events.append(('close', fd))
        if fd in self.ram.live: self.ram.close(fd)
        elif fd in self.sdk.live: self.sdk.close(fd)
        else: self.pidfds.remove(fd)
        if fd == self.close_failure: raise RuntimeError(Private())

    def sleep(self, seconds):
        self.events.append(('sleep', seconds))
        if not self.stalled: self.clock.values[0] += int(seconds * 1_000_000_000)


@contextmanager
def fixture():
    with environment() as (ram, clock), model() as sdk, ExitStack() as stack:
        ram.next_fd, sdk.next_fd = 100, 200
        osmodel = NativeOS(ram, sdk, clock)
        stack.enter_context(patch.object(kernel, 'os', osmodel))
        stack.enter_context(patch.object(kernel, 'fcntl', osmodel))
        stack.enter_context(patch.object(kernel, 'sys', SimpleNamespace(platform='linux', version_info=(3, 13))))
        stack.enter_context(patch.object(kernel, 'time', SimpleNamespace(sleep=osmodel.sleep)))
        stack.enter_context(patch.object(kernel, '_OWNERS', {}))
        stack.enter_context(patch.object(kernel, '_LOCK', threading.Lock()))
        s = SimpleNamespace(os=osmodel, ram=ram, sdk=sdk, clock=clock, native=None, kernel=None)
        class SourcePorts(Ports):
            def record_config_intent(self, raw):
                self.event('intent'); s.journals.record_config_intent(raw)
                s.owner.claim(); s.pin.pin()
            def stage_config(self, arguments, original_input, before_send, **limits):
                self.event('stage')
                s.native = native.OriginalAssociationConfigNativeIntent(s.owner, s.pin); s.native.record()
                if osmodel.before: osmodel.before(s)
                if s.kernel is None: s.kernel = kernel.OriginalAssociationConfigKernel(s.actor, s.native)
                return s.kernel.run()
            def record_config_result(self, raw):
                self.event('result'); s.journals.record_config_result(raw)
        s.ports = SourcePorts(); s.actor = config.OriginalAssociationConfigStaging(s.ports, **context())
        s.store = files.OriginalAssociationPrivateFiles(tools=Path('C:/controlled/tools'), artifact=Path('C:/controlled/artifact'))
        s.store._state, s.store._fds = 'READY', [900]
        def writer(role, raw):
            assert role not in s.store._entries
            fd = 901 + len(s.store._entries); s.store._fds.append(fd); s.store._attempted.add(role)
            s.store._entries[role] = (raw, fd, files._ROLES[role][0], (1, 2, 1000, 0o100600))
        stack.enter_context(patch.object(s.store, 'write', writer))
        s.journals = journals.OriginalAssociationConfigJournals(s.store)
        channel = channels.OriginalAssociationConfigChannels(s.actor._input)
        channel.provision()
        s.owner = resources.OriginalAssociationConfigResources(channel, s.journals)
        s.pin = executable.OriginalAssociationConfigExecutable('/owned/sdk')
        s.ports.monotonic_ms = s.owner.monotonic_ms
        osmodel.s = s
        try:
            yield s
        finally:
            # Only unconsumed pre-spawn sources may be released. Unknown model
            # children stay strongly owned until this RAM model's lifetime ends.
            if s.owner._state in ('NEW', 'READY'): s.owner.close()
            if channel._handle_owner is channel and channel._state == 'READY': channel.close()
            if s.pin._state in ('NEW', 'READY'): s.pin.close()


class Kernel(unittest.TestCase):
    def refused(self, s):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_STAGING_UNAVAILABLE$'): s.actor.run()

    def unknown(self, s):
        self.assertIs(kernel._OWNERS[id(s.kernel)], s.kernel)
        self.assertTrue(s.kernel._attempted)
        self.assertIs(s.owner._channels._handle_owner, s.kernel)
        self.assertIs(s.pin._handle_owner, s.kernel)
        self.assertEqual(s.kernel._state, 'FAILED')

    def test_constructor_zero_IO_original_objects_only_and_no_cleanup_or_raw_getter(self):
        with fixture() as s:
            before = (list(s.ram.events), list(s.sdk.events), s.clock.calls)
            value = kernel.OriginalAssociationConfigKernel(s.actor, native.OriginalAssociationConfigNativeIntent(s.owner, s.pin))
            self.assertEqual([n for n in dir(value) if not n.startswith('_')], ['run'])
            self.assertNotIn('__del__', type(value).__dict__)
            self.assertNotIn('__exit__', type(value).__dict__)
            for a, n in ((None, None), (object(), value), (s.actor, object())):
                with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_KERNEL_UNAVAILABLE$'): kernel.OriginalAssociationConfigKernel(a, n)
            self.assertEqual(before, (s.ram.events, s.sdk.events, s.clock.calls))

    def test_actual_windows_and_missing_native_capabilities_refuse_without_consumption(self):
        for mode in ('actual-windows', 'python312', 'CLOSEFROM'):
            with fixture() as s:
                if mode == 'actual-windows':
                    s.os.before = lambda _: setattr(kernel, 'sys', sys if sys.platform == 'win32' else SimpleNamespace(platform='win32', version_info=(3, 13)))
                if mode == 'python312': kernel.sys.version_info = (3, 12)
                if mode == 'CLOSEFROM': s.os.POSIX_SPAWN_CLOSEFROM = None
                self.refused(s)
                self.assertEqual(s.os.events, []); self.assertFalse(kernel._OWNERS)
                self.assertEqual(s.owner._state, 'READY'); self.assertEqual(s.pin._state, 'READY')

    def test_actual_actor_same_clock_guard_journal_spawn_known0_empty_and_release_order(self):
        with fixture() as s:
            self.assertIsNone(s.actor.run())
            k = s.kernel
            self.assertEqual(k._state, 'DONE'); self.assertEqual(k._first_exit, (4444, 0))
            self.assertIs(type(k._first_seen), int)
            spawn = next(e for e in s.os.events if e[0] == 'spawn')
            self.assertEqual(spawn[1], '/proc/4242/fd/204'); self.assertIsNone(spawn[3])
            self.assertEqual(spawn[4], ((2, 100, 0), (2, 103, 1), (2, 105, 2), (3, 3)))
            self.assertEqual(sum(e[0] == 'spawn' for e in s.os.events), 1)
            self.assertEqual(sum(e[0] == 'poll' for e in s.os.events), 1)
            self.assertEqual([e for e in s.os.events if e[0] == 'read'], [('read', 102, 1), ('read', 104, 1)])
            self.assertIs(k._sources[0], s.actor._intent); self.assertIs(k._sources[1], s.actor._input)
            self.assertEqual(set(s.store._entries), {'config-command-intent', 'config-native-command-intent', 'config-command-result'})
            self.assertFalse(kernel._OWNERS); self.assertFalse(resources._PRESPAWN_OWNERS)
            self.assertFalse(s.ram.live); self.assertFalse(s.sdk.live); self.assertFalse(s.os.pidfds)

    def test_outside_close_after_both_moves_cannot_release_originals_or_break_source_clock(self):
        with fixture() as s:
            rejected = []
            def attempt():
                if s.owner._state == 'TRANSFERRED':
                    for action in (s.owner.close, s.pin.close, s.owner._channels.close):
                        with self.assertRaises(RuntimeError): action()
                        rejected.append(True)
            s.ports.hooks['owner'] = attempt
            s.actor.run(); self.assertEqual(len(rejected), 3)

    def test_wrapper_clock_or_copied_source_is_not_original_actor_clock_provenance(self):
        for mode in ('clock-wrapper', 'actor-copy', 'native-copy'):
            with fixture() as s:
                def before(value):
                    if mode == 'clock-wrapper': value.ports.monotonic_ms = lambda: value.owner.monotonic_ms()
                    elif mode == 'actor-copy': value.kernel = kernel.OriginalAssociationConfigKernel(copy(value.actor), value.native)
                    else: value.kernel = kernel.OriginalAssociationConfigKernel(value.actor, copy(value.native))
                s.os.before = before; self.refused(s)
                self.assertEqual(s.os.events, []); self.assertFalse(kernel._OWNERS)

    def test_original_final_guard_failure_prevents_send_after_transfers(self):
        with fixture() as s:
            def fail():
                if s.owner._state == 'TRANSFERRED': s.ports.owner_result = object()
            s.ports.hooks['owner'] = fail; self.refused(s)
            self.assertFalse(any(e[0] == 'spawn' for e in s.os.events))
            self.assertFalse(s.ram.live); self.assertFalse(s.sdk.live); self.assertFalse(kernel._OWNERS)

    def test_failure_between_either_permission_move_keeps_one_owner_and_closes_once(self):
        original = executable.OriginalAssociationConfigExecutable._consume_for_kernel
        for after in (False, True):
            with fixture() as s:
                def fail(pin, value):
                    if after: original(pin, value)
                    raise RuntimeError(Private())
                with patch.object(executable.OriginalAssociationConfigExecutable, '_consume_for_kernel', fail): self.refused(s)
                self.assertFalse(s.ram.live); self.assertFalse(kernel._OWNERS)
                closed = [e[1] for e in s.os.events if e[0] == 'close']
                self.assertEqual(len(closed), len(set(closed)))
                self.assertEqual(s.pin._state, 'TRANSFERRED' if after else 'READY')

    def test_unknown_spawn_exception_or_private_PID_keeps_originals_without_any_later_IO(self):
        for value in ('exception', True, Private()):
            with fixture() as s:
                if value == 'exception': s.os.spawn_error = True
                else: s.os.pid = value
                self.refused(s); self.unknown(s)
                self.assertEqual([e[0] for e in s.os.events], ['spawn'])

    def test_PID_is_registered_before_pidfd_and_unknown_pidfd_does_not_poll_or_close(self):
        for value in ('exception', Private()):
            with fixture() as s:
                s.os.on_pidfd = lambda value: self.assertEqual(value.kernel._pid, 4444)
                if value == 'exception': s.os.pidfd_error = True
                else: s.os.pidfd = value
                self.refused(s); self.unknown(s)
                self.assertEqual([e[0] for e in s.os.events], ['spawn', 'pidfd'])

    def test_unknown_private_or_nonzero_poll_has_no_later_clock_read_or_poll(self):
        for result in ((4444, 256), (True, 0), Private(), (0, 1), RuntimeError(Private())):
            with fixture() as s:
                s.os.waits = [result]; observed = []
                s.os.on_wait = lambda _: observed.append(s.clock.calls)
                self.refused(s); self.unknown(s)
                self.assertEqual(s.clock.calls, observed[0])
                self.assertEqual(sum(e[0] == 'poll' for e in s.os.events), 1)
                self.assertFalse(any(e[0] == 'read' for e in s.os.events))

    def test_first_zero_only_once_bounded_empty_reads_and_no_private_output_on_failure(self):
        for values in ([b'private'], [b'', Private()], [BlockingIOError()]):
            with fixture() as s:
                s.os.reads = list(values); self.refused(s); self.unknown(s)
                self.assertEqual(s.kernel._first_exit, (4444, 0))
                self.assertIs(type(s.kernel._first_seen), int)
                self.assertEqual(sum(e[0] == 'read' for e in s.os.events), len(values))
                self.assertEqual(sum(e[0] == 'poll' for e in s.os.events), 1)

    def test_stalled_clock_pending_is_bounded_without_deadline_reset_or_extra_polls(self):
        with fixture() as s:
            s.os.waits, s.os.stalled = [(0, 0)], True
            self.refused(s); self.unknown(s)
            self.assertEqual(sum(e[0] == 'poll' for e in s.os.events), 201)
            waits = [e[1] for e in s.os.events if e[0] == 'sleep']
            self.assertEqual(len(waits), 200); self.assertTrue(all(0 < x <= 0.05 for x in waits))
            self.assertIsNone(s.kernel._first_seen)

    def test_expired_before_send_or_late_native_return_never_becomes_accepted(self):
        for late in (False, True):
            with fixture() as s:
                def expire(value): value.clock.values[0] = 20_000_000_000
                if late: s.os.on_spawn = expire
                else: s.os.before = expire
                self.refused(s)
                self.assertFalse(any(e[0] in ('poll', 'read', 'pidfd') for e in s.os.events))
                if late: self.unknown(s); self.assertEqual(s.kernel._pid, 4444)
                else: self.assertFalse(any(e[0] == 'spawn' for e in s.os.events))

    def test_spawn_reentry_is_sticky_even_when_native_callback_swallows_it(self):
        with fixture() as s:
            def reenter(value):
                with self.assertRaises(RuntimeError): value.kernel.run()
            s.os.on_spawn = reenter; self.refused(s); self.unknown(s)
            self.assertEqual([e[0] for e in s.os.events], ['spawn'])

    def test_uncertain_child_duplicate_close_is_never_retried_or_followed_by_poll(self):
        with fixture() as s:
            s.os.close_failure = 100; self.refused(s); self.unknown(s)
            before = list(s.os.events)
            with self.assertRaises(RuntimeError): s.kernel.run()
            self.assertEqual(before, s.os.events)
            self.assertEqual(s.kernel._uncertain, [100])
            self.assertFalse(any(e[0] == 'poll' for e in s.os.events))

    def test_copy_of_unknown_kernel_cannot_poll_close_or_drop_original_registry(self):
        with fixture() as s:
            s.os.spawn_error = True; self.refused(s)
            value = copy(s.kernel); before = list(s.os.events)
            with self.assertRaises(RuntimeError): value.run()
            self.assertEqual(before, s.os.events); self.unknown(s)

    def test_no_real_Popen_file_SQL_hash_environment_export_or_automatic_cleanup(self):
        with fixture() as s, ExitStack() as stack:
            calls = []
            def forbidden(*args, **kwargs): calls.append(True); raise AssertionError('forbidden IO')
            for target, name in ((builtins, 'open'), (Path, 'read_bytes'), (Path, 'open'),
                    (subprocess, 'Popen'), (subprocess, 'run'), (sqlite3, 'connect'), (hashlib, 'sha256')):
                stack.enter_context(patch.object(target, name, forbidden))
            s.actor.run(); self.assertEqual(calls, [])
            self.assertIsNone(next(e for e in s.os.events if e[0] == 'spawn')[3])


if __name__ == '__main__':
    unittest.main()
