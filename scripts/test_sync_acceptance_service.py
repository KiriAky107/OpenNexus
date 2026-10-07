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

    def test_production_stack_checks_service_before_initializing_dependencies(self):
        from sync_production_stack import SyncProductionStack
        with self.assertRaisesRegex(RuntimeError, 'NOT_CONFIGURED'):
            SyncProductionStack({}, self.root, 'missing-service')

    def test_production_drivers_refuse_missing_service_without_starting_processes(self):
        cases=[('S-04','s04_sync_service'),('S-05','s05_sync_uploads'),('S-06','s06_sync_security'),
            ('S-07','s07_sync_backup'),('S-09','s09_sync_performance')]
        for case,name in cases:
            module=importlib.import_module(name);output=self.root/(case+'.json')
            with self.subTest(case=case), patch.dict(os.environ,{'OPENNEXUS_ACCEPTANCE_CASE_ID':case}), patch(
                'sys.argv',[name,'--config','unused.json','--output',str(output)]
            ), patch('subprocess.Popen') as child, patch('subprocess.run') as command, (
                patch.object(module.platform,'platform',return_value='test-platform') if case=='S-09' else contextlib.nullcontext()
            ):
                self.assertEqual(module.main(),1);child.assert_not_called();command.assert_not_called()
                result=json.loads(output.read_text('utf-8'))
                self.assertEqual(result['status'],'FAILED')
                self.assertEqual(result['reason'],'SYNC_ACCEPTANCE_SERVICE_NOT_CONFIGURED')

    def test_repository_evidence_uses_the_two_configured_service_locks(self):
        import phase3_acceptance as runner
        (self.service/'uv.lock').write_bytes(b'configured-sync-lock')
        community=self.root/'split-community';community.mkdir()
        (community/'uv.lock').write_bytes(b'configured-community-lock')
        mirror=self.root/'server sync/uv.lock';mirror.parent.mkdir();mirror.write_bytes(b'stale-lock')
        with patch.dict(os.environ,{'OPENNEXUS_SYNC_SERVER_DIR':str(self.service),
            'OPENNEXUS_COMMUNITY_SERVER_DIR':str(community)}), patch.object(runner,'ROOT',self.root), patch(
                'phase3_acceptance.subprocess.run',return_value=subprocess.CompletedProcess(['git'],0,'fixed-commit','')
            ):
            evidence=runner._repository_evidence({})
        self.assertEqual(evidence['lock_sha256'],{
            'sync-service/uv.lock':hashlib.sha256(b'configured-sync-lock').hexdigest(),
            'community-service/uv.lock':hashlib.sha256(b'configured-community-lock').hexdigest()})
        self.assertEqual(evidence['service_roots'],{'sync-service':str(self.service),'community-service':str(community)})

    def test_configured_lock_cannot_silently_disappear_from_the_snapshot(self):
        import phase3_acceptance as runner
        os.environ['OPENNEXUS_SYNC_SERVER_DIR']=str(self.service)
        with self.assertRaisesRegex(runner.AcceptanceError,'SERVICE_LOCKFILE_UNAVAILABLE'):
            runner._repository_evidence({})

    def test_rename_crash_failure_cannot_be_hidden_by_successful_pull_and_upload(self):
        module=importlib.import_module('s02_sync_client');output=self.root/'S02-rename-failed.json'
        with patch.dict(os.environ,{'OPENNEXUS_SYNC_SERVER_DIR':str(self.service),'OPENNEXUS_ACCEPTANCE_CASE_ID':'S-02'}), patch(
            'sys.argv',['s02','--config','unused.json','--output',str(output)]
        ), patch.object(module.shutil,'which',return_value='cargo'), patch.object(module,'run_exact',side_effect=[True,False,True]) as oracle:
            self.assertEqual(module.main(),1)
            self.assertEqual(oracle.call_count,3)
            self.assertIn(module.RENAME_TEST,oracle.call_args_list[1].args[0])
            self.assertEqual(json.loads(output.read_text('utf-8'))['status'],'FAILED')


if __name__ == '__main__':
    unittest.main()
