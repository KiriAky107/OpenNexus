"""Native-call RAM models only; no real socket, SDK, child or fixture root."""
import copy
import ctypes
import gc
from collections import namedtuple
import os
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import android_release_preconfig_sdk as sdk
import android_release_preconfig_host_resources as host
from test_android_release_preconfig_sdk import Model, PrivateError, PrivateValue


class NativeModel(Model):
    uname_result = namedtuple('Uname', 'sysname nodename release version machine')

    def __init__(self):
        super().__init__()
        self.close = self.close  # SAME concrete captured close function object.
        self.socket_fd, self.port = 201, 43123
        self.native_hooks, self.native_events = {}, []

    def uname(self):
        self.event('uname')
        return self.uname_result('Linux', 'owned', '1', '1', 'x86_64')

    def fstat(self, fd):
        if self.fds.get(fd) == 'listener':
            self.event('fstat', fd)
            return self.stat_result((stat.S_IFSOCK | 0o777, 91, 7, 1, 1001, 1001, 0, 1, 2, 3))
        return super().fstat(fd)

    def get_blocking(self, fd):
        value = self.event('blocking', fd)
        return False if value is None else value

    def native_event(self, name, *args):
        self.native_events.append((name, *args))
        hook = self.native_hooks.get(name)
        return None if hook is None else hook(*args)

    def create(self, family, flags, protocol):
        # Model the kernel's returned descriptor before any source post-check.
        fd = self.socket_fd
        if type(fd) is int and fd >= 0 and fd not in self.fds:
            self.fds[fd] = 'listener'
        value = self.native_event('socket', family, flags, protocol)
        return fd if value is None else value

    def bind(self, fd, address, length):
        self.bound = bytes(address._obj)
        value = self.native_event('bind', fd, self.bound, length)
        return 0 if value is None else value

    def listen(self, fd, backlog):
        value = self.native_event('listen', fd, backlog)
        return 0 if value is None else value

    def socket_name(self, fd, address, length):
        result = address._obj
        result.family = 2
        result.port = int.from_bytes(self.port.to_bytes(2, 'big'), sys.byteorder)
        result.address[:] = (127, 0, 0, 1)
        length._obj.value = 16
        value = self.native_event('name', fd, result, length._obj)
        return 0 if value is None else value

    def option(self, fd, level, key, result, length):
        result._obj.value = {3: 1, 30: 1, 2: 0, 15: 0}[key]
        length._obj.value = 4
        value = self.native_event('option', fd, level, key, result._obj, length._obj)
        return 0 if value is None else value

    def api(self):
        self.native_event('library')
        return object(), (self.create, self.bind, self.listen, self.socket_name, self.option)


class PreconfigListener(unittest.TestCase):
    def setUp(self):
        self.model = NativeModel()
        self.clock_calls = 0
        self.patches = [patch.object(sdk, 'os', self.model),
            patch.object(sdk, 'sys', SimpleNamespace(platform='linux')),
            patch.object(host, 'os', self.model),
            patch.object(host, 'sys', SimpleNamespace(platform='linux', byteorder='little')),
            patch.object(host, '_native_listener_api', self.model.api),
            patch.object(host, 'time', SimpleNamespace(monotonic_ns=self.clock))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        # Only modeled lifetimes are cleared. This is not native cleanup/Stop.
        host._OWNERS.clear()
        sdk._OWNERS.clear()
        for p in reversed(self.patches):
            p.stop()

    def clock(self):
        self.clock_calls += 1
        raise AssertionError('listener must not sample or reset a clock')

    def fails(self, call):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_PRECONFIG_(?:SDK|HOST_RESOURCES)_UNAVAILABLE$'):
            call()

    def consumed(self):
        source = sdk.OriginalPreconfigSdk()
        source.pin()  # Original RAM fixture setup, not an old test body.
        resource = host.OriginalPreconfigHostResources(source)
        resource.claim()
        self.model.events.clear()
        return source, resource

    def test_bound_listener_is_actual_source_fd_port_and_explicit_prechild_close(self):
        source, resource = self.consumed()
        sdk_fds = tuple(resource._fds)
        self.assertIsNone(resource.bind_loopback_listener())
        self.assertEqual(resource._listener_fds, [201])
        self.assertEqual(resource._listener_port, 43123)
        self.assertEqual(self.model.bound, b'\x02\0\0\0\x7f\0\0\x01' + b'\0' * 8)
        self.assertEqual(self.model.native_events[:3],
            [('library',), ('socket', 2, 1 | 0x80000 | 0x800, 0),
             ('bind', 201, self.model.bound, 16)])
        self.assertEqual([e[2] for e in self.model.native_events if e[0] == 'listen'], [8])
        self.assertEqual([e[3] for e in self.model.native_events if e[0] == 'option'], [3, 30, 2, 15])
        self.assertEqual(self.clock_calls, 0)
        self.assertTrue(all(e[0] in ('uname', 'env', 'pid', 'uid', 'fstat', 'inheritable', 'blocking')
                            for e in self.model.events))
        self.fails(source.close)
        self.model.events.clear()
        resource.close()
        resource.close()
        self.assertEqual(self.model.events, [('close', 201)] + [('close', fd) for fd in reversed(sdk_fds)])
        self.assertEqual(self.model.fds, {})
        self.assertEqual(host._OWNERS, {})
        self.assertEqual(sdk._OWNERS, {})

    def test_actual_windows_gate_precedes_environment_library_or_socket(self):
        _, resource = self.consumed()
        self.assertEqual(sys.platform, 'win32')
        with patch.object(host, 'sys', sys):
            self.fails(resource.bind_loopback_listener)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.model.native_events, [])
        self.assertEqual(self.clock_calls, 0)

    def test_constructor_claim_and_unclaimed_bind_have_no_listener_io(self):
        source = sdk.OriginalPreconfigSdk()
        resource = host.OriginalPreconfigHostResources(source)
        self.fails(resource.bind_loopback_listener)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.model.native_events, [])

    def test_foreign_copy_cannot_bind_or_close_original_listener(self):
        _, resource = self.consumed()
        resource.bind_loopback_listener()
        before = list(self.model.events), list(self.model.native_events)
        foreign = copy.copy(resource)
        self.fails(foreign.bind_loopback_listener)
        self.fails(foreign.close)
        self.assertEqual((self.model.events, self.model.native_events), before)
        self.assertIn(201, self.model.fds)

    def test_second_bind_revokes_without_another_library_socket_or_close(self):
        _, resource = self.consumed()
        resource.bind_loopback_listener()
        before = list(self.model.events), list(self.model.native_events)
        self.fails(resource.bind_loopback_listener)
        self.fails(resource.close)
        self.assertEqual((self.model.events, self.model.native_events), before)
        self.assertIs(host._OWNERS[id(resource)], resource)

    def test_changed_source_environment_stops_before_socket(self):
        _, resource = self.consumed()
        self.model.environ['GITHUB_SHA'] = PrivateValue()
        self.fails(resource.bind_loopback_listener)
        self.assertEqual(self.model.native_events, [])
        self.assertNotIn(('env', 'ANDROID_HOME'), self.model.events)

    def test_environment_reentry_stops_before_next_field_or_native_library(self):
        _, resource = self.consumed()
        self.model.hooks['env'] = lambda key: self.fails(resource.bind_loopback_listener)
        self.fails(resource.bind_loopback_listener)
        self.assertEqual([e for e in self.model.events if e[0] == 'env'], [('env', 'GITHUB_ACTIONS')])
        self.assertEqual(self.model.native_events, [])

    def test_wrong_native_architecture_rejects_before_environment_or_library(self):
        _, resource = self.consumed()
        self.model.uname = lambda: self.model.uname_result('Linux', 'owned', '1', '1', 'aarch64')
        self.fails(resource.bind_loopback_listener)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.model.native_events, [])

    def test_private_native_loader_error_retains_source_without_socket_or_cleanup(self):
        _, resource = self.consumed()
        with patch.object(host, '_native_listener_api', side_effect=PrivateError()):
            self.fails(resource.bind_loopback_listener)
        self.assertEqual(self.model.native_events, [])
        self.assertFalse(any(e[0] == 'close' for e in self.model.events))
        self.assertIs(host._OWNERS[id(resource)], resource)

    def test_native_creation_reentry_retains_returned_fd_before_rejecting(self):
        _, resource = self.consumed()
        def reentry(*_):
            self.fails(resource.close)
        self.model.native_hooks['socket'] = reentry
        self.fails(resource.bind_loopback_listener)
        self.assertEqual(resource._listener_fds, [201])
        self.assertEqual([e[0] for e in self.model.native_events], ['library', 'socket'])
        self.assertFalse(any(e[0] in ('fstat', 'close') for e in self.model.events))

    def test_unknown_creation_keeps_owner_without_read_close_retry_or_gc_cleanup(self):
        _, resource = self.consumed()
        self.model.native_hooks['socket'] = lambda *_: (_ for _ in ()).throw(PrivateError())
        self.fails(resource.bind_loopback_listener)
        self.assertTrue(resource._listener_open_unknown)
        before = list(self.model.events), list(self.model.native_events)
        reference = weakref.ref(resource)
        del resource
        gc.collect()
        self.assertIsNotNone(reference())
        self.assertEqual((self.model.events, self.model.native_events), before)

    def test_returned_sdk_fd_alias_is_not_given_listener_close_authority(self):
        _, resource = self.consumed()
        self.model.socket_fd = resource._fds[0]
        self.fails(resource.bind_loopback_listener)
        self.assertEqual(resource._listener_fds, [])
        self.assertTrue(resource._listener_open_unknown)
        self.assertFalse(any(e[0] == 'close' for e in self.model.events))

    def test_bind_private_return_stops_before_listen_or_readback(self):
        _, resource = self.consumed()
        self.model.native_hooks['bind'] = lambda *_: PrivateValue()
        self.fails(resource.bind_loopback_listener)
        self.assertEqual([e[0] for e in self.model.native_events], ['library', 'socket', 'bind'])
        self.assertIn(201, self.model.fds)

    def test_shared_adb_port_is_rejected_before_option_queries(self):
        _, resource = self.consumed()
        self.model.port = 5037
        self.fails(resource.bind_loopback_listener)
        self.assertFalse(any(e[0] == 'option' for e in self.model.native_events))

    def test_unknown_listen_and_bad_address_do_not_query_later_options(self):
        _, resource = self.consumed()
        self.model.native_hooks['listen'] = lambda *_: True
        self.fails(resource.bind_loopback_listener)
        self.assertFalse(any(e[0] == 'name' for e in self.model.native_events))

    def test_non_loopback_kernel_address_fails_closed(self):
        _, resource = self.consumed()
        self.model.native_hooks['name'] = lambda fd, value, length: value.address.__setitem__(0, 0)
        self.fails(resource.bind_loopback_listener)
        self.assertFalse(any(e[0] == 'option' for e in self.model.native_events))

    def test_socket_option_shape_rejects_before_next_option(self):
        _, resource = self.consumed()
        def wrong(fd, level, key, value, length):
            if key == 3:
                length.value = 8
        self.model.native_hooks['option'] = wrong
        self.fails(resource.bind_loopback_listener)
        self.assertEqual([e[3] for e in self.model.native_events if e[0] == 'option'], [3])

    def test_listener_close_uncertainty_preserves_sdk_fds_and_strong_slots(self):
        source, resource = self.consumed()
        resource.bind_loopback_listener()
        sdk_fds = tuple(resource._fds)
        self.model.events.clear()
        self.model.hooks['close'] = lambda fd: (_ for _ in ()).throw(PrivateError())
        self.fails(resource.close)
        self.assertEqual(self.model.events, [('close', 201)])
        self.assertEqual(tuple(resource._fds), sdk_fds)
        self.assertEqual(resource._uncertain, [201])
        self.assertIs(sdk._OWNERS[id(source)], source)
        self.assertIs(host._OWNERS[id(resource)], resource)
        before = list(self.model.events)
        self.fails(resource.close)
        self.assertEqual(self.model.events, before)

    def test_late_listener_binding_mutation_prevents_all_sdk_closes(self):
        _, resource = self.consumed()
        resource.bind_loopback_listener()
        self.model.events.clear()
        def mutate(fd):
            resource._listener_native = None
        self.model.hooks['close'] = mutate
        self.fails(resource.close)
        self.assertEqual(self.model.events, [('close', 201)])
        self.assertTrue(resource._fds)

    def test_corrupted_listener_fd_group_does_not_close_foreign_descriptor(self):
        _, resource = self.consumed()
        resource.bind_loopback_listener()
        resource._listener_fds.append(999)
        before = list(self.model.events)
        self.fails(resource.close)
        self.assertEqual(self.model.events, before)
        self.assertIn(201, self.model.fds)


if __name__ == '__main__':
    unittest.main()
