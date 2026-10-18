"""Data-only guards for the new UI lifecycle probe; not device acceptance."""
from copy import deepcopy
from contextlib import closing, ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from android_file_actions_probe import BODY, UPDATED, NODE, changed_content, committed, frozen, intent, paths, snapshot
from android_runtime_probe import Probe

FILE = '11111111-1111-4111-8111-111111111111'
OP = '22222222-2222-4222-8222-222222222222'


class FileActionBoundaries(unittest.TestCase):
    def test_original_creation_intent_keeps_null_file_ID_while_receipt_binds_the_saved_file(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as resources:
            root = Path(directory); tools = root / 'tools'; tools.mkdir(); output = root / 'evidence'; output.mkdir()
            original, _ = paths('md'); digest = hashlib.sha256(BODY).hexdigest()
            draft = resources.enter_context(closing(sqlite3.connect(root / 'drafts.sqlite3')))
            draft.executescript('''CREATE TABLE drafts(id,file_id,path,base_hash,generation,saved_generation,content,edit_session);
                CREATE TABLE save_intents(operation_id,draft_id,generation,file_id,path,expected,content,state,error);
                CREATE TABLE mutation_intents(operation_id,kind,file_id,path,destination,expected,state,error);''')
            draft.execute('INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?)', ('own-draft', FILE, original, digest, 1, 1, BODY.decode(), 'own-session'))
            draft.execute('INSERT INTO save_intents VALUES (?,?,?,?,?,?,?,?,?)', (OP, 'own-draft', 1, None, original, '', BODY.decode(), 'committed', None))
            draft.commit()
            host = root / 'host.sqlite3'
            with closing(sqlite3.connect(host)) as db:
                db.executescript('''CREATE TABLE files(id,path,hash,revision,deleted);
                    CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state);
                    CREATE TABLE operations(operation_id,fingerprint,state,result);''')
                db.execute('INSERT INTO files VALUES (?,?,?,?,?)', (FILE, original, digest, 1, 0))
                db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,?,?)', (OP, FILE, 1, original, digest, 'put', 'pending'))
                db.execute('INSERT INTO operations VALUES (?,?,?,?)', (OP, 'a' * 64, 'committed',
                    json.dumps({'file_id': FILE, 'path': original, 'hash': digest, 'deleted': False})))
                db.commit()
            def read_private(path, **_):
                self.assertIn(path, ('./vault/.ainote/host.sqlite3', './vault/.ainote/host.sqlite3-wal', './vault/.ainote/host.sqlite3-shm'))
                return subprocess.CompletedProcess([], 0, host.read_bytes(), b'') if path.endswith('host.sqlite3') else subprocess.CompletedProcess([], 1, b'', b'')
            probe = SimpleNamespace(root='./', tools=tools, output=output,
                database_rows=lambda query, params: draft.execute(query, params).fetchall(), private=read_private)
            # An original creation intent is immutable: its NULL is never
            # retroactively rewritten to the ID allocated by the native commit.
            first = snapshot(probe, 'md', 'own-created', FILE)
            self.assertEqual(len(first['saves']), 1)
            self.assertIsNone(first['saves'][0]['file_id'])
            self.assertEqual(json.loads(first['operations'][0]['result'])['file_id'], FILE)
            draft.execute('DELETE FROM drafts'); draft.commit()
            cold = snapshot(probe, 'md', 'own-deleted-cold', FILE)
            self.assertEqual(cold['saves'], first['saves'])
            self.assertEqual(cold['drafts'], [])
            with self.assertRaisesRegex(RuntimeError, 'bind its native committed file result'):
                snapshot(probe, 'md', 'wrong-original-file', OP)
            draft.execute('UPDATE save_intents SET expected=?', ('b' * 64,)); draft.commit()
            with self.assertRaisesRegex(RuntimeError, 'bind its native committed file result'):
                snapshot(probe, 'md', 'null-update-is-not-creation', FILE)

    def test_explicit_file_scope_dispatches_only_its_new_owned_lifecycle(self):
        probe = Probe.__new__(Probe)
        probe.args = SimpleNamespace(scope='file-actions')
        probe.boot = Mock()
        with patch('android_file_actions_probe.run_file_actions') as run:
            probe.run()
        probe.boot.assert_called_once_with()
        run.assert_called_once_with(probe)

    def test_same_immutable_native_suite_is_not_replayed_in_file_action_scope(self):
        probe = Probe.__new__(Probe)
        probe.args = SimpleNamespace(scope='file-actions')
        probe.shell = Mock()
        probe.native_keystore_tests()
        probe.shell.assert_not_called()

    def test_only_two_explicit_owned_original_and_unicode_destination_paths(self):
        self.assertEqual(paths('md'), ('fileops-original.md', 'fileops/资料😀.md'))
        self.assertEqual(paths('canvas'), ('fileops-original.canvas', 'fileops/资料😀.canvas'))
        for value in ('../x', '', 'png', 'MD', None):
            with self.subTest(value=value), self.assertRaises(RuntimeError): paths(value)

    def test_MD_concurrency_is_the_exact_original_with_CRLF_and_new_CJK_emoji(self):
        result = changed_content('md', BODY)
        self.assertEqual(result, UPDATED)
        self.assertIn('并发更改'.encode(), result)
        self.assertNotIn(b'\n', result.replace(b'\r\n', b''))
        with self.assertRaises(RuntimeError): changed_content('md', BODY + b'different')

    def test_Canvas_concurrency_keeps_original_graph_and_records_unknown_fields(self):
        value = {'nodes': [{'id': 'own-node', 'type': 'text', 'text': NODE, 'x': 4, 'y': 7,
                            'vendor-existing': {'original': True}}], 'edges': [], 'vendor-existing': [3, '原文']}
        result = changed_content('canvas', json.dumps(value, ensure_ascii=False).encode())
        read = json.loads(result)
        self.assertTrue(result.endswith(b'\r\n'))
        self.assertEqual(read['vendor-existing'], value['vendor-existing'])
        self.assertEqual(read['nodes'][0]['vendor-existing'], value['nodes'][0]['vendor-existing'])
        self.assertEqual(read['nodes'][0]['id'], 'own-node')
        self.assertEqual(read['nodes'][0]['vendor-file-actions'], {'retain': '不重写'})
        for change in (lambda v:v.update(nodes=[]), lambda v:v.update(**{'vendor-file-actions': {}}),
                       lambda v:v['nodes'][0].update(text='different')):
            bad = deepcopy(value); change(bad)
            with self.assertRaises(RuntimeError): changed_content('canvas', json.dumps(bad).encode())

    def preview(self):
        row = {'operation_id': OP, 'kind': 'rename', 'file_id': FILE, 'path': paths('md')[0],
               'destination': paths('md')[1], 'expected': hashlib.sha256(UPDATED).hexdigest(), 'state': 'prepared', 'error': None}
        data = {'mutations': [row], 'operations': [], 'outbox': [], 'files': [{'id': FILE, 'deleted': 0}]}
        log = 'observed\nORIGINAL_FILE_FREEZE ' + OP + ' ' + row['expected'] + '\n'
        return data, row, log

    def test_visible_review_is_bound_to_its_one_native_ID_path_and_original_content(self):
        data, row, log = self.preview()
        self.assertEqual(frozen(data, log, 'md', UPDATED, 'rename', paths('md')[1]), row)
        for field, value in (('file_id', 'other'), ('file_id', OP), ('expected', '0' * 64), ('destination', 'other.md'), ('state', 'committed')):
            bad = deepcopy(data); bad['mutations'][0][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError): frozen(bad, log, 'md', UPDATED, 'rename', paths('md')[1])
        with self.assertRaises(RuntimeError): frozen(data, log + log, 'md', UPDATED, 'rename', paths('md')[1])
        with self.assertRaises(RuntimeError): frozen(data, log, 'md', UPDATED, 'rename', paths('md')[1], expected_id=FILE)

    def test_a_rejected_review_must_have_no_workspace_execution_or_outbox(self):
        data, row, log = self.preview(); row.update(state='rejected', error='REVISION_CONFLICT')
        frozen(data, log, 'md', UPDATED, 'rename', paths('md')[1], state='rejected', expected_id=OP)
        for table in ('operations', 'outbox'):
            bad = deepcopy(data); bad[table] = [{'operation_id': OP}]
            with self.subTest(table=table), self.assertRaises(RuntimeError):
                frozen(bad, log, 'md', UPDATED, 'rename', paths('md')[1], state='rejected')

    def committed_fixture(self, deleted=False):
        _, row, _ = self.preview()
        if deleted: row.update(kind='delete', path=paths('md')[1], destination='')
        row['state'] = 'committed'; digest = hashlib.sha256(UPDATED).hexdigest()
        entry = {'id': FILE, 'path': paths('md')[1], 'hash': digest, 'revision': 3, 'deleted': int(deleted)}
        result = {'file_id': FILE, 'path': entry['path'], 'hash': digest, 'deleted': deleted}
        data = {'mutations': [row], 'files': [entry], 'operations': [{'operation_id': OP,
                'state': 'committed', 'fingerprint': 'a' * 64, 'result': json.dumps(result)}],
                'outbox': [{'operation_id': OP, 'file_id': FILE, 'path': entry['path'], 'hash': '' if deleted else digest,
                            'revision': 3, 'operation': 'delete' if deleted else 'put'}],
                'drafts': [] if deleted else [{'file_id': FILE, 'path': entry['path'], 'content': UPDATED.decode(),
                                              'generation': 2, 'saved_generation': 2}]}
        return data, deepcopy(row)

    def test_real_native_rename_uses_same_file_ID_and_put_outbox_not_a_rename_protocol(self):
        data, row = self.committed_fixture()
        value = committed(data, row, UPDATED, deleted=False)
        self.assertEqual(value['file_id'], FILE)
        bad = deepcopy(data); bad['outbox'][0]['operation'] = 'rename'
        with self.assertRaises(RuntimeError): committed(bad, row, UPDATED, deleted=False)

    def test_delete_keeps_saved_entry_hash_but_requires_empty_tombstone_wire_hash(self):
        data, row = self.committed_fixture(True)
        self.assertTrue(committed(data, row, UPDATED, deleted=True)['deleted'])
        for change in (lambda v:v['outbox'][0].update(hash='a' * 64),
                       lambda v:v.update(drafts=[{'file_id': FILE}]), lambda v:v['files'][0].update(deleted=0)):
            bad = deepcopy(data); change(bad)
            with self.assertRaises(RuntimeError): committed(bad, row, UPDATED, deleted=True)

    def test_duplicate_receipt_or_file_replacement_does_not_prove_exactly_once(self):
        data, row = self.committed_fixture()
        for change in (lambda v:v['operations'].append(deepcopy(v['operations'][0])),
                       lambda v:v['outbox'].append(deepcopy(v['outbox'][0])),
                       lambda v:v['files'][0].update(id=OP), lambda v:v['drafts'][0].update(content='different')):
            bad = deepcopy(data); change(bad)
            with self.assertRaises(RuntimeError): committed(bad, row, UPDATED, deleted=False)

    def test_genuine_unrelated_discovery_is_not_misrepresented_as_zero_global_writes(self):
        data, row = self.committed_fixture()
        data['outbox'].append({'operation_id': FILE, 'genuine_independent_discovery': True})
        data['operations'].append({'operation_id': FILE, 'genuine_independent_discovery': True})
        committed(data, row, UPDATED, deleted=False)

    def test_local_or_phone_environment_refuses_before_intent_or_device_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = SimpleNamespace(output=Path(directory), args=SimpleNamespace(serial='emulator-5562', scope='file-actions'))
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError): intent(probe, 'md', 'seed')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_original_UI_intent_is_exclusive_and_records_one_explicit_action(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = SimpleNamespace(output=Path(directory), args=SimpleNamespace(serial='emulator-5562', scope='file-actions',
                tools_commit='1' * 40, commit='2' * 40))
            with patch('android_file_actions_probe.require_hosted_emulator'), patch.dict(os.environ, GITHUB_RUN_ID='12345'):
                intent(probe, 'canvas', 'rename-apply', original_operation_id=OP)
                with self.assertRaises(FileExistsError): intent(probe, 'canvas', 'rename-apply', original_operation_id=OP)
            files = list(Path(directory).glob('*.json')); self.assertEqual(len(files), 1)
            value = json.loads(files[0].read_text('utf-8'))
            self.assertFalse(value['automatic_retry']); self.assertEqual(value['original_operation_id'], OP)


if __name__ == '__main__': unittest.main()
