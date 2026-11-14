"""RAM lifecycle models only; the scoped Linux job checks actual owned cleanup."""
import copy
import gc
import hashlib
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch
import weakref

import android_release_platform_tools as prepared
import android_release_preconfig_host_resources as host
import android_release_preconfig_sdk as sdk
import test_android_release_preconfig_host_resources as host_tests
from test_android_release_preconfig_sdk import PrivateError, PrivateValue


class PreparedHostOwnership(unittest.TestCase):
    def setUp(self):
        host_tests.HostResources.setUp(self)  # RAM fixture helper, no old test bodies.
        self.adb = b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00'
        self.cleanup_action = None
        self.patches.extend([
            patch.object(prepared, 'os', self.model),
            patch.object(prepared, 'ADB_BYTES', len(self.adb)),
            patch.object(prepared, 'ADB_SHA256', hashlib.sha256(self.adb).hexdigest()),
            patch.object(prepared.PreparedPlatformTools, '_guard', lambda p: p._original()),
            patch.object(prepared.PreparedPlatformTools, '_close_owned_files', self.cleanup),
        ])
        for p in self.patches[-5:]:
            p.start()
        self.model.hooks['pread'] = lambda fd, count, offset: (
            self.adb if self.model.fds[fd].endswith('/adb') else b'PK\x03\x04')

    def tearDown(self):
        prepared._OWNERS.clear()  # RAM registry discard, never native cleanup/Stop.
        host_tests.HostResources.tearDown(self)

    def fails(self, call):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_(?:PRECONFIG_(?:SDK|HOST_RESOURCES)|PLATFORM_TOOLS_PREPARE)_UNAVAILABLE$'):
            call()

    def cleanup(self):
        self.model.event('prepared_cleanup')
        if self.cleanup_action:
            self.cleanup_action()
        for p in list(prepared._OWNERS.values()):
            while p._fds:
                p._close_fd(p._fds[-1])
            p._created.clear()

    def source(self):
        s = sdk.OriginalPreconfigSdk()
        s.pin()
        p = prepared.PreparedPlatformTools()
        p._root = PurePosixPath('/owned/sdk')
        p._owner, p._source, p._environment = {}, {}, s._context
        p._state = 'READY'
        prepared._OWNERS[id(p)] = p
        p.bind_sdk(s)  # Production descriptor/hash binder over the RAM syscall model.
        self.model.events.clear()
        return p, s

    def consumed(self):
        p, s = self.source()
        h = host.OriginalPreconfigHostResources(s)
        h.claim()
        self.model.events.clear()
        return p, s, h

    def assert_three_slots(self, p, s, h):
        self.assertIs(prepared._OWNERS[id(p)], p)
        self.assertIs(sdk._OWNERS[id(s)], s)
        self.assertIs(host._OWNERS[id(h)], h)

    def test_constructor_and_unclaimed_close_leave_prepare_ownership_untouched(self):
        p, s = self.source()
        h = host.OriginalPreconfigHostResources(s)
        h.close()
        self.assertEqual(self.model.events, [])
        self.assertEqual(self.clock.calls, 0)
        self.assertIsNone(p._host)
        self.assertIs(s._handle_owner, s)

    def test_claim_retains_same_prepare_binding_and_moves_one_sdk_authority(self):
        p, s = self.source()
        original = s._fds, s._edges, s._aliases, p._binding
        h = host.OriginalPreconfigHostResources(s)
        self.assertIsNone(h.claim())
        self.assertIs(h._platform_tools_source, p)
        self.assertIs(h._platform_tools_binding, p._host_binding)
        self.assertIs(p._host, h)
        self.assertIs(s._handle_owner, h)
        for a, b in zip((h._fds, h._edges, h._aliases, p._binding), original):
            self.assertIs(a, b)
        self.assert_three_slots(p, s, h)
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual([e[0] for e in self.model.events], ['fstat', 'inheritable', 'pread'])

    def test_external_prepare_probe_verify_and_close_are_revoked_before_io(self):
        p, s, h = self.consumed()
        for call in (p.close, p.probe_version_once, lambda: p.verify_sdk(s), s.close):
            self.fails(call)
        self.assertEqual(self.model.events, [])
        self.assertEqual(h._state, 'READY')
        self.assert_three_slots(p, s, h)

    def test_clock_keeps_same_direct_source_without_extra_sdk_reads(self):
        p, s, h = self.consumed()
        self.assertEqual(h.monotonic_ms(), 1)
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(self.model.events, [])
        self.assertIs(h._clock_source, self.clock.function)

    def test_known_host_close_orders_sdk_fds_then_prepared_files_then_slot_retirement(self):
        p, s, h = self.consumed()
        expected = [('close', fd) for fd in reversed(h._fds)] + [('prepared_cleanup',)]
        self.cleanup_action = lambda: self.assert_three_slots(p, s, h)
        h.close()
        h.close()
        self.assertEqual(self.model.events, expected)
        self.assertEqual((h._state, p._state), ('CLOSED', 'CLOSED'))
        self.assertEqual((host._OWNERS, sdk._OWNERS, prepared._OWNERS), ({}, {}, {}))

    def test_copied_or_private_prepare_rejects_without_closing_sdk_fds(self):
        p, s = self.source()
        for bad in (copy.copy(p), PrivateValue()):
            s._platform_tools_source = bad
            self.fails(host.OriginalPreconfigHostResources(s).claim)
            self.assertEqual(self.model.events, [])
            self.assertIs(s._handle_owner, s)
        s._platform_tools_source = p

    def test_private_hash_read_failure_keeps_all_original_owners_without_transfer(self):
        p, s = self.source()
        h = host.OriginalPreconfigHostResources(s)
        self.model.hooks['pread'] = lambda *args: (_ for _ in ()).throw(PrivateError())
        self.fails(h.claim)
        self.assertIs(s._handle_owner, s)
        self.assertEqual(p._state, 'HOST_UNKNOWN')
        self.assert_three_slots(p, s, h)
        before = list(self.model.events)
        for call in (h.close, p.close, p.probe_version_once, lambda: p.verify_sdk(s)):
            self.fails(call)
        self.assertEqual(self.model.events, before)

    def test_late_claim_reentry_stops_before_sdk_authority_move(self):
        p, s = self.source()
        h = host.OriginalPreconfigHostResources(s)
        def reenter(*args):
            self.fails(h.close)
            return self.adb
        self.model.hooks['pread'] = reenter
        self.fails(h.claim)
        self.assertIs(s._handle_owner, s)
        self.assertEqual(p._state, 'HOST_UNKNOWN')
        self.assertFalse(any(e[0] in ('close', 'prepared_cleanup') for e in self.model.events))
        self.assert_three_slots(p, s, h)

    def test_unknown_clock_keeps_three_strong_slots_and_no_gc_cleanup(self):
        p, s, h = self.consumed()
        self.clock.action = lambda: (_ for _ in ()).throw(PrivateError())
        self.fails(h.monotonic_ms)
        refs = weakref.ref(p), weakref.ref(s), weakref.ref(h)
        del p, s, h
        gc.collect()
        self.assertTrue(all(r() is not None for r in refs))
        self.assertEqual(self.model.events, [])

    def test_uncertain_sdk_fd_close_does_not_start_prepare_cleanup_or_retry(self):
        p, s, h = self.consumed()
        self.model.hooks['close'] = lambda fd: (_ for _ in ()).throw(PrivateError())
        self.fails(h.close)
        self.assertEqual(len(self.model.events), 1)
        before = list(self.model.events)
        for call in (h.close, s.close, p.close):
            self.fails(call)
        self.assertEqual(self.model.events, before)
        self.assert_three_slots(p, s, h)

    def test_prepare_cleanup_failure_retains_all_slots_and_no_retry(self):
        p, s, h = self.consumed()
        self.cleanup_action = lambda: (_ for _ in ()).throw(PrivateError())
        self.fails(h.close)
        self.assertEqual(h._fds, [])
        self.assertEqual(p._state, 'CLOSING')
        self.assert_three_slots(p, s, h)
        before = list(self.model.events)
        for call in (h.close, s.close, p.close):
            self.fails(call)
        self.assertEqual(self.model.events, before)

    def test_source_change_during_sdk_close_stops_before_next_descriptor(self):
        p, s, h = self.consumed()
        self.model.hooks['close'] = lambda fd: setattr(p, '_state', PrivateValue())
        self.fails(h.close)
        self.assertEqual(len(self.model.events), 1)
        self.assertEqual(len(h._fds), 7)
        self.assert_three_slots(p, s, h)
        before = list(self.model.events)
        self.fails(h.close)
        self.assertEqual(self.model.events, before)

    def test_source_private_fd_mutation_rejects_before_clock_or_cleanup(self):
        p, s, h = self.consumed()
        p._fds.append(PrivateValue())
        self.fails(h.monotonic_ms)
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual(self.model.events, [])
        self.assert_three_slots(p, s, h)

    def test_private_source_state_is_rejected_without_comparison_or_clock(self):
        p, s, h = self.consumed()
        p._state = PrivateValue()
        self.fails(h.monotonic_ms)
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual(self.model.events, [])
        self.assert_three_slots(p, s, h)

    def test_private_source_stamp_is_rejected_before_clock_or_file_operations(self):
        p, s, h = self.consumed()
        p._host_stamp = PrivateValue()
        self.fails(h.monotonic_ms)
        self.assertEqual(self.clock.calls, 0)
        self.assertEqual(self.model.events, [])
        self.assert_three_slots(p, s, h)

    def test_sdk_retirement_contention_keeps_prepared_closed_files_slot_reserved(self):
        p, s, h = self.consumed()
        self.assertTrue(sdk._LOCK.acquire(False))
        try:
            self.fails(h.close)
        finally:
            sdk._LOCK.release()
        self.assertEqual(p._state, 'HOST_CLOSED')
        self.assert_three_slots(p, s, h)
        before = list(self.model.events)
        for call in (h.close, p.close):
            self.fails(call)
        self.assertEqual(self.model.events, before)

    def test_copied_host_cannot_close_prepared_files(self):
        p, s, h = self.consumed()
        self.fails(lambda: p._close_from_host(copy.copy(h)))
        self.assertEqual(self.model.events, [])
        self.assert_three_slots(p, s, h)

    def test_cleanup_return_guard_stops_after_late_host_failure(self):
        p, s, h = self.consumed()
        h._state, p._state = 'CLOSING', 'CLOSING'
        calls = []
        def late():
            calls.append('owned operation')
            self.fails(h.close)
        self.fails(lambda: p._cleanup_call(late))
        self.assertEqual(calls, ['owned operation'])
        self.assertEqual(self.model.events, [])
        self.assert_three_slots(p, s, h)


if __name__ == '__main__':
    unittest.main()
