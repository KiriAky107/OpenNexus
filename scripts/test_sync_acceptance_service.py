"""Check real-service binding and skipped-test rejection without starting services."""
from pathlib import Path
import contextlib
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/acceptance_cases'))
from sync_test_service import configure_service, run_exact, source_receipts


class ServiceBindingTests(unittest.TestCase):
    def setUp(self):
        build = ROOT / '.build'; build.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='sync-acceptance-helper-', dir=build)
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(build.resolve())
        self.service = self.root / 'split-sync'
        self.interpreter = self.service / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
        for path in (self.service / 'sync_server/app.py', self.service / 'tests/host_fixture.py', self.interpreter):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'actual configured fixture')
        self.env = patch.dict(os.environ, {}, clear=True); self.env.start()

    def tearDown(self):
        self.env.stop()
        assert self.root.is_relative_to((ROOT / '.build').resolve())
        self.temp.cleanup()

    def test_missing_configuration_never_discovers_an_old_mirror(self):
        mirror = self.root / 'server sync/tests/host_fixture.py'
        mirror.parent.mkdir(parents=True); mirror.write_bytes(b'old mirror')
        with self.assertRaisesRegex(RuntimeError, 'NOT_CONFIGURED'):
            configure_service()

    def test_configured_service_must_have_the_actual_fixture_and_interpreter(self):
        os.environ['OPENNEXUS_SYNC_SERVER_DIR'] = str(self.service)
        self.assertEqual(configure_service(), self.service)
        for path in (self.service / 'tests/host_fixture.py', self.interpreter):
            content = path.read_bytes(); path.unlink()
            with self.assertRaisesRegex(RuntimeError, 'INCOMPLETE'):
                configure_service()
            path.write_bytes(content)

    def test_receipt_hashes_the_configured_service_not_the_desktop_mirror(self):
        os.environ['OPENNEXUS_SYNC_SERVER_DIR'] = str(self.service)
        old = self.root / 'server sync/tests/host_fixture.py'
        old.parent.mkdir(parents=True); old.write_bytes(b'old mirror')
        receipts = source_receipts(self.root, ['sync-service/tests/host_fixture.py'])
        self.assertEqual(receipts, [{'path':'sync-service/tests/host_fixture.py',
            'source_root':str(self.service), 'sha256':hashlib.sha256(b'actual configured fixture').hexdigest()}])
        self.assertNotEqual(receipts[0]['sha256'], hashlib.sha256(old.read_bytes()).hexdigest())

    def test_relative_service_is_normalized_for_child_process_working_directory(self):
        os.environ['OPENNEXUS_SYNC_SERVER_DIR'] = 'split-sync'
        with patch('os.getcwd', return_value=str(self.root)):
            self.assertEqual(configure_service(), self.service)
        self.assertEqual(os.environ['OPENNEXUS_SYNC_SERVER_DIR'], str(self.service))

    def test_only_a_failed_receipt_can_omit_an_unavailable_service(self):
        with self.assertRaisesRegex(RuntimeError, 'NOT_CONFIGURED'):
            source_receipts(self.root, ['sync-service/tests/host_fixture.py'])
        self.assertEqual(source_receipts(self.root, ['sync-service/tests/host_fixture.py'], allow_missing=True), [])

    def test_exact_oracle_must_reject_skips_zero_cases_and_nonzero_exit(self):
        for code, stdout, stderr, expected in [
            (0, 'test result: ok. 1 passed; 0 failed;', '', True),
            (0, 'test result: ok. 1 passed; 0 failed;', 'skipped: missing service', False),
            (0, 'Skipped: missing dependency\ntest result: ok. 1 passed; 0 failed;', '', False),
            (0, 'test result: ok. 0 passed; 0 failed;', '', False),
            (1, 'test result: ok. 1 passed; 0 failed;', '', False),
        ]:
            with self.subTest(code=code, stdout=stdout, stderr=stderr), patch(
                'sync_test_service.subprocess.run', return_value=subprocess.CompletedProcess(['cargo'],code,stdout,stderr)
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_exact(['cargo'], cwd=ROOT), expected)

    def test_client_drivers_write_failure_without_launching_cargo_when_unconfigured(self):
        for case, name in [('S-01','s01_sync_client'),('S-02','s02_sync_client'),('S-03','s03_sync_client'),('S-08','s08_sync_client')]:
            module = importlib.import_module(name); output = self.root / (case + '.json')
            with self.subTest(case=case), patch.dict(os.environ, {'OPENNEXUS_ACCEPTANCE_CASE_ID':case}), patch(
                'sys.argv', [name, '--config', 'unused.json', '--output', str(output)]
            ), patch.object(module.shutil, 'which', return_value='cargo'), patch('sync_test_service.subprocess.run') as child:
                self.assertEqual(module.main(), 1); child.assert_not_called()
                result = json.loads(output.read_text('utf-8'))
                self.assertEqual(result['status'], 'FAILED')
                self.assertEqual(result['reason'], 'SYNC_ACCEPTANCE_SERVICE_NOT_CONFIGURED')
                self.assertFalse(any(row['path'].startswith('sync-service/') for row in result['files']))


if __name__ == '__main__':
    unittest.main()
