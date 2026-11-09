"""Controlled RAM syscalls only; no native Linux pipe/process/device acceptance."""
import builtins
from contextlib import ExitStack, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sync_association_config_channels as channels


class RamPipes:
    name = 'posix'
    O_CLOEXEC, O_ACCMODE, O_RDONLY, O_WRONLY, F_GETFL = 524288, 3, 0, 1, 3

    def __init__(self):
        self.events, self.live, self.hooks = [], {}, {}
        self.next_fd = 10
        self.pipe_buf = 4096
        self.write_result = None
        self.close_error = None
        self.close_return = None
        self.writes = []

    def event(self, name, *args):
        self.events.append((name, *args))
        hook = self.hooks.get(name)
        if hook:
            hook(*args)

    def pipe2(self, flags):
        self.event('pipe2', flags)
        pair = (self.next_fd, self.next_fd + 1)
        self.next_fd += 2
        for index, fd in enumerate(pair):
            self.live[fd] = dict(ino=pair[0], uid=1000, mode=stat.S_IFIFO | 0o600,
                nlink=1, blocking=True, inheritable=False, access=index)
        return pair

    def fstat(self, fd):
        self.event('fstat', fd)
        value = self.live[fd]
        return SimpleNamespace(st_dev=5, st_ino=value['ino'], st_uid=value['uid'],
            st_mode=value['mode'], st_nlink=value['nlink'])

    def geteuid(self):
        self.event('geteuid')
        return 1000

    def fpathconf(self, fd, name):
        self.event('fpathconf', fd, name)
        return self.pipe_buf

    def get_inheritable(self, fd):
        self.event('get_inheritable', fd)
        return self.live[fd]['inheritable']

    def get_blocking(self, fd):
        self.event('get_blocking', fd)
        return self.live[fd]['blocking']

    def set_blocking(self, fd, value):
        self.event('set_blocking', fd, value)
        self.live[fd]['blocking'] = value

    def fcntl(self, fd, operation):
        self.event('fcntl', fd, operation)
        return self.live[fd]['access']

    def write(self, fd, raw):
        self.event('write', fd)
        self.writes.append((fd, raw))
        return len(raw) if self.write_result is None else self.write_result

    def close(self, fd):
        self.event('close', fd)
        self.live.pop(fd)  # Reusing this number after close must never be retried.
        if fd == self.close_error:
            raise InterruptedError('private close error must not be exported')
        return self.close_return


@contextmanager
def controlled(ram):
    with patch.object(channels, 'os', ram), patch.object(channels, 'fcntl', ram), \
            patch.object(channels, 'sys', SimpleNamespace(platform='linux')):
        yield


class ConfigChannels(unittest.TestCase):
    def unavailable(self, function):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_CHANNELS_UNAVAILABLE$'):
            function()

    def closed_once(self, ram):
        self.assertEqual(ram.live, {})
        values = [event[1] for event in ram.events if event[0] == 'close']
        self.assertEqual(len(values), len(set(values)))

    def test_constructor_zero_io_exact_private_input_and_no_public_handoff(self):
        ram = RamPipes()
        with controlled(ram):
            raw = bytes(bytearray(b'private stdin'))
            actor = channels.OriginalAssociationConfigChannels(raw)
            self.assertIs(actor._input, raw)
            self.assertEqual(ram.events, [])
            for value in (None, True, '', b'', bytearray(b'p'), memoryview(b'p'), b'p'*2049):
                self.unavailable(lambda: channels.OriginalAssociationConfigChannels(value))
            class Private:
                def __str__(self):
                    raise AssertionError('must not stringify private')
            self.unavailable(lambda: channels.OriginalAssociationConfigChannels(Private()))
            self.assertEqual(ram.events, [])
            self.assertEqual([name for name in dir(actor) if not name.startswith('_')],
                ['close', 'provision'])
            actor.close()
            self.assertEqual(ram.events, [])
            self.assertNotIn('__del__', type(actor).__dict__)
            self.assertNotIn('__exit__', type(actor).__dict__)

    def test_actual_unsupported_windows_refuses_before_any_pipe_or_fallback(self):
        if os.name != 'nt':
            self.skipTest('specific actual Windows capability check')
        actor = channels.OriginalAssociationConfigChannels(b'p')
        with patch.object(channels.os, 'pipe', side_effect=AssertionError('no fallback')), \
                patch.object(channels.os, 'pipe2', create=True,
                    side_effect=AssertionError('no actual pipe')):
            self.unavailable(actor.provision)
        self.assertEqual(actor._fds, [])

    def test_missing_native_capabilities_refuse_before_first_pipe(self):
        for missing in ('pipe2', 'fpathconf', 'get_blocking', 'set_blocking', 'write', 'close'):
            ram = RamPipes()
            setattr(ram, missing, None)
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'p')
                self.unavailable(actor.provision)
                self.assertEqual(ram.events, [])

    def test_same_input_once_direction_flags_five_owned_handles_and_explicit_close(self):
        ram = RamPipes()
        with controlled(ram):
            raw = bytes(bytearray(b'private'))
            actor = channels.OriginalAssociationConfigChannels(raw)
            actor.provision()
            self.assertEqual(actor._state, 'READY')
            self.assertIs(actor._input, raw)
            self.assertEqual(len(ram.writes), 1)
            self.assertIs(ram.writes[0][1], raw)
            self.assertEqual(actor._pipes, ((10,), (12, 13), (14, 15)))
            self.assertEqual(set(actor._fds), {10, 12, 13, 14, 15})
            self.assertEqual([ram.live[fd]['blocking'] for fd in (10, 12, 13, 14, 15)],
                [True, False, True, False, True])
            self.assertTrue(all(not value['inheritable'] for value in ram.live.values()))
            self.assertEqual([e for e in ram.events if e[0] == 'pipe2'],
                [('pipe2', ram.O_CLOEXEC)]*3)
            actor.close()
            events = list(ram.events)
            actor.close()
            self.assertEqual(ram.events, events)
            self.closed_once(ram)

    def test_exact_boundaries_and_insufficient_pipe_buf_prevent_write(self):
        for size in (1, 2048):
            ram = RamPipes()
            ram.pipe_buf = size
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'p'*size)
                actor.provision()
                self.assertEqual(len(ram.writes), 1)
                actor.close()
                self.closed_once(ram)
        for limit in (True, 0, -1, 3, None):
            ram = RamPipes()
            ram.pipe_buf = limit
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'p'*4)
                self.unavailable(actor.provision)
                self.assertEqual(ram.writes, [])
                self.closed_once(ram)

    def test_descriptor_registered_before_post_open_stat_failure(self):
        ram = RamPipes()
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            def fail(fd):
                self.assertEqual(len(actor._fds), 6)
                raise OSError('private stat')
            ram.hooks['fstat'] = fail
            self.unavailable(actor.provision)
            self.assertEqual(ram.writes, [])
            self.closed_once(ram)

    def test_wrong_type_uid_mode_link_direction_and_alias_refuse_before_write(self):
        mutations = [dict(mode=stat.S_IFREG | 0o600), dict(mode=stat.S_IFIFO | 0o644),
            dict(uid=1001), dict(nlink=2), dict(access=True), dict(access=1),
            dict(inheritable=True), dict(blocking=False), dict(ino=0)]
        for change in mutations:
            ram = RamPipes()
            ram.hooks['fstat'] = lambda fd: ram.live[10].update(change)
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'p')
                self.unavailable(actor.provision)
                self.assertEqual(ram.writes, [])
                self.closed_once(ram)
        ram = RamPipes()
        ram.hooks['fstat'] = lambda fd: [ram.live[k].update(ino=10) for k in ram.live]
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            self.unavailable(actor.provision)
            self.assertEqual(ram.writes, [])
            self.closed_once(ram)

    def test_parent_blocking_mode_checked_before_write(self):
        ram = RamPipes()
        ram.set_blocking = lambda fd, value: None
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            self.unavailable(actor.provision)
            self.assertEqual(ram.writes, [])
            self.closed_once(ram)

    def test_partial_bool_unknown_and_raising_write_no_second_attempt(self):
        for value in (0, 1, 5, True, 'private', object()):
            ram = RamPipes()
            ram.write_result = value
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'four')
                self.unavailable(actor.provision)
                self.assertEqual(len(ram.writes), 1)
                before = len(ram.events)
                self.unavailable(actor.provision)
                self.assertEqual(len(ram.events), before)
                self.closed_once(ram)
        ram = RamPipes()
        ram.hooks['write'] = lambda fd: (_ for _ in ()).throw(BlockingIOError('private'))
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            self.unavailable(actor.provision)
            self.assertEqual(len([e for e in ram.events if e[0] == 'write']), 1)
            self.closed_once(ram)

    def test_reentry_during_open_or_write_burns_permission_and_closes_new_handles(self):
        for phase in ('pipe2', 'write'):
            ram = RamPipes()
            with controlled(ram):
                actor = channels.OriginalAssociationConfigChannels(b'p')
                def reenter(*args):
                    try:
                        actor.close()
                    except RuntimeError:
                        pass
                ram.hooks[phase] = reenter
                self.unavailable(actor.provision)
                self.assertEqual(actor._state, 'FAILED')
                self.closed_once(ram)
                self.assertLessEqual(len(ram.writes), 1)

    def test_close_error_or_unknown_result_keeps_uncertainty_without_fd_retry(self):
        for explicit in (False, True):
            for unknown in (False, True):
                ram = RamPipes()
                with controlled(ram):
                    actor = channels.OriginalAssociationConfigChannels(b'p')
                    if explicit:
                        actor.provision()
                    if unknown:
                        ram.close_return = True
                    else:
                        ram.close_error = 10 if explicit else 11
                    self.unavailable(actor.close if explicit else actor.provision)
                    self.assertTrue(actor._uncertain_close)
                    events = list(ram.events)
                    self.unavailable(actor.close)
                    self.assertEqual(ram.events, events)
                    self.closed_once(ram)

    def test_post_write_identity_change_and_duplicate_provision_close_without_rewrite(self):
        ram = RamPipes()
        ram.hooks['write'] = lambda fd: ram.live[10].update(ino=999)
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            self.unavailable(actor.provision)
            self.assertEqual(len(ram.writes), 1)
            self.closed_once(ram)
        ram = RamPipes()
        with controlled(ram):
            actor = channels.OriginalAssociationConfigChannels(b'p')
            actor.provision()
            self.unavailable(actor.provision)
            self.assertEqual(len(ram.writes), 1)
            self.closed_once(ram)

    def test_no_read_file_crypto_sql_process_clock_or_automatic_cleanup(self):
        ram = RamPipes()
        with controlled(ram), ExitStack() as stack:
            def forbidden(*args, **kwargs):
                raise AssertionError('forbidden side effect')
            for owner, name in ((builtins, 'open'), (Path, 'read_bytes'), (Path, 'write_bytes'),
                    (hashlib, 'sha256'), (sqlite3, 'connect'), (subprocess, 'Popen'),
                    (subprocess, 'run'), (os, 'read'), (os, 'waitpid'), (os, 'kill'),
                    (time, 'monotonic'), (time, 'monotonic_ns'), (time, 'sleep')):
                stack.enter_context(patch.object(owner, name, forbidden))
            actor = channels.OriginalAssociationConfigChannels(b'p')
            actor.provision()
            actor.close()
            self.closed_once(ram)


if __name__ == '__main__':
    unittest.main()
