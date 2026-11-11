"""RAM syscall models only; no SDK fixture, child or actual Linux filesystem."""
import copy
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_preconfig_sdk as sdk


class PrivateError(BaseException):
    def __str__(self):
        raise AssertionError('private error stringified')


class PrivateValue:
    def __eq__(self, other):
        raise AssertionError('private value compared')

    def __str__(self):
        raise AssertionError('private value stringified')


class Environment(dict):
    def __init__(self, model):
        super().__init__(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted',
            RUNNER_OS='Linux', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
            GITHUB_SHA='e' * 40, ANDROID_HOME='/owned/sdk', ANDROID_SDK_ROOT='/owned/sdk')
        self.model = model

    def get(self, key):
        self.model.event('env', key)
        return super().get(key)


class Model:
    name = 'posix'
    O_RDONLY, O_NOFOLLOW, O_DIRECTORY, O_CLOEXEC, O_NONBLOCK = 0, 0x100, 0x200, 0x400, 0x800
    stat_result = os.stat_result

    def __init__(self):
        self.events, self.hooks, self.fds = [], {}, {}
        self.environ = Environment(self)
        self.supports_dir_fd = {self.open, self.stat}
        self.supports_follow_symlinks = {self.stat}
        self.nodes = {}
        for i, path in enumerate(('/', '/owned', '/owned/sdk', '/owned/sdk/platform-tools',
                '/owned/sdk/platform-tools/adb', '/owned/sdk/platforms',
                '/owned/sdk/platforms/android-36', '/owned/sdk/platforms/android-36/android.jar')):
            mode = 0o100500 if path.endswith('/adb') else 0o100400 if path.endswith('.jar') else 0o40755
            size = 20 if path.endswith('/adb') else 4 if path.endswith('.jar') else 4096
            self.nodes[path] = os.stat_result((mode, i + 1, 7, 1, 1001, 1001, size, 1, 2, 3),
                {'st_mtime_ns': 2000, 'st_ctime_ns': 3000})

    def event(self, name, *args):
        self.events.append((name, *args))
        if name in self.hooks:
            return self.hooks[name](*args)

    def path(self, name, parent=None):
        if name.startswith('/proc/321/fd/'):
            return self.fds[int(name.rsplit('/', 1)[1])]
        return name if parent is None else self.fds[parent].rstrip('/') + '/' + name

    def stat(self, name, *, dir_fd=None, follow_symlinks=True):
        self.event('stat', name, dir_fd, follow_symlinks)
        return self.nodes[self.path(name, dir_fd)]

    def open(self, name, flags, *, dir_fd=None):
        if not sdk._OWNERS:
            raise AssertionError('native open before strong owner registration')
        self.event('open', name, flags, dir_fd)
        fd = 10 + len(self.fds)
        self.fds[fd] = self.path(name, dir_fd)
        return fd

    def fstat(self, fd):
        self.event('fstat', fd)
        return self.nodes[self.fds[fd]]

    def get_inheritable(self, fd):
        value = self.event('inheritable', fd)
        return False if value is None else value

    def pread(self, fd, bound, offset):
        value = self.event('pread', fd, bound, offset)
        if value is not None:
            return value
        return b'\x7fELF\x02\x01\x01' + b'\0' * 11 + b'\x3e\x00' if bound == 20 else b'PK\x03\x04'

    def geteuid(self):
        self.event('uid')
        return 1001

    def getpid(self):
        value = self.event('pid')
        return 321 if value is None else value

    def readlink(self, name):
        value = self.event('readlink', name)
        return self.path(name) if value is None else value

    def close(self, fd):
        value = self.event('close', fd)
        if value is None:
            del self.fds[fd]
        return value


class PreconfigSdkOwner(unittest.TestCase):
    def setUp(self):
        self.model = Model()
        self.os_patch = patch.object(sdk, 'os', self.model)
        self.sys_patch = patch.object(sdk, 'sys', SimpleNamespace(platform='linux'))
        self.os_patch.start()
        self.sys_patch.start()

    def tearDown(self):
        # RAM model lifetime only. This is not cleanup/Stop of native resources.
        sdk._OWNERS.clear()
        self.os_patch.stop()
        self.sys_patch.stop()

    def fails(self, call):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_PRECONFIG_SDK_UNAVAILABLE$'):
            call()

    def test_constructor_and_new_close_do_not_touch_environment_or_io(self):
        owner = sdk.OriginalPreconfigSdk()
        owner.close()
        owner.close()
        self.assertEqual(self.model.events, [])
        self.assertEqual(sdk._OWNERS, {})

    def test_actual_windows_gate_rejects_before_environment_or_files(self):
        self.assertEqual(sys.platform, 'win32')
        with patch.object(sdk, 'sys', sys), patch.object(sdk, 'os', SimpleNamespace(name=os.name)):
            self.fails(sdk.OriginalPreconfigSdk().pin)
        self.assertEqual(self.model.events, [])
        self.assertEqual(sdk._OWNERS, {})

    def test_native_capability_failure_precedes_environment_and_registry(self):
        self.model.supports_dir_fd = set()
        self.fails(sdk.OriginalPreconfigSdk().pin)
        self.assertEqual(self.model.events, [])
        self.assertEqual(sdk._OWNERS, {})

    def test_pin_verify_and_close_own_original_fds_and_two_parent_aliases(self):
        owner = sdk.OriginalPreconfigSdk()
        self.assertIsNone(owner.pin())
        self.assertIs(sdk._OWNERS[id(owner)], owner)
        self.assertEqual(owner._state, 'READY')
        self.assertEqual(len(owner._fds), 8)
        self.assertEqual(len(owner._aliases), 2)
        self.assertEqual([x[2:] for x in self.model.events if x[0] == 'pread'], [(20, 0), (4, 0)])
        owner.verify()
        owner.close()
        self.assertEqual(self.model.fds, {})
        self.assertEqual(sdk._OWNERS, {})
        closed = [x[1] for x in self.model.events if x[0] == 'close']
        self.assertEqual(len(closed), len(set(closed)))

    def test_context_path_alias_private_values_and_bounds_reject_before_open(self):
        cases = [('GITHUB_ACTIONS', 'false'), ('ANDROID_HOME', '/owned/../sdk'),
            ('ANDROID_HOME', '/owned//sdk'), ('ANDROID_HOME', '/owned/sdk/'),
            ('ANDROID_HOME', '/' + 'a' * 513), ('ANDROID_SDK_ROOT', '/other/sdk'),
            ('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', True),
            ('GITHUB_SHA', PrivateValue()), ('GITHUB_ACTIONS', PrivateValue())]
        for key, value in cases:
            with self.subTest(key=key):
                original = self.model.environ[key]
                self.model.environ[key] = value
                self.fails(sdk.OriginalPreconfigSdk().pin)
                self.model.environ[key] = original
                self.assertFalse(any(x[0] == 'open' for x in self.model.events))
                self.assertEqual(sdk._OWNERS, {})

    def test_environment_reentry_does_not_read_next_field(self):
        owner = sdk.OriginalPreconfigSdk()
        def reenter(key):
            if key == 'GITHUB_ACTIONS':
                self.fails(owner.close)
        self.model.hooks['env'] = reenter
        self.fails(owner.pin)
        self.assertEqual(self.model.events, [('env', 'GITHUB_ACTIONS')])
        self.assertEqual(sdk._OWNERS, {})

    def test_open_reentry_records_returned_fd_before_revocation_cleanup(self):
        owner = sdk.OriginalPreconfigSdk()
        self.model.hooks['open'] = lambda *args: self.fails(owner.close)
        self.fails(owner.pin)
        self.assertEqual([x[1] for x in self.model.events if x[0] == 'close'], [10])
        self.assertEqual(self.model.fds, {})
        self.assertEqual(sdk._OWNERS, {})

    def test_unknown_open_retains_original_owner_and_never_retries_or_closes(self):
        owner = sdk.OriginalPreconfigSdk()
        self.model.hooks['open'] = lambda *args: (_ for _ in ()).throw(PrivateError())
        self.fails(owner.pin)
        self.assertIs(sdk._OWNERS[id(owner)], owner)
        self.assertTrue(owner._open_unknown)
        prior = list(self.model.events)
        self.fails(owner.pin)
        self.fails(owner.close)
        self.assertEqual(self.model.events, prior)

    def test_close_uncertainty_retains_all_remaining_handles_and_owner(self):
        owner = sdk.OriginalPreconfigSdk()
        owner.pin()
        self.model.hooks['close'] = lambda fd: 1
        self.fails(owner.close)
        self.assertIs(sdk._OWNERS[id(owner)], owner)
        self.assertEqual(len(owner._uncertain), 1)
        self.assertEqual(len(owner._fds), 7)
        prior = list(self.model.events)
        self.fails(owner.close)
        self.assertEqual(self.model.events, prior)

    def test_foreign_copy_cannot_close_original_handles(self):
        owner = sdk.OriginalPreconfigSdk()
        owner.pin()
        copied = copy.copy(owner)
        prior = list(self.model.events)
        self.fails(copied.close)
        self.assertEqual(self.model.events, prior)
        self.assertEqual(len(owner._fds), 8)
        owner.close()

    def test_second_owner_cannot_open_or_release_first_owner(self):
        owner = sdk.OriginalPreconfigSdk()
        owner.pin()
        prior = list(self.model.events)
        self.fails(sdk.OriginalPreconfigSdk().pin)
        self.assertEqual(self.model.events, prior)
        self.assertIs(sdk._OWNERS[id(owner)], owner)
        owner.close()

    def test_sdk_header_failure_closes_known_fds_without_reading_other_file(self):
        self.model.hooks['pread'] = lambda *args: b'private-invalid-header'
        self.fails(sdk.OriginalPreconfigSdk().pin)
        self.assertEqual(len([x for x in self.model.events if x[0] == 'pread']), 1)
        self.assertEqual(self.model.fds, {})
        self.assertEqual(sdk._OWNERS, {})

    def test_changed_context_revokes_without_another_descriptor_query(self):
        owner = sdk.OriginalPreconfigSdk()
        owner.pin()
        self.model.events.clear()
        self.model.environ['GITHUB_SHA'] = 'f' * 40
        self.fails(owner.verify)
        self.assertFalse(any(x[0] == 'stat' for x in self.model.events))
        self.assertEqual(self.model.fds, {})

    def test_parent_pid_alias_and_descriptor_inheritability_fail_closed(self):
        for hook, value in [('pid', 322), ('readlink', PrivateValue()), ('inheritable', True)]:
            with self.subTest(hook=hook):
                owner = sdk.OriginalPreconfigSdk()
                owner.pin()
                self.model.hooks[hook] = lambda *args, v=value: v
                self.fails(owner.verify)
                self.model.hooks.clear()
                self.assertEqual(self.model.fds, {})
                self.assertEqual(sdk._OWNERS, {})

    def test_symlink_hardlink_writable_file_and_changed_metadata_reject(self):
        path = '/owned/sdk/platform-tools/adb'
        original = self.model.nodes[path]
        for mode, links in [(0o120777, 1), (0o100500, 2), (0o100522, 1)]:
            with self.subTest(mode=mode):
                values = list(original)
                values[0], values[3] = mode, links
                self.model.nodes[path] = os.stat_result(values, {'st_mtime_ns': 2000, 'st_ctime_ns': 3000})
                self.fails(sdk.OriginalPreconfigSdk().pin)
                self.assertEqual(self.model.fds, {})
        self.model.nodes[path] = original
        owner = sdk.OriginalPreconfigSdk()
        owner.pin()
        values = list(original)
        values[6] += 1
        self.model.nodes[path] = os.stat_result(values, {'st_mtime_ns': 2000, 'st_ctime_ns': 3000})
        self.fails(owner.verify)
        self.assertEqual(self.model.fds, {})

    def test_no_command_handoff_admission_or_public_descriptor_getter(self):
        public = {n for n in vars(sdk.OriginalPreconfigSdk) if not n.startswith('_')}
        self.assertEqual(public, {'pin', 'verify', 'close'})
        self.assertNotIn('__del__', vars(sdk.OriginalPreconfigSdk))
        self.assertNotIn('__enter__', vars(sdk.OriginalPreconfigSdk))
        self.assertFalse(any('process' in n or 'spawn' in n for n in vars(sdk)))


if __name__ == '__main__':
    unittest.main()
