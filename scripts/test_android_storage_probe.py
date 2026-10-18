"""Synthetic storage boundaries, not an Android mount/ENOSPC runtime claim."""
from copy import deepcopy
import hashlib
import os
import unittest
from unittest.mock import Mock, patch

from android_storage_probe import APP, CAPACITY, ORIGINAL, UPDATED, OwnedFilesystem, file_identity, mount_identity, original_pending, space, storage_paths


class StorageBoundaries(unittest.TestCase):
    def test_local_or_physical_host_refuses_before_ADB_or_private_storage_access(self):
        p = Mock(); p.args.serial = 'emulator-5562'
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            OwnedFilesystem(p)
        self.assertEqual(p.shell.call_count, 0)
        self.assertEqual(p.device.call_count, 0)

    def test_only_exact_user0_app_private_child_can_be_a_target(self):
        parent, target, filler = storage_paths('/data/user/0/' + APP, './files/owned/', '12345')
        self.assertEqual(target, parent + '/full-area')
        self.assertEqual(filler, '.owned-enospc-12345')
        for home, root, run in (('/data', './files/', '123'), ('/data/user/10/' + APP, './files/', '123'),
                               ('/data/user/0/' + APP, './files/../', '123'),
                               ('/data/user/0/' + APP, '/files/', '123'),
                               ('/data/user/0/' + APP, './files/', '../other')):
            with self.subTest(home=home, root=root, run=run), self.assertRaises(RuntimeError):
                storage_paths(home, root, run)

    def test_target_owner_mode_and_bounded_original_filler_are_required(self):
        value = file_identity(b'10123:10123:600:42:3:4096\n', 10123)
        self.assertEqual(value['inode'], 42)
        for raw in (b'0:0:600:42:3:4096', b'10123:10123:777:42:3:4096',
                    b'10123:10123:600:0:3:4096', f'10123:10123:600:42:3:{CAPACITY+1}'.encode(), b'unknown'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                file_identity(raw, 10123)

    def test_fullness_is_the_exact16MiB_tmpfs_not_global_free_space(self):
        self.assertEqual(space(b'tmpfs:4096:4096:0')['available_bytes'], 0)
        self.assertEqual(space(b'tmpfs:4096:4096:2048')['available_bytes'], CAPACITY // 2)
        for raw in (b'ext2/ext3:4096:4096:0', b'tmpfs:8192:4096:0', b'tmpfs:4096:4096:4097', b'tmpfs:0:0:0'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError): space(raw)

    def test_original_mount_id_source_and_restrictions_are_verified(self):
        target = '/data/user/0/' + APP + '/files/vault/full-area'
        row = f'42 24 0:47 / {target} rw,nosuid,nodev,noexec - tmpfs onx-owned-123 rw,size=16384k\n'.encode()
        value = mount_identity(row, target, 'onx-owned-123')
        self.assertEqual(value['mount_id'], 42)
        for raw in (row.replace(b'noexec', b'exec'), row.replace(b'onx-owned-123', b'other'), row + row):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                mount_identity(raw, target, 'onx-owned-123')

    def pending_fixture(self):
        file_id = '11111111-1111-4111-8111-111111111111'
        before = {'saves': [{'state': 'committed'}], 'drafts': [{'id': 'own-draft', 'file_id': file_id, 'saved_generation': 1}],
                  'files': [{'id': file_id, 'hash': hashlib.sha256(ORIGINAL).hexdigest()}], 'outbox': []}
        intent = {'state': 'prepared', 'operation_id': '22222222-2222-4222-8222-222222222222',
                  'content': UPDATED.decode(), 'error': 'FILESYSTEM_ERROR', 'file_id': file_id,
                  'expected': hashlib.sha256(ORIGINAL).hexdigest(), 'generation': 2}
        failed = {'saves': [{'state': 'committed'}, intent],
                  'drafts': [{'id': 'own-draft', 'content': UPDATED.decode(), 'generation': 2, 'saved_generation': 1}],
                  'files': deepcopy(before['files']),
                  'operations': [{'operation_id': intent['operation_id'], 'state': 'pending'}],
                  'journal': [{'operation_id': intent['operation_id'], 'state': 'pending'}],
                  'payloads': [{'operation_id': intent['operation_id'], 'hash': hashlib.sha256(UPDATED).hexdigest(), 'size': len(UPDATED)}],
                  'outbox': []}
        return before, failed

    def test_real_file_error_contract_keeps_original_id_payload_and_pending_journal(self):
        before, failed = self.pending_fixture()
        self.assertEqual(original_pending(before, failed), failed['saves'][1])

    def test_database_full_or_changed_content_does_not_prove_original_file_ENOSPC(self):
        before, failed = self.pending_fixture()
        for key, value in (('error', 'QUOTA_EXCEEDED'), ('content', 'different'), ('file_id', 'different'), ('generation', 1)):
            bad = deepcopy(failed); bad['saves'][1][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): original_pending(before, bad)

    def test_missing_journal_payload_or_premature_outbox_is_not_recoverable_proof(self):
        before, failed = self.pending_fixture()
        for table in ('journal', 'payloads', 'operations'):
            bad = deepcopy(failed); bad[table] = []
            with self.subTest(table=table), self.assertRaises(RuntimeError): original_pending(before, bad)
        bad = deepcopy(failed); bad['outbox'] = [{'operation_id': failed['saves'][1]['operation_id']}]
        with self.assertRaises(RuntimeError): original_pending(before, bad)

    def test_changed_original_file_or_lost_durable_draft_refuses_recovery_claim(self):
        before, failed = self.pending_fixture()
        for section, key, value in (('drafts', 'content', 'different'), ('drafts', 'saved_generation', 2), ('files', 'hash', '0' * 64)):
            bad = deepcopy(failed); bad[section][0][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(RuntimeError): original_pending(before, bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
