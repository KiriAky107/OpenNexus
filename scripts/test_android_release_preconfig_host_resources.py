"""RAM source/descriptor/clock models; no actual SDK, Linux fd or child."""
import copy
import gc
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import android_release_preconfig_sdk as sdk
import android_release_preconfig_host_resources as host
from test_android_release_preconfig_sdk import Model, PrivateError, PrivateValue


class Clock:
    def __init__(self):
        self.calls = 0
        self.values = [1_000_000]
        self.action = None
        self.function = lambda: self.read()

    def read(self):
        self.calls += 1
        if self.action:
            self.action()
        return self.values.pop(0)


class ReentryLock:
    def __init__(self, action):
        self.action, self.held = action, False

    def acquire(self, blocking):
        if blocking is not False or self.held:
            raise AssertionError('unexpected lock acquisition')
        self.held = True
        self.action()
        return True

    def release(self):
        if not self.held:
            raise AssertionError('unowned lock release')
        self.held = False


class HostResources(unittest.TestCase):
    def setUp(self):
        self.model, self.clock = Model(), Clock()
        self.patches = [patch.object(sdk, 'os', self.model),
            patch.object(sdk, 'sys', SimpleNamespace(platform='linux')),
            patch.object(host, 'os', self.model),
            patch.object(host, 'sys', SimpleNamespace(platform='linux')),
            patch.object(host, 'time', SimpleNamespace(monotonic_ns=self.clock.function))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        # These registries contain only RAM models. Clearing them is not native
        # cleanup/Stop and says nothing about the lifetime of an actual child.
        host._OWNERS.clear()
        sdk._OWNERS.clear()
        for p in reversed(self.patches):
            p.stop()

    def fails(self, call):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_PRECONFIG_(?:SDK|HOST_RESOURCES)_UNAVAILABLE$'):
            call()

    def source(self):
        source = sdk.OriginalPreconfigSdk()
        source.pin()  # Only the original RAM syscall Model, not an old test body.
        self.model.events.clear()
        return source

    def consumed(self):
        source = self.source()
        resource = host.OriginalPreconfigHostResources(source)
        resource.claim()
        return source, resource

    def test_constructor_and_unclaimed_close_are_zero_io_and_clock(self):
        source = sdk.OriginalPreconfigSdk()
        resource = host.OriginalPreconfigHostResources(source)
        resource.close()
        resource.close()
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual(source._state, 'NEW')
        self.assertEqual(host._OWNERS, {})

    def test_actual_windows_gate_precedes_source_io_and_consumption(self):
        self.assertEqual(sys.platform, 'win32')
        source = sdk.OriginalPreconfigSdk()
        with patch.object(host, 'sys', sys):
            self.fails(host.OriginalPreconfigHostResources(source).claim)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual(source._state, 'NEW')

    def test_claim_moves_same_groups_revokes_external_close_and_retires_slots(self):
        source = self.source()
        originals = source._fds, source._edges, source._aliases
        resource = host.OriginalPreconfigHostResources(source)
        self.assertIsNone(resource.claim())
        for current, original in zip((resource._fds, resource._edges, resource._aliases), originals):
            self.assertIs(current, original)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.clock.calls, 0)
        self.assertIs(source._handle_owner, resource)
        self.assertIs(sdk._OWNERS[id(source)], source)
        self.assertIs(host._OWNERS[id(resource)], resource)
        for call in (source.pin, source.verify, source.close):
            self.fails(call)
        self.assertEqual(source._state, 'TRANSFERRED')
        self.assertEqual(self.model.events, [])
        expected = list(reversed(resource._fds))
        resource.close()
        resource.close()
        self.assertEqual(self.model.events, [('close', fd) for fd in expected])
        self.assertEqual(self.model.fds, {})
        self.assertEqual(sdk._OWNERS, {})
        self.assertEqual(host._OWNERS, {})

    def test_second_sdk_and_host_cannot_replace_reserved_original_lifetime(self):
        source, resource = self.consumed()
        self.fails(sdk.OriginalPreconfigSdk().pin)
        self.fails(host.OriginalPreconfigHostResources(source).claim)
        self.assertEqual(self.model.events, [])
        self.assertEqual(len(resource._fds), 8)
        resource.close()

    def test_foreign_copy_or_unready_sdk_does_not_close_original_fds(self):
        source = self.source()
        self.fails(lambda: host.OriginalPreconfigHostResources(PrivateValue()))
        self.fails(host.OriginalPreconfigHostResources(copy.copy(source)).claim)
        self.fails(host.OriginalPreconfigHostResources(sdk.OriginalPreconfigSdk()).claim)
        self.assertEqual(self.model.events, [])
        self.assertEqual(len(source._fds), 8)
        self.assertIs(sdk._OWNERS[id(source)], source)
        source.close()

    def test_copied_host_has_no_clock_or_close_authority(self):
        source, resource = self.consumed()
        copied = copy.copy(resource)
        self.fails(copied.close)
        self.fails(copied.monotonic_ms)
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.clock.calls, 0)
        self.assertIs(source._handle_owner, resource)
        resource.close()

    def test_private_or_invalid_source_shape_rejects_before_any_io(self):
        source = self.source()
        source._fds.append(PrivateValue())
        self.fails(host.OriginalPreconfigHostResources(source).claim)
        source._fds.pop()
        self.assertEqual(self.model.events, [])
        self.assertEqual(source._state, 'READY')
        self.assertEqual(host._OWNERS, {})
        source.close()

    def test_lock_contention_never_takes_or_closes_foreign_sdk(self):
        source = self.source()
        for lock in (host._LOCK, sdk._LOCK):
            self.assertTrue(lock.acquire(False))
            try:
                self.fails(host.OriginalPreconfigHostResources(source).claim)
            finally:
                lock.release()
            self.assertEqual(host._OWNERS, {})
            self.assertIs(source._handle_owner, source)
            self.assertEqual(self.model.events, [])
        source.close()

    def test_source_stamp_rechecked_after_interlocked_late_mutation(self):
        source = self.source()
        resource = host.OriginalPreconfigHostResources(source)
        with patch.object(sdk, '_LOCK', ReentryLock(lambda: source._fds.append(999))):
            self.fails(resource.claim)
        source._fds.pop()
        self.assertIs(source._handle_owner, source)
        self.assertEqual(self.model.events, [])
        self.assertEqual(host._OWNERS, {})
        source.close()

    def test_reentry_before_authority_move_stops_without_sdk_io_or_close(self):
        source = self.source()
        resource = host.OriginalPreconfigHostResources(source)
        with patch.object(sdk, '_LOCK', ReentryLock(lambda: self.fails(resource.close))):
            self.fails(resource.claim)
        self.assertEqual(self.model.events, [])
        self.assertIs(source._handle_owner, source)
        self.assertEqual(host._OWNERS, {})
        source.close()

    def test_only_explicit_clock_samples_original_bound_source_without_reset(self):
        source, resource = self.consumed()
        self.assertEqual(self.clock.calls, 0)
        self.clock.values = [1_000_000, 1_999_999, 2_000_000]
        self.assertEqual([resource.monotonic_ms() for _ in range(3)], [1, 1, 2])
        self.assertEqual(self.clock.calls, 3)
        self.assertEqual(self.model.events, [])
        resource.close()

    def test_private_clock_value_revokes_without_coercion_retry_or_cleanup(self):
        source, resource = self.consumed()
        self.clock.values = [PrivateValue()]
        self.fails(resource.monotonic_ms)
        for call in (resource.monotonic_ms, resource.claim, resource.close, source.close):
            self.fails(call)
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(self.model.events, [])
        self.assertIs(host._OWNERS[id(resource)], resource)
        self.assertIs(sdk._OWNERS[id(source)], source)

    def test_clock_reentry_and_backwards_value_retain_original_owners(self):
        source, resource = self.consumed()
        self.assertEqual(resource.monotonic_ms(), 1)
        self.clock.values = [0]
        self.fails(resource.monotonic_ms)
        self.assertEqual(self.model.events, [])
        self.assertIs(host._OWNERS[id(resource)], resource)
        # Model lifetime reset only, never a native cleanup or Stop.
        host._OWNERS.clear()
        sdk._OWNERS.clear()
        self.model.fds.clear()
        source, resource = self.consumed()
        self.clock.values = [1_000_000]
        self.clock.action = lambda: self.fails(resource.close)
        self.fails(resource.monotonic_ms)
        self.assertEqual(self.model.events, [])
        self.assertIs(host._OWNERS[id(resource)], resource)

    def test_clock_bool_negative_overflow_and_changed_source_fail_before_later_io(self):
        for value in (True, -1, (host._MAX + 1) * 1_000_000):
            with self.subTest(kind=type(value).__name__):
                # Discard only RAM model lifetimes between these independent cases.
                host._OWNERS.clear()
                sdk._OWNERS.clear()
                self.model.fds.clear()
                source, resource = self.consumed()
                self.clock.values = [value]
                self.fails(resource.monotonic_ms)
                self.assertEqual(self.model.events, [])
                self.assertIs(host._OWNERS[id(resource)], resource)
        host._OWNERS.clear()
        sdk._OWNERS.clear()
        self.model.fds.clear()
        source, resource = self.consumed()
        before = self.clock.calls
        host.time.monotonic_ns = lambda: (_ for _ in ()).throw(AssertionError('changed clock called'))
        self.fails(resource.monotonic_ms)
        self.assertEqual(self.clock.calls, before)
        self.assertEqual(self.model.events, [])

    def test_uncertain_native_close_keeps_remaining_fds_and_both_slots(self):
        source, resource = self.consumed()
        self.model.hooks['close'] = lambda fd: (_ for _ in ()).throw(PrivateError())
        self.fails(resource.close)
        self.assertEqual(len(resource._fds), 7)
        self.assertEqual(len(resource._uncertain), 1)
        before = list(self.model.events)
        self.fails(resource.close)
        self.fails(source.close)
        self.assertEqual(self.model.events, before)
        self.assertIs(sdk._OWNERS[id(source)], source)
        self.assertIs(host._OWNERS[id(resource)], resource)

    def test_late_close_group_mutation_never_closes_new_foreign_fd(self):
        source, resource = self.consumed()
        self.model.hooks['close'] = lambda fd: resource._fds.append(999)
        self.fails(resource.close)
        self.assertEqual(len(self.model.events), 1)
        self.assertEqual(self.model.events[0][0], 'close')
        self.assertNotEqual(self.model.events[0][1], 999)
        self.assertIs(host._OWNERS[id(resource)], resource)

    def test_unknown_clock_lifetime_has_strong_refs_and_no_gc_cleanup(self):
        source, resource = self.consumed()
        self.clock.action = lambda: (_ for _ in ()).throw(PrivateError())
        self.fails(resource.monotonic_ms)
        source_ref, resource_ref = weakref.ref(source), weakref.ref(resource)
        del source, resource
        gc.collect()
        self.assertIsNotNone(source_ref())
        self.assertIsNotNone(resource_ref())
        self.assertEqual(self.model.events, [])


if __name__ == '__main__':
    unittest.main()
