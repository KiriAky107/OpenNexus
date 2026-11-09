"""Source-buffer RAM checks and three owned real-file writer checks.

SDK/pipe/clock sources are controlled models, not actual factory/Native proof.
"""
import builtins
from contextlib import contextmanager, ExitStack
from copy import copy, deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sync_association_config_native_intent as native
import android_release_sync_association_config_channels as channels
import android_release_sync_association_config_journals as journals
import android_release_sync_association_config_resources as resources
import android_release_sync_association_config_executable as executable
import android_release_sync_association_private_files as files
from test_android_release_sync_association_config_host import context
from test_android_release_sync_association_config_resources import environment
from test_android_release_sync_association_config_executable import model
from test_android_release_sync_association_private_files import PosixFiles, PrivateFiles


@contextmanager
def sources(store=None):
    with environment() as (ram, clock), model() as sdk, ExitStack() as stack:
        ram.next_fd, sdk.next_fd = 100, 200  # Disjoint from real fixture file fds.
        value = context()
        original_input = json.dumps(dict(vault_id=value['launch_scope']['remote_vault'],
            vault_name=value['vault_name'])).encode('utf-8')
        channel = channels.OriginalAssociationConfigChannels(original_input); channel.provision()
        writes = []
        if store is None:
            store = files.OriginalAssociationPrivateFiles(tools=Path('C:/controlled/tools'), artifact=Path('C:/controlled/artifact'))
            store._state, store._fds = 'READY', [900]
            def writer(role, raw):
                writes.append((role, raw))
                assert role not in store._entries
                fd = 901 + len(store._entries); store._fds.append(fd)
                store._attempted.add(role)
                store._entries[role] = (raw, fd, files._ROLES[role][0], (1, 2, 1234, 0o100600))
            stack.enter_context(patch.object(store, 'write', writer))
        original = journals.OriginalAssociationConfigJournals(store)
        raw = native.config.common.encoded(dict(schema=1, launch_scope=value['launch_scope'],
            command_id=value['command_id'], arguments=list(native.config._ARGUMENTS),
            private_destination=native.config._PATH, stdin_bytes=len(original_input), automatic_retry=False))
        original.record_config_intent(raw)
        owner = resources.OriginalAssociationConfigResources(channel, original); owner.claim(); owner.monotonic_ms()
        pin = executable.OriginalAssociationConfigExecutable('/owned/sdk'); pin.pin()
        binding = native.OriginalAssociationConfigNativeIntent(owner, pin)
        try:
            yield SimpleNamespace(owner=owner, pin=pin, store=store, journals=original,
                binding=binding, raw=raw, input=original_input, ram=ram, sdk=sdk, clock=clock, writes=writes)
        finally:
            owner.close(); pin.close()
            assert not ram.live and not sdk.live


class NativeIntent(unittest.TestCase):
    def refused(self, function):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_NATIVE_INTENT_UNAVAILABLE$'):
            function()

    def test_constructor_is_zero_io_and_has_only_record_without_raw_or_admission(self):
        with sources() as s:
            before = (list(s.ram.events), list(s.sdk.events), s.clock.calls, list(s.writes))
            other = native.OriginalAssociationConfigNativeIntent(s.owner, s.pin)
            self.assertEqual([n for n in dir(other) if not n.startswith('_')], ['record'])
            for left, right in ((object(), s.pin), (s.owner, object()), (None, None)):
                self.refused(lambda: native.OriginalAssociationConfigNativeIntent(left, right))
            self.assertEqual(before, (s.ram.events, s.sdk.events, s.clock.calls, s.writes))

    def test_same_source_buffers_full_native_argv_and_original_store_entry_no_extra_IO(self):
        with sources() as s:
            before = (list(s.ram.events), list(s.sdk.events), s.clock.calls)
            self.assertIsNone(s.binding.record())
            entry = s.binding._entry
            self.assertIs(entry, s.store._entries[native._ROLE])
            self.assertIs(entry[0], s.writes[-1][1])
            self.assertIs(s.binding._sources[0], s.raw)
            self.assertIs(s.binding._sources[1], s.input)
            self.assertIs(s.binding._sources[2], s.pin._arguments)
            self.assertIs(s.binding._clock_source, s.owner._clock_source)
            value = json.loads(entry[0])
            self.assertEqual(value['native_arguments'], list(s.pin._arguments))
            self.assertEqual(value['native_executable_parent_fd_path'], '/proc/4242/fd/204')
            self.assertEqual(value['config_command_id'], context()['command_id'])
            self.assertEqual(value['source_host_observed_monotonic_ms'], s.owner._last)
            self.assertNotIn('stdin', value); self.assertNotIn('sha256', value)
            self.assertEqual(before, (s.ram.events, s.sdk.events, s.clock.calls))
            self.assertEqual(s.journals._state, 'INTENT_RECORDED')

    def test_duplicate_and_second_binding_do_not_rewrite_or_close_originals(self):
        with sources() as s:
            s.binding.record(); raw = s.binding._entry[0]
            before = (len(s.writes), list(s.ram.events), list(s.sdk.events))
            self.refused(s.binding.record)
            self.refused(native.OriginalAssociationConfigNativeIntent(s.owner, s.pin).record)
            self.assertEqual(before, (len(s.writes), s.ram.events, s.sdk.events))
            self.assertIs(s.store._entries[native._ROLE][0], raw)

    def test_private_or_malformed_profiles_are_rejected_before_encode_or_writer(self):
        class Private:
            def __str__(self): raise AssertionError('private stringification')
        for mode in ('duplicate', 'constant', 'scope', 'input', 'argv', 'clock', 'pid', 'identity'):
            with sources() as s:
                if mode in ('duplicate', 'constant', 'scope'):
                    raw = b'{"schema":1,"schema":1}' if mode == 'duplicate' else b'NaN' if mode == 'constant' else b'{"scope":"private"}'
                    entry = s.journals._intent
                    s.journals._intent = s.store._entries['config-command-intent'] = (raw, *entry[1:])
                    s.owner._buffers = (raw, s.owner._input)
                if mode == 'input':
                    s.owner._input = b'{"vault_id":"private","vault_name":"private"}'
                    s.owner._buffers = (s.raw, s.owner._input)
                if mode == 'argv': s.pin._arguments = (Private(),)
                if mode == 'clock': s.owner._last = Private()
                if mode == 'pid': s.pin._parent_pid = Private()
                if mode == 'identity':
                    e = s.pin._edges[-1]; s.pin._edges[-1] = (*e[:3], (Private(),), e[4])
                encoded = []
                def forbidden(*args): encoded.append(True); raise AssertionError('early encoding')
                before = len(s.writes)
                with patch.object(native.config.common, 'encoded', forbidden): self.refused(s.binding.record)
                self.assertEqual(encoded, []); self.assertEqual(len(s.writes), before)

    def test_copied_owner_pin_binding_or_original_entry_alias_cannot_record(self):
        for mode in ('owner-copy', 'pin-copy', 'binding-copy', 'entry-alias'):
            with sources() as s:
                binding = s.binding
                if mode == 'owner-copy': binding = native.OriginalAssociationConfigNativeIntent(copy(s.owner), s.pin)
                if mode == 'pin-copy': binding = native.OriginalAssociationConfigNativeIntent(s.owner, deepcopy(s.pin))
                if mode == 'binding-copy': binding = copy(binding)
                if mode == 'entry-alias': s.journals._intent = tuple(list(s.journals._intent))
                before = (len(s.writes), list(s.ram.events), list(s.sdk.events))
                self.refused(binding.record)
                self.assertEqual(before, (len(s.writes), s.ram.events, s.sdk.events))

    def test_unknown_partial_writer_reentry_and_late_source_change_burn_without_retry(self):
        for mode in ('unknown', 'error', 'raw-copy', 'reentry', 'late-clock', 'late-input'):
            with sources() as s:
                original_write = s.store.write
                def writer(role, raw):
                    if mode == 'error': raise RuntimeError('private writer failure')
                    original_write(role, raw)
                    if mode == 'raw-copy':
                        e = s.store._entries[role]; s.store._entries[role] = (memoryview(raw).tobytes(), *e[1:])
                    if mode == 'reentry': self.refused(s.binding.record)
                    if mode == 'late-clock': s.owner._last += 1
                    if mode == 'late-input': s.owner._input = bytes(bytearray(s.input))
                    return object() if mode == 'unknown' else None
                with patch.object(s.store, 'write', writer): self.refused(s.binding.record)
                self.assertIsNone(s.binding._entry)
                before = len(s.writes); self.refused(s.binding.record)
                self.assertEqual(len(s.writes), before)

    def test_descriptor_alias_or_closed_source_refused_without_touching_unowned_fds(self):
        for mode in ('alias', 'closed', 'registry'):
            with sources() as s:
                saved = list(s.pin._fds)
                if mode == 'alias': s.pin._fds[0] = s.owner._fds[0]
                if mode == 'closed': s.journals._state = 'DONE'
                if mode == 'registry': resources._PRESPAWN_OWNERS.clear()
                before = (list(s.ram.events), list(s.sdk.events), len(s.writes))
                self.refused(s.binding.record)
                self.assertEqual(before, (s.ram.events, s.sdk.events, len(s.writes)))
                s.pin._fds[:] = saved
                if mode == 'registry': resources._PRESPAWN_OWNERS[id(s.owner)] = s.owner

    def test_no_raw_file_read_hash_SQL_clock_process_or_default_success_port(self):
        with sources() as s, ExitStack() as stack:
            calls = []
            def forbidden(*args, **kwargs): calls.append(True); raise AssertionError('forbidden side effect')
            for target, name in ((builtins, 'open'), (Path, 'read_bytes'), (Path, 'open'),
                    (hashlib, 'sha256'), (sqlite3, 'connect'), (subprocess, 'Popen'), (subprocess, 'run')):
                stack.enter_context(patch.object(target, name, forbidden))
            before = s.clock.calls
            s.binding.record()
            self.assertEqual(calls, []); self.assertEqual(s.clock.calls, before)
            self.assertEqual(s.binding._state, 'RECORDED')


class NativeIntentFiles(unittest.TestCase):
    setUp = PrivateFiles.setUp
    cleanup_case = PrivateFiles.cleanup_case

    def test_new_role_real_exclusive_fsync_and_same_native_writer_buffer(self):
        self.assertEqual(files._ROLES[native._ROLE], ('signed-association-config-native-command-intent.private.json', 4096))
        store = files.OriginalAssociationPrivateFiles(tools=self.tools, artifact=self.artifact)
        with PosixFiles() as io:
            store.provision()
            try:
                with sources(store) as s:
                    s.binding.record(); raw = s.binding._entry[0]
                    self.assertIs(s.binding._entry, store._entries[native._ROLE])
                    self.assertEqual(sum(e[0] == 'fsync' for e in io.events), 2)
                    self.assertEqual(set(store._entries), {'config-command-intent', native._ROLE})
            finally: store.close()
        self.assertEqual((self.task / files._ROLES[native._ROLE][0]).read_bytes(), raw)

    def test_existing_native_file_refuses_without_overwrite_or_retry(self):
        store = files.OriginalAssociationPrivateFiles(tools=self.tools, artifact=self.artifact)
        with PosixFiles():
            store.provision()
            try:
                with sources(store) as s:
                    target = self.task / files._ROLES[native._ROLE][0]; target.write_bytes(b'original-existing')
                    with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_NATIVE_INTENT_UNAVAILABLE$'): s.binding.record()
                    self.assertIsNone(s.binding._entry)
                    with self.assertRaises(RuntimeError): s.binding.record()
            finally: store.close()
        self.assertEqual(target.read_bytes(), b'original-existing')

    def test_native_fsync_failure_preserves_partial_without_approval_or_rewrite(self):
        store = files.OriginalAssociationPrivateFiles(tools=self.tools, artifact=self.artifact)
        with PosixFiles() as io:
            store.provision()
            try:
                with sources(store) as s:
                    def fail(fd): raise OSError('controlled native intent fsync failure')
                    io.on_fsync = fail
                    with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_NATIVE_INTENT_UNAVAILABLE$'): s.binding.record()
                    self.assertIsNone(s.binding._entry); self.assertEqual(store._state, 'FAILED')
                    before = list(io.events)
                    with self.assertRaises(RuntimeError): s.binding.record()
                    self.assertEqual(io.events, before)
            finally: store.close()
        self.assertGreater((self.task / files._ROLES[native._ROLE][0]).stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
