"""Small real-file mechanical binding checks with controlled POSIX semantics.

Windows directory/no-follow/mode facts are bridged, not native Linux proof.
Exclusive creation, writes/fsync/hardlinks use the real owned fixture tree.
"""
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch

import android_release_sync_association_config_journals as journals
import android_release_sync_association_private_files as files
from test_android_release_sync_association_private_files import PosixFiles, PrivateFiles


class ConfigJournals(unittest.TestCase):
    # Reuse only these fixture functions; do not inherit/run old test methods.
    setUp = PrivateFiles.setUp
    cleanup_case = PrivateFiles.cleanup_case

    def store(self, index=None):
        tools = self.tools if index is None else self.case / ('tools-' + str(index))
        if index is not None:
            tools.mkdir()
        return files.OriginalAssociationPrivateFiles(tools=tools, artifact=self.artifact)

    def refused(self, value, action):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_JOURNALS_UNAVAILABLE$'):
            action()
        self.assertEqual(value._state, 'FAILED')
        self.assertIsNone(value._intent)
        self.assertIsNone(value._result)

    def test_constructor_has_no_IO_provision_or_public_lease(self):
        store = self.store()
        with patch.object(os, 'open', side_effect=AssertionError), \
             patch.object(os, 'stat', side_effect=AssertionError), \
             patch.object(os, 'mkdir', side_effect=AssertionError), \
             patch.object(time, 'monotonic', side_effect=AssertionError), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError):
            value = journals.OriginalAssociationConfigJournals(store)
            self.assertIs(value._store, store)
            self.assertEqual(store._state, 'NEW')
            self.assertEqual({n for n in vars(type(value)) if not n.startswith('_')},
                             {'record_config_intent', 'record_config_result'})
            for supplied in (object(), None, {'store': store}):
                with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_JOURNALS_UNAVAILABLE$'):
                    journals.OriginalAssociationConfigJournals(supplied)

    def test_real_two_journals_retain_original_store_entries_and_writer_buffers(self):
        store = self.store()
        value = journals.OriginalAssociationConfigJournals(store)
        intent = bytes(bytearray(b'controlled config command metadata'))
        result = bytes(bytearray(b'controlled known0 config metadata'))
        with PosixFiles() as io:
            store.provision()
            self.assertIsNone(value.record_config_intent(intent))
            self.assertIsNone(value.record_config_result(result))
            self.assertIs(value._intent, store._entries['config-command-intent'])
            self.assertIs(value._result, store._entries['config-command-result'])
            self.assertIs(value._intent[0], intent)
            self.assertIs(value._result[0], result)
            self.assertNotEqual(value._intent[1], value._result[1])
            self.assertEqual(sum(e[0] == 'fsync' for e in io.events), 2)
            self.assertEqual(set(store._entries), {'config-command-intent', 'config-command-result'})
            store.close()
        self.assertEqual((self.task / files._ROLES['config-command-intent'][0]).read_bytes(), intent)
        self.assertEqual((self.task / files._ROLES['config-command-result'][0]).read_bytes(), result)

    def test_correct_new_roles_and_4096_bound_do_not_use_instrument_or_Stop_names(self):
        self.assertEqual(files._ROLES['config-command-intent'],
                         ('signed-association-config-command-intent.private.json', 4096))
        self.assertEqual(files._ROLES['config-command-result'],
                         ('signed-association-config-command-result.private.json', 4096))
        store = self.store()
        with PosixFiles():
            store.provision()
            value = journals.OriginalAssociationConfigJournals(store)
            value.record_config_intent(b'x' * 4096)
            value.record_config_result(b'y' * 4096)
            store.close()
        self.assertEqual({p.name for p in self.task.iterdir()},
                         {files._ROLES[role][0] for role in ('config-command-intent','config-command-result')})

    def test_private_invalid_buffers_and_out_of_order_close_only_owned_fds(self):
        class Private:
            def __str__(self): raise AssertionError('private coercion')
            def __bytes__(self): raise AssertionError('private coercion')
        for index, raw in enumerate((Private(), b'', b'x'*4097, bytearray(b'x'))):
            store = self.store(index)
            with PosixFiles() as io:
                store.provision()
                value = journals.OriginalAssociationConfigJournals(store)
                start = len(io.events)
                self.refused(value, lambda: value.record_config_intent(raw))
                self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))
                self.assertEqual(store._fds, [])
        store = self.store()
        with PosixFiles() as io:
            store.provision()
            value = journals.OriginalAssociationConfigJournals(store)
            start = len(io.events)
            self.refused(value, lambda: value.record_config_result(b'not-yet'))
            self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))

    def test_duplicate_or_second_binding_never_replaces_original_journal(self):
        for index, second in enumerate((False, True)):
            store = self.store(index)
            with PosixFiles() as io:
                store.provision()
                value = journals.OriginalAssociationConfigJournals(store)
                value.record_config_intent(b'original')
                if second:
                    value = journals.OriginalAssociationConfigJournals(store)
                start = len(io.events)
                self.refused(value, lambda: value.record_config_intent(b'replacement'))
                self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))
                before = list(io.events)
                self.refused(value, lambda: value.record_config_result(b'retry'))
                self.assertEqual(io.events, before)
            target = self.case / ('tools-' + str(index)) / 'association-private-files' / files._ROLES['config-command-intent'][0]
            self.assertEqual(target.read_bytes(), b'original')

    def test_closed_owner_or_replaced_RAM_entry_refuses_before_result_write(self):
        for index, replace in enumerate((False, True)):
            store = self.store(index)
            with PosixFiles() as io:
                store.provision()
                value = journals.OriginalAssociationConfigJournals(store)
                value.record_config_intent(b'original')
                if replace:
                    entry = store._entries['config-command-intent']
                    store._entries['config-command-intent'] = tuple(list(entry))
                else:
                    store.close()
                start = len(io.events)
                self.refused(value, lambda: value.record_config_result(b'unknown'))
                self.assertTrue(all(e[0] == 'close' for e in io.events[start:]))

    def test_result_fsync_failure_preserves_original_without_retry_or_result_approval(self):
        store = self.store()
        value = journals.OriginalAssociationConfigJournals(store)
        with PosixFiles() as io:
            store.provision()
            value.record_config_intent(b'original intent')
            def fail(fd): raise OSError('controlled fsync failure')
            io.on_fsync = fail
            self.refused(value, lambda: value.record_config_result(b'written-not-approved'))
            before = list(io.events)
            self.refused(value, lambda: value.record_config_result(b'retry'))
            self.assertEqual(io.events, before)
            self.assertEqual(store._fds, [])
        self.assertEqual((self.task / files._ROLES['config-command-intent'][0]).read_bytes(), b'original intent')
        self.assertEqual((self.task / files._ROLES['config-command-result'][0]).read_bytes(), b'written-not-approved')

    def test_real_hardlink_or_fsync_reentry_closes_before_further_store_IO(self):
        for index, reentry in enumerate((False, True)):
            store = self.store(index)
            with PosixFiles() as io:
                store.provision()
                value = journals.OriginalAssociationConfigJournals(store)
                if reentry:
                    def reenter(fd):
                        try: value.record_config_result(b'premature')
                        except RuntimeError: pass
                    io.on_fsync = reenter
                    self.refused(value, lambda: value.record_config_intent(b'original'))
                    after_fsync = next(i for i, e in enumerate(io.events) if e[0] == 'fsync') + 1
                    self.assertTrue(all(e[0] == 'close' for e in io.events[after_fsync:]))
                else:
                    value.record_config_intent(b'original')
                    target = self.case / ('tools-' + str(index)) / 'association-private-files' / files._ROLES['config-command-intent'][0]
                    os.link(target, self.case / 'controlled-hardlink')
                    start = len(io.events)
                    self.refused(value, lambda: value.record_config_result(b'next'))
                    self.assertFalse(any(e[0] in ('open','write','fsync') for e in io.events[start:]))
                self.assertEqual(store._fds, [])

    def test_binding_has_no_raw_read_hash_SQL_clock_or_process_and_does_not_approve_runtime(self):
        store = self.store()
        with PosixFiles():
            store.provision()
            with patch('builtins.open', side_effect=AssertionError), \
                 patch.object(Path, 'open', side_effect=AssertionError), \
                 patch.object(os, 'read', side_effect=AssertionError), \
                 patch.object(hashlib, 'sha256', side_effect=AssertionError), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError), \
                 patch.object(time, 'monotonic', side_effect=AssertionError), \
                 patch.object(subprocess, 'Popen', side_effect=AssertionError):
                value = journals.OriginalAssociationConfigJournals(store)
                value.record_config_intent(b'opaque approved metadata only')
                self.assertIsNone(value.record_config_result(b'not a Native or readback ACK'))
                self.assertEqual(value._state, 'DONE')
                store.close()


if __name__ == '__main__':
    unittest.main()
