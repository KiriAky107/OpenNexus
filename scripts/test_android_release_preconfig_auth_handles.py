"""Byte-free auth handle RAM models; no real HOME/key/SDK/FS/socket/child."""
import copy
import gc
import os
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import android_release_preconfig_sdk as sdk
import android_release_preconfig_host_resources as host
from test_android_release_preconfig_listener import NativeModel
from test_android_release_preconfig_sdk import PrivateError, PrivateValue


class AuthModel(NativeModel):
    def __init__(self):
        super().__init__()
        self.environ['HOME'] = '/owned/home'
        self.forced_open_fd = None
        for index, (path, mode, size) in enumerate((
                ('/owned/home', stat.S_IFDIR | 0o755, 4096),
                ('/owned/home/.android', stat.S_IFDIR | 0o700, 4096),
                ('/owned/home/.android/adbkey', stat.S_IFREG | 0o600, 1704))):
            self.nodes[path] = os.stat_result((mode, 200 + index, 7, 1, 1001, 1001,
                size, 1, 2, 3), {'st_mtime_ns': 2000, 'st_ctime_ns': 3000})

    def stat(self, name, *, dir_fd=None, follow_symlinks=True):
        value = self.event('stat', name, dir_fd, follow_symlinks)
        return self.nodes[self.path(name, dir_fd)] if value is None else value

    def open(self, name, flags, *, dir_fd=None):
        fd = super().open(name, flags, dir_fd=dir_fd)
        return fd if self.forced_open_fd is None else self.forced_open_fd


class AuthHandles(unittest.TestCase):
    def setUp(self):
        self.model = AuthModel()
        self.patches = [patch.object(sdk, 'os', self.model),
            patch.object(sdk, 'sys', SimpleNamespace(platform='linux')),
            patch.object(host, 'os', self.model),
            patch.object(host, 'sys', SimpleNamespace(platform='linux', byteorder='little')),
            patch.object(host, '_native_listener_api', self.model.api),
            patch.object(host, 'time', SimpleNamespace(monotonic_ns=self.no_clock))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        # Only RAM model lifetimes, never actual resource cleanup or Stop.
        host._OWNERS.clear()
        sdk._OWNERS.clear()
        for p in reversed(self.patches):
            p.stop()

    def no_clock(self):
        raise AssertionError('auth pin must not sample/reset a clock')

    def fails(self, action):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_PRECONFIG_(?:SDK|HOST_RESOURCES)_UNAVAILABLE$'):
            action()

    def consumed(self):
        source = sdk.OriginalPreconfigSdk()
        source.pin()  # Reused RAM fixture setup, not any old test method.
        owner = host.OriginalPreconfigHostResources(source)
        owner.claim()
        self.model.events.clear()
        return source, owner

    def replace_key(self, **changes):
        path = '/owned/home/.android/adbkey'
        old = self.model.nodes[path]
        fields = ['st_mode', 'st_ino', 'st_dev', 'st_nlink', 'st_uid', 'st_gid',
                  'st_size', 'st_atime', 'st_mtime', 'st_ctime']
        self.model.nodes[path] = os.stat_result(tuple(changes.get(k, getattr(old, k)) for k in fields),
            {'st_mtime_ns': 2000, 'st_ctime_ns': 3000})

    def test_constructor_and_unclaimed_auth_pin_are_zero_io_clock(self):
        owner = host.OriginalPreconfigHostResources(sdk.OriginalPreconfigSdk())
        self.fails(owner.pin_existing_auth)
        self.assertEqual(self.model.events, [])

    def test_same_source_existing_handles_no_key_bytes_and_known_close_order(self):
        source, owner = self.consumed()
        sdk_fds = tuple(owner._fds)
        self.assertIsNone(owner.pin_existing_auth())
        auth_fds = tuple(owner._auth_fds)
        self.assertEqual(len(auth_fds), 5)
        self.assertEqual(owner._auth_home, '/owned/home')
        self.assertEqual([e[1] for e in self.model.events if e[0] == 'open'],
                         ['/', 'owned', 'home', '.android', 'adbkey'])
        self.assertTrue(all(e[0] in ('env', 'pid', 'uid', 'stat', 'open', 'fstat', 'inheritable')
                            for e in self.model.events))
        self.assertEqual(self.model.native_events, [])
        self.fails(source.close)
        self.model.events.clear()
        owner.close()
        owner.close()
        self.assertEqual(self.model.events, [('close', fd) for fd in reversed(auth_fds)]
                         + [('close', fd) for fd in reversed(sdk_fds)])
        self.assertEqual(self.model.fds, {})

    def test_same_listener_auth_sdk_close_order_without_extra_socket_or_clock(self):
        _, owner = self.consumed()
        sdk_fds = tuple(owner._fds)
        owner.bind_loopback_listener()
        owner.pin_existing_auth()
        auth_fds = tuple(owner._auth_fds)
        native = list(self.model.native_events)
        self.model.events.clear()
        owner.close()
        self.assertEqual(self.model.events, [('close', 201)]
            + [('close', fd) for fd in reversed(auth_fds)]
            + [('close', fd) for fd in reversed(sdk_fds)])
        self.assertEqual(self.model.native_events, native)

    def test_actual_windows_gate_precedes_environment_and_auth_fs(self):
        _, owner = self.consumed()
        self.assertEqual(sys.platform, 'win32')
        with patch.object(host, 'sys', sys):
            self.fails(owner.pin_existing_auth)
        self.assertEqual(self.model.events, [])

    def test_private_home_rejects_without_comparison_or_fs(self):
        _, owner = self.consumed()
        self.model.environ['HOME'] = PrivateValue()
        self.fails(owner.pin_existing_auth)
        self.assertFalse(any(e[0] == 'stat' for e in self.model.events))

    def test_missing_home_has_no_passwd_or_empty_home_fallback(self):
        _, owner = self.consumed()
        del self.model.environ['HOME']
        self.fails(owner.pin_existing_auth)
        self.assertFalse(any(e[0] == 'stat' for e in self.model.events))

    def test_noncanonical_home_is_rejected_before_any_fs(self):
        _, owner = self.consumed()
        self.model.environ['HOME'] = '/owned/../home'
        self.fails(owner.pin_existing_auth)
        self.assertFalse(any(e[0] == 'stat' for e in self.model.events))

    def test_absent_android_directory_does_not_create_or_cleanup(self):
        _, owner = self.consumed()
        del self.model.nodes['/owned/home/.android']
        self.fails(owner.pin_existing_auth)
        self.assertFalse(any(e[0] == 'close' for e in self.model.events))
        self.assertEqual(len(owner._auth_fds), 3)
        self.assertIs(host._OWNERS[id(owner)], owner)

    def test_absent_key_does_not_read_generate_or_close_partial_handles(self):
        _, owner = self.consumed()
        del self.model.nodes['/owned/home/.android/adbkey']
        self.fails(owner.pin_existing_auth)
        self.assertEqual(len(owner._auth_fds), 4)
        self.assertFalse(any(e[0] in ('pread', 'close') for e in self.model.events))

    def test_symlink_key_metadata_fails_before_open(self):
        _, owner = self.consumed()
        self.replace_key(st_mode=stat.S_IFLNK | 0o600)
        self.fails(owner.pin_existing_auth)
        self.assertNotIn('adbkey', [e[1] for e in self.model.events if e[0] == 'open'])

    def test_hardlinked_key_fails_before_open(self):
        _, owner = self.consumed()
        self.replace_key(st_nlink=2)
        self.fails(owner.pin_existing_auth)
        self.assertNotIn('adbkey', [e[1] for e in self.model.events if e[0] == 'open'])

    def test_wrong_key_permissions_fail_without_chmod_or_read(self):
        _, owner = self.consumed()
        self.replace_key(st_mode=stat.S_IFREG | 0o644)
        self.fails(owner.pin_existing_auth)
        self.assertFalse(any(e[0] in ('pread', 'close') for e in self.model.events))

    def test_private_open_exception_retains_unknown_and_original_owners(self):
        source, owner = self.consumed()
        self.model.hooks['open'] = lambda *_: (_ for _ in ()).throw(PrivateError())
        self.fails(owner.pin_existing_auth)
        self.assertTrue(owner._auth_open_unknown)
        ref = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNotNone(ref())
        self.assertIs(sdk._OWNERS[id(source)], source)
        self.assertFalse(any(e[0] == 'close' for e in self.model.events))

    def test_open_reentry_retains_actual_return_before_postgate(self):
        _, owner = self.consumed()
        self.model.hooks['open'] = lambda *_: self.fails(owner.close)
        self.fails(owner.pin_existing_auth)
        self.assertEqual(len(owner._auth_fds), 1)
        self.assertFalse(any(e[0] in ('fstat', 'close') for e in self.model.events))

    def test_returned_sdk_alias_stops_before_fstat_or_close(self):
        _, owner = self.consumed()
        self.model.forced_open_fd = owner._fds[0]
        self.fails(owner.pin_existing_auth)
        self.assertTrue(owner._auth_open_unknown)
        self.assertEqual(owner._auth_fds, [])
        self.assertFalse(any(e[0] in ('fstat', 'close') for e in self.model.events))

    def test_home_changes_before_final_return_rejects_without_read_or_cleanup(self):
        _, owner = self.consumed()
        count = []
        def change(key):
            if key == 'HOME':
                count.append(key)
                if len(count) == 2:
                    self.model.environ['HOME'] = '/owned/other'
        self.model.hooks['env'] = change
        self.fails(owner.pin_existing_auth)
        self.assertEqual(len(owner._auth_fds), 5)
        self.assertFalse(any(e[0] in ('pread', 'close') for e in self.model.events))

    def test_second_pin_revokes_without_fs_retry_or_close(self):
        _, owner = self.consumed()
        owner.pin_existing_auth()
        before = list(self.model.events)
        self.fails(owner.pin_existing_auth)
        self.fails(owner.close)
        self.assertEqual(self.model.events, before)

    def test_foreign_copy_has_no_auth_pin_or_close_authority(self):
        _, owner = self.consumed()
        owner.pin_existing_auth()
        before = list(self.model.events)
        foreign = copy.copy(owner)
        self.fails(foreign.pin_existing_auth)
        self.fails(foreign.close)
        self.assertEqual(self.model.events, before)

    def test_uncertain_auth_close_stops_before_remaining_auth_and_sdk(self):
        source, owner = self.consumed()
        owner.pin_existing_auth()
        sdk_fds, auth_fds = tuple(owner._fds), tuple(owner._auth_fds)
        self.model.events.clear()
        self.model.hooks['close'] = lambda *_: (_ for _ in ()).throw(PrivateError())
        self.fails(owner.close)
        self.assertEqual(self.model.events, [('close', auth_fds[-1])])
        self.assertEqual(tuple(owner._fds), sdk_fds)
        self.assertEqual(owner._uncertain, [auth_fds[-1]])
        self.assertIs(sdk._OWNERS[id(source)], source)
        self.assertIs(host._OWNERS[id(owner)], owner)

    def test_late_snapshot_change_stops_before_second_close(self):
        _, owner = self.consumed()
        owner.pin_existing_auth()
        last = owner._auth_fds[-1]
        self.model.events.clear()
        def change(fd):
            owner._auth_snapshot = tuple(list(owner._auth_snapshot))
        self.model.hooks['close'] = change
        self.fails(owner.close)
        self.assertEqual(self.model.events, [('close', last)])


if __name__ == '__main__':
    unittest.main()
