"""Pre-spawn RAM fd/writer/clock model; no real files, child or source admission."""
import builtins
from contextlib import contextmanager, ExitStack
from copy import copy, deepcopy
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import android_release_sync_association_config_channels as channels
import android_release_sync_association_config_journals as journals
import android_release_sync_association_config_resources as resources
from android_release_sync_association_private_files import OriginalAssociationPrivateFiles, _ROLES
from test_android_release_sync_association_config_channels import RamPipes, controlled


class Clock:
    def __init__(self):
        self.calls, self.values, self.hook = 0, [1_000_000], None

    def __call__(self):
        self.calls += 1
        if self.hook:
            self.hook()
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


@contextmanager
def environment():
    ram, clock = RamPipes(), Clock()
    with controlled(ram), patch.object(resources, 'os', ram), \
            patch.object(resources, 'time', SimpleNamespace(monotonic_ns=clock)), \
            patch.object(resources, '_PRESPAWN_OWNERS', {}), \
            patch.object(resources, '_LOCK', threading.Lock()):
        yield ram, clock


def fixture(ram, *, ready=True, fd_offset=0):
    ram.next_fd += fd_offset
    channel = channels.OriginalAssociationConfigChannels(bytes(bytearray(b'private-config')))
    if ready:
        channel.provision()
    store = OriginalAssociationPrivateFiles(tools=Path('C:/controlled/tools'), artifact=Path('C:/controlled/artifact'))
    store._state, store._fds = 'READY', [900 + fd_offset]
    original = journals.OriginalAssociationConfigJournals(store)
    def write(role, raw):
        assert role == 'config-command-intent' and role not in store._entries
        store._entries[role] = (raw, store._fds[0], _ROLES[role][0], (1, 2, 1000, 0o100600))
    with patch.object(store, 'write', write):
        original.record_config_intent(bytes(bytearray(b'opaque-original-intent')))
    return channel, original, store


class ResourceOwner(unittest.TestCase):
    def unavailable(self, function):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_RESOURCES_UNAVAILABLE$'):
            function()

    def channel_unavailable(self, function):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_CHANNELS_UNAVAILABLE$'):
            function()

    def clean(self, ram):
        self.assertEqual(ram.live, {})
        fds = [e[1] for e in ram.events if e[0] == 'close']
        self.assertEqual(len(fds), len(set(fds)))

    def test_constructor_zero_io_and_public_surface_without_source_admission(self):
        with environment() as (ram, clock):
            channel, original, _ = fixture(ram, ready=False)
            before = list(ram.events)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            self.assertEqual(ram.events, before)
            self.assertEqual(clock.calls, 0)
            self.assertEqual([n for n in dir(owner) if not n.startswith('_')], ['claim', 'close', 'monotonic_ms'])
            self.assertNotIn('__del__', type(owner).__dict__)
            self.assertNotIn('__exit__', type(owner).__dict__)
            for left, right in ((object(), original), (channel, object()), (None, None)):
                self.unavailable(lambda: resources.OriginalAssociationConfigResources(left, right))
            self.assertEqual(ram.events, before)
            owner.close()
            self.assertEqual(ram.events, before)

    def test_claim_moves_original_five_handles_buffers_and_store_entry_without_io(self):
        with environment() as (ram, clock):
            channel, original, store = fixture(ram)
            fds, pipes, ids, raw = channel._fds, channel._pipes, channel._identities, channel._input
            entry = original._intent
            owner = resources.OriginalAssociationConfigResources(channel, original)
            before = list(ram.events)
            owner.claim()
            self.assertEqual(ram.events, before)
            self.assertEqual(clock.calls, 0)
            self.assertIs(owner._fds, fds)
            self.assertIs(owner._pipes, pipes)
            self.assertIs(owner._identities, ids)
            self.assertIs(owner._input, raw)
            self.assertIs(owner._buffers[0], entry[0])
            self.assertIs(owner._buffers[1], raw)
            self.assertIs(original._intent, store._entries['config-command-intent'])
            self.assertIs(channel._handle_owner, owner)
            self.assertEqual(channel._state, 'TRANSFERRED')
            self.assertEqual(channel._fds, [])
            self.assertIsNone(channel._input)
            self.assertIs(resources._PRESPAWN_OWNERS[id(owner)], owner)
            owner.close()
            self.clean(ram)
            self.assertEqual(store._fds, [900])
            self.assertEqual(store._state, 'READY')
            self.assertEqual(resources._PRESPAWN_OWNERS, {})

    def test_outside_channel_close_provision_or_transfer_cannot_close_moved_handles(self):
        with environment() as (ram, _):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            owner.claim()
            before = list(ram.events)
            for action in (channel.close, channel.provision, lambda: channel._consume_for_resources(owner)):
                self.channel_unavailable(action)
                self.assertEqual(ram.events, before)
                self.assertEqual(len(ram.live), 5)
            owner.close()
            self.clean(ram)

    def test_copy_and_deepcopy_channel_or_journal_never_claim_or_close_originals(self):
        for factory in (copy, deepcopy):
            for which in ('channel', 'journal'):
                with environment() as (ram, clock):
                    channel, original, _ = fixture(ram)
                    selected = factory(channel if which == 'channel' else original)
                    left, right = (selected, original) if which == 'channel' else (channel, selected)
                    owner = resources.OriginalAssociationConfigResources(left, right)
                    before = list(ram.events)
                    self.unavailable(owner.claim)
                    if which == 'channel':
                        self.channel_unavailable(selected.close)
                    self.assertEqual(ram.events, before)
                    self.assertEqual(clock.calls, 0)
                    self.assertEqual(len(ram.live), 5)
                    channel.close()
                    self.clean(ram)

    def test_copy_of_claimed_owner_cannot_release_or_remove_original_registry_slot(self):
        for factory in (copy, deepcopy):
            with environment() as (ram, _):
                channel, original, _ = fixture(ram)
                owner = resources.OriginalAssociationConfigResources(channel, original)
                owner.claim()
                cloned = factory(owner)
                before = list(ram.events)
                self.unavailable(cloned.close)
                self.unavailable(cloned.monotonic_ms)
                self.assertEqual(ram.events, before)
                self.assertIs(resources._PRESPAWN_OWNERS[id(owner)], owner)
                owner.close()
                self.clean(ram)

    def test_foreign_consumer_does_not_inspect_private_object_or_change_channel(self):
        with environment() as (ram, _):
            channel, original, _ = fixture(ram)
            class Foreign:
                def __getattribute__(self, name):
                    raise AssertionError('private object inspected')
            before = list(ram.events)
            self.channel_unavailable(lambda: channel._consume_for_resources(Foreign()))
            self.assertEqual(ram.events, before)
            self.assertEqual(channel._state, 'READY')
            channel.close()
            self.clean(ram)

    def test_unprepared_unknown_private_aliased_entry_or_pipe_metadata_refused_before_transfer(self):
        for mode in ('unprepared', 'journal-state', 'entry-copy', 'wrong-role', 'private-raw', 'fd-alias', 'pipe-id'):
            with environment() as (ram, clock):
                channel, original, store = fixture(ram, ready=mode != 'unprepared')
                if mode == 'journal-state':
                    original._state = 'DONE'
                if mode == 'entry-copy':
                    original._intent = tuple(list(original._intent))
                if mode == 'wrong-role':
                    entry = list(original._intent); entry[2] = 'instrument-command-intent.private.json'
                    original._intent = store._entries['config-command-intent'] = tuple(entry)
                if mode == 'private-raw':
                    channel._input = object()
                if mode == 'fd-alias':
                    store._fds.append(channel._fds[0])
                if mode == 'pipe-id':
                    channel._identities[channel._fds[0]] = object()
                owner = resources.OriginalAssociationConfigResources(channel, original)
                before = list(ram.events)
                self.unavailable(owner.claim)
                self.assertEqual(ram.events, before)
                self.assertEqual(clock.calls, 0)
                self.assertEqual(resources._PRESPAWN_OWNERS, {})
                channel.close()
                self.clean(ram)

    def test_one_strong_owner_slot_refuses_another_without_touching_either_original(self):
        with environment() as (ram, _):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            owner.claim()
            other, other_journal, _ = fixture(ram, fd_offset=100)
            candidate = resources.OriginalAssociationConfigResources(other, other_journal)
            before = list(ram.events)
            self.unavailable(candidate.claim)
            self.assertEqual(ram.events, before)
            self.assertIs(resources._PRESPAWN_OWNERS[id(owner)], owner)
            owner.close(); other.close()
            self.clean(ram)

    def test_single_owner_is_retained_without_destructor_or_automatic_cleanup(self):
        with environment() as (ram, _):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            owner.claim(); reference = weakref.ref(owner); del owner
            self.assertIsNotNone(reference())
            self.assertEqual(len(ram.live), 5)
            reference().close()
            self.clean(ram)

    def test_original_clock_reference_exact_long_nonreverse_and_no_reset(self):
        with environment() as (ram, clock):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            self.assertIs(owner._clock_source, clock)
            resources.time.monotonic_ns = lambda: (_ for _ in ()).throw(AssertionError('changed clock'))
            clock.values = [1_999_999, 2_000_000, resources._MAX * 1_000_000]
            self.assertEqual(owner.monotonic_ms(), 1)
            owner.claim()
            self.assertEqual(owner.monotonic_ms(), 2)
            self.assertEqual(owner.monotonic_ms(), resources._MAX)
            self.assertEqual(clock.calls, 3)
            owner.close()
            self.clean(ram)

    def test_bad_clock_or_private_exception_has_no_following_sample_or_descriptor_retry(self):
        for value in (True, -1, object(), (resources._MAX + 1) * 1_000_000, 0):
            with environment() as (ram, clock):
                channel, original, _ = fixture(ram)
                owner = resources.OriginalAssociationConfigResources(channel, original)
                owner.claim()
                self.assertEqual(owner.monotonic_ms(), 1)
                clock.values = [value]
                self.unavailable(owner.monotonic_ms)
                calls, before = clock.calls, list(ram.events)
                self.unavailable(owner.monotonic_ms)
                self.assertEqual(clock.calls, calls)
                self.assertEqual(ram.events, before)
                self.clean(ram)
        with environment() as (ram, clock):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original); owner.claim()
            class PrivateError(Exception):
                def __str__(self):
                    raise AssertionError('private exception stringified')
            clock.hook = lambda: (_ for _ in ()).throw(PrivateError())
            self.unavailable(owner.monotonic_ms)
            self.clean(ram)

    def test_reentrant_clock_or_transfer_closes_only_known_prespawn_handles_once(self):
        with environment() as (ram, clock):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original); owner.claim()
            clock.hook = owner.close
            self.unavailable(owner.monotonic_ms)
            self.clean(ram)
        with environment() as (ram, _):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            consume = channel._consume_for_resources
            def reenter(value):
                consume(value)
                try:
                    owner.claim()
                except RuntimeError:
                    pass
            with patch.object(channel, '_consume_for_resources', reenter):
                self.unavailable(owner.claim)
            self.clean(ram)
            self.assertEqual(resources._PRESPAWN_OWNERS, {})

    def test_double_claim_and_uncertain_close_never_retry_or_release_foreign_writer(self):
        with environment() as (ram, _):
            channel, original, store = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original); owner.claim()
            self.unavailable(owner.claim)
            self.clean(ram); self.assertEqual(store._fds, [900])
        with environment() as (ram, _):
            channel, original, store = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original); owner.claim()
            ram.close_error = 10
            self.unavailable(owner.close)
            self.assertEqual(owner._uncertain_close, [10])
            self.assertIs(resources._PRESPAWN_OWNERS[id(owner)], owner)
            before = list(ram.events)
            self.unavailable(owner.close)
            self.assertEqual(ram.events, before)
            self.assertEqual(store._fds, [900]); self.clean(ram)

    def test_no_private_read_hash_sql_process_file_or_clock_during_claim(self):
        with environment() as (ram, clock):
            channel, original, _ = fixture(ram)
            owner = resources.OriginalAssociationConfigResources(channel, original)
            with ExitStack() as stack:
                def forbidden(*args, **kwargs):
                    raise AssertionError('unexpected source or runtime action')
                for obj, name in ((builtins, 'open'), (Path, 'read_bytes'), (Path, 'write_bytes'),
                        (Path, 'mkdir'), (hashlib, 'sha256'), (sqlite3, 'connect'),
                        (subprocess, 'run'), (subprocess, 'Popen'), (os, 'read'), (os, 'waitpid'), (os, 'kill')):
                    stack.enter_context(patch.object(obj, name, forbidden))
                owner.claim()
                self.assertEqual(clock.calls, 0)
                owner.close()
            self.clean(ram)


if __name__ == '__main__':
    unittest.main()
