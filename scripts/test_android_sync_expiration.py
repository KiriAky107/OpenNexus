"""Clock/orchestration contracts only; no session, device, Docker or network."""
from pathlib import Path
from unittest.mock import patch
import ast,json,os,tempfile,unittest,uuid,yaml
import android_sync_expiration as subject
from android_workflow_gate import admitted

class ExpiryContract(unittest.TestCase):
    def root(self,directory):
        root=Path(directory).resolve()
        scope=patch.object(subject.auth,'owned_root',return_value=root);scope.start();self.addCleanup(scope.stop)
        subject.OwnedClock.initialize(root);return root

    def test_real_time_continues_at_zero_offset_and_only_declared_forward_offsets_are_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            clock=subject.OwnedClock(self.root(directory),now=lambda:1000)
            self.assertEqual(clock(),1000)
            first=clock.advance('access_expired',str(uuid.uuid4()),0)
            self.assertEqual(clock(),1901);self.assertEqual(first['offset_seconds'],901)
            second=clock.advance('refresh_expired',str(uuid.uuid4()),1)
            self.assertEqual(clock(),1000+31*86400);self.assertFalse(second['actual_elapsed_time_claimed'])
            for mode,revision in [('normal',2),('access_expired',2),('refresh_expired',0),('other',0)]:
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):clock.advance(mode,str(uuid.uuid4()),revision)

    def test_same_original_receipt_is_read_back_without_another_clock_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.root(directory);clock=subject.OwnedClock(root);operation=str(uuid.uuid4())
            first=clock.advance('access_expired',operation,0);raw=(clock.root/'control.json').read_bytes()
            self.assertEqual(clock.advance('access_expired',operation,0),first)
            self.assertEqual((clock.root/'control.json').read_bytes(),raw)
            with self.assertRaises(RuntimeError):clock.advance('access_expired',str(uuid.uuid4()),0)
            with self.assertRaises(RuntimeError):subject.OwnedClock.initialize(root)

    def test_interrupted_original_clock_commit_recovers_receipt_from_unchanged_original_id(self):
        with tempfile.TemporaryDirectory() as directory:
            clock=subject.OwnedClock(self.root(directory));operation=str(uuid.uuid4())
            original=subject.auth.atomic_json
            def write(path,value,**kwargs):
                if path.parent.name=='control-receipts':raise OSError('unit interrupted receipt')
                return original(path,value,**kwargs)
            with patch.object(subject.auth,'atomic_json',side_effect=write),self.assertRaises(OSError):
                clock.advance('access_expired',operation,0)
            state=clock.state();self.assertEqual(state['operation_id'],operation)
            self.assertEqual(clock.advance('access_expired',operation,0)['operation_id'],operation)
            self.assertEqual(clock.state(),state)

    def test_bad_control_or_foreign_owner_is_rejected_without_clock_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.root(directory);clock=subject.OwnedClock(root)
            for value in [dict(revision=1,operation_id=None,mode='access_expired'),
                    dict(revision=99,operation_id=str(uuid.uuid4()),mode='normal'),
                    dict(revision=0,operation_id=None,mode='normal',extra=True)]:
                subject.atomic_json(clock.root/'control.json',value)
                with self.subTest(value=value),self.assertRaises(RuntimeError):clock()
            with self.assertRaises(RuntimeError):subject.OwnedClock(root/'different')

    def test_expiration_workflow_reuses_apk_and_only_runs_its_owned_service(self):
        source=Path(__file__).resolve().parents[1]
        workflow=yaml.load((source/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for reuse in ('','39112675356'):
            for name in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(workflow['jobs'][name]['if'],'sync-expiration',reuse=reuse,build='skipped'))
            self.assertEqual(admitted(workflow['jobs']['emulator-runtime']['if'],'sync-expiration',reuse=reuse,build='skipped'),bool(reuse))
        steps=workflow['jobs']['emulator-runtime']['steps']
        preflight=next(s for s in steps if s.get('name')=='Validate the immutable reuse selection')
        code=preflight['run'].split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        base=dict(REUSE_RUN='39112675356',REUSE_COMMIT='20af3d2cad9da2350f0e775f0e5605630f834443',
            RUNTIME_SCOPE='sync-expiration',WEBVIEW_PREVIEW='true',WEBVIEW_CYCLE='true')
        with patch.dict(os.environ,base,clear=True):exec(compile(code,'expiry-workflow','exec'),{})
        with patch.dict(os.environ,dict(base,REVOCATION_REFRESH_ONLY='true'),clear=True),self.assertRaises(AssertionError):
            exec(compile(code,'expiry-workflow','exec'),{})
        prepare=next(s for s in steps if 'scripts/android_auth_hosted.py prepare --fixture' in s.get('run',''))
        self.assertIn("'sync-expiration' && 'sync-expiration' || 'sync'",prepare['run'])
        run=next(s for s in steps if 'scripts/android_sync_runtime.py' in s.get('run',''))
        self.assertIn("'sync-expiration' && '--expiration'",run['run'])
        self.assertTrue(admitted(run['if'],'sync-expiration'))
        generic=next(s for s in steps if 'scripts/android_runtime_probe.py --commit' in s.get('run',''))
        self.assertFalse(admitted(generic['if'],'sync-expiration'))

    def test_only_explicit_expiry_branch_runs_before_an_upload_and_keeps_original_ui_backend(self):
        source=Path(__file__).resolve().parents[1]
        runtime=(source/'scripts/android_sync_runtime.py').read_text('utf-8')
        branch=runtime.split("if getattr(self.args,'expiration',False):",1)[1].split("if getattr(self.args,'revocation',False):",1)[0]
        self.assertIn('run_expiration(self,setup,prepared,device)',branch);self.assertIn('return',branch)
        fixture=Path(subject.__file__).read_text('utf-8');ast.parse(fixture)
        self.assertIn('clock=clock',fixture);self.assertNotIn('UPDATE sessions',fixture)
        self.assertNotIn('OriginalRevocation(',fixture);self.assertNotIn('verify=False',fixture)
        self.assertEqual(fixture.count("probe.sync_phase('cycle-revoked')"),1)
        self.assertEqual(fixture.count("probe.sync_phase('refresh-revoked')"),1)

if __name__=='__main__':unittest.main()
