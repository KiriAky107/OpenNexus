import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch, Mock
import yaml
from android_document_limits_probe import CASES, MIB, NODE, SEED, SEED_PATH, fixture, exported_originals, readonly_result, run_document_limits
from android_runtime_probe import Probe
from test_android_desktop_workflow import admitted


class NativeDocumentLimits(unittest.TestCase):
    def test_actual_boundary_data_is_valid_UTF8_JSON_with_original_extensions_and_duplicate_CRLF_headings(self):
        for _, path, size, allowed in CASES:
            body = fixture(path, size)
            self.assertEqual(len(body), size)
            self.assertLess(len(body.decode()), size)
            if path.endswith('.canvas'):
                value = json.loads(body)
                self.assertEqual(value['nodes'][0]['text'], NODE)
                self.assertEqual(value['nodes'][0]['vendor'], {'keep': ['甲', True, None]})
                self.assertEqual(value['vendor-root']['original'], ['扩展', 17, False])
                self.assertEqual(value['edges'], [])
                self.assertTrue(body.endswith(b'\r\n'))
                self.assertEqual(allowed, size <= 4 * MIB)
            else:
                self.assertEqual(body.count('# 相同标题 😀\r\n'.encode()), 2)
                self.assertEqual(allowed, size <= MIB)
        for path, size in [('other.md', MIB), (CASES[0][1], 1), ('../limits-exact.md', MIB)]:
            with self.assertRaises(RuntimeError):fixture(path, size)

    def setup_rows(self):
        originals = {SEED_PATH: SEED, **{p: b'owned original bytes' for _, p, _, _ in CASES}}
        files = [dict(id=f'12345678-1234-1234-1234-{i:012x}', path=p,
            hash=hashlib.sha256(b).hexdigest(), revision=1, deleted=0) for i, (p, b) in enumerate(originals.items(), 1)]
        before = dict(files=files, saves=[{'operation_id': 'original', 'state': 'committed'}],
            outbox=[{'operation_id': 'original', 'state': 'pending'}],
            operations=[{'operation_id': 'original', 'fingerprint': 'a' * 64}], journal=[],
            drafts=[{'path': SEED_PATH, 'file_id': files[0]['id'], 'generation': 1, 'saved_generation': 1}])
        return originals, before

    def accepted(self, before, originals, paths):
        after = copy.deepcopy(before)
        for path in paths:
            row = next(f for f in before['files'] if f['path'] == path)
            after['drafts'].append(dict(path=path, file_id=row['id'], base_hash=row['hash'],
                bytes=len(originals[path]), sha256=row['hash'], generation=0, saved_generation=0))
        return after

    def test_readonly_accepted_draft_has_exact_native_id_bytes_base_and_no_new_ledger(self):
        originals, before = self.setup_rows()
        opened = {p for _, p, _, allowed in CASES if allowed}
        after = self.accepted(before, originals, opened)
        readonly_result(before, after, originals, opened)
        for table in ('files', 'saves', 'outbox', 'operations', 'journal'):
            changed = copy.deepcopy(after)
            changed[table].append({'foreign': True})
            with self.subTest(table=table), self.assertRaises(RuntimeError):readonly_result(before, changed, originals, opened)

    def test_rejected_oversize_draft_or_replaced_original_cannot_pass(self):
        originals, before = self.setup_rows()
        opened = {CASES[0][1]}
        after = self.accepted(before, originals, opened)
        for field, value in [('file_id', 'foreign'), ('base_hash', 'b' * 64), ('sha256', 'b' * 64),
                ('generation', 1), ('bytes', 999)]:
            changed = copy.deepcopy(after);changed['drafts'][-1][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):readonly_result(before, changed, originals, opened)
        changed = self.accepted(after, originals, {CASES[1][1]})
        with self.assertRaises(RuntimeError):readonly_result(before, changed, originals, opened)
        changed = copy.deepcopy(after);changed['drafts'][0]['generation'] = 2
        with self.assertRaises(RuntimeError):readonly_result(before, changed, originals, opened)

    def test_own_hosted_scope_required_before_files_or_GUI_are_touched(self):
        with tempfile.TemporaryDirectory() as work, patch.dict(os.environ, {}, clear=True):
            probe = SimpleNamespace(args=SimpleNamespace(scope='document-limits', serial='user-phone'), output=Path(work))
            with self.assertRaises(RuntimeError):run_document_limits(probe)
            self.assertFalse(list(Path(work).iterdir()))
        probe = Probe.__new__(Probe);probe.args=SimpleNamespace(scope='document-limits');probe.boot=Mock()
        with patch('android_document_limits_probe.run_document_limits') as run:probe.run()
        probe.boot.assert_called_once_with();run.assert_called_once_with(probe)

    def test_scope_reuses_one_fixed_APK_without_other_client_builds(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows/android.yml').read_text('utf-8'), Loader=yaml.BaseLoader)
        self.assertIn('document-limits', workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for reuse in ('', '39112675356'):
            self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'], 'document-limits', reuse=reuse))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'], 'document-limits', reuse='39112675356', build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'], 'document-limits', reuse='', build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['desktop-startup']['if'], 'document-limits'))

    def test_content_export_requires_exact_original_bytes_manifest_and_no_additional_private_file(self):
        originals={'original.md':b'# original\r\n','original.canvas':b'{"nodes":[],"edges":[],"vendor":true}\r\n'}
        manifest={'format':'opennexus-content-export','schema':1,'scope':'markdown-canvas-images',
            'files':[{'path':'vault/'+path,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for path,raw in originals.items()]}
        def bundle(extra=False, changed=False, wrong_manifest=False):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as archive:
                for path,raw in originals.items():archive.writestr('vault/'+path,raw+(b'changed' if changed else b''))
                value=copy.deepcopy(manifest)
                if wrong_manifest:value['files'][0]['size']+=1
                archive.writestr('OpenNexus-export.json',json.dumps(value))
                if extra:archive.writestr('vault/.ainote/credentials.key',b'not real credentials; forbidden fixture')
            return out.getvalue()
        exported_originals(bundle(),originals)
        for args in ({'extra':True},{'changed':True},{'wrong_manifest':True}):
            with self.subTest(args=args),self.assertRaises(RuntimeError):exported_originals(bundle(**args),originals)


if __name__ == '__main__':unittest.main()
