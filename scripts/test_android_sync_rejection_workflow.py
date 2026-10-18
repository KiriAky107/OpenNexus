from pathlib import Path
from unittest.mock import patch
import ast,os,unittest,yaml
from android_workflow_gate import admitted

class RevocationWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.workflow=yaml.load((cls.root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)

    def test_named_original_scope_never_builds_or_launches_desktop(self):
        self.assertIn('sync-revocation',self.workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        jobs=self.workflow['jobs']
        for reuse in ('','39112675356'):
            self.assertFalse(admitted(jobs['debug-apk']['if'],'sync-revocation',reuse=reuse))
            # The explicit scope skips debug-apk, so its actual dependency is
            # skipped even when a caller omits the required reused package.
            self.assertEqual(admitted(jobs['emulator-runtime']['if'],'sync-revocation',reuse=reuse,build='skipped'),bool(reuse))
            for name in ('desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(jobs[name]['if'],'sync-revocation',reuse=reuse))

    def test_real_fixed_provider_and_private_sync_artifact_are_the_only_case_routes(self):
        steps=self.workflow['jobs']['emulator-runtime']['steps']
        checkout=next(step for step in steps if step.get('with',{}).get('repository')=='KiriAky107/Sync-for-OpenNexus')
        self.assertEqual(checkout['with']['ref'],'29e2e6d373ea752352981bdad05d34a7a0d9c3d9')
        self.assertTrue(admitted(checkout['if'],'sync-revocation'))
        prepare=next(step for step in steps if 'scripts/android_auth_hosted.py prepare --fixture sync' in step.get('run',''))
        self.assertTrue(admitted(prepare['if'],'sync-revocation'))
        # This step uses setup-python's python3, not the service .venv. Its
        # parser dependency must be present before workflow tests import yaml.
        dependency='python3 -m pip install --disable-pip-version-check PyYAML==6.0.3'
        self.assertIn(dependency,prepare['run'])
        self.assertLess(prepare['run'].index(dependency),prepare['run'].index('python3 -m unittest'))
        run=next(step for step in steps if 'scripts/android_sync_runtime.py' in step.get('run',''))
        self.assertTrue(admitted(run['if'],'sync-revocation'));self.assertIn("'--revocation'",run['run'])
        generic=next(step for step in steps if 'scripts/android_runtime_probe.py --commit' in step.get('run',''))
        self.assertFalse(admitted(generic['if'],'sync-revocation'))
        artifact=next(step for step in steps if step.get('with',{}).get('name')=='OpenNexus-android-${{ inputs.runtime_scope }}-${{ env.COMPILED_SHA }}')
        self.assertTrue(admitted(artifact['if'],'sync-revocation'))
        self.assertNotIn('owner',artifact['with']['path']);self.assertNotIn('logcat',artifact['with']['path'])

    def test_new_original_case_branches_after_necessary_setup_before_old_successful_transfer(self):
        source=(self.root/'scripts/android_sync_runtime.py').read_text('utf-8');ast.parse(source)
        start=source.index("if getattr(self.args,'revocation',False):")
        end=source.index("self.sync_phase('cycle');native=self.host_snapshot('first-cycle')")
        branch=source[start:end]
        self.assertIn('run_revocation(self,setup,prepared,device)',branch);self.assertIn('return',branch)
        self.assertIn("scope.add_argument('--revocation',action='store_true')",source)
        self.assertIn('include_payloads=False',source)

    def test_explicit_webview_preview_admission_is_checked_before_download(self):
        steps=self.workflow['jobs']['emulator-runtime']['steps']
        step=next(s for s in steps if s.get('name')=='Validate the immutable reuse selection')
        self.assertEqual(step['env']['WEBVIEW_PREVIEW'],'${{ inputs.sync_webview_preview }}')
        self.assertEqual(step['env'].get('SEMANTIC_PREVIEW'),'${{ inputs.sync_semantic_preview }}')
        code=step['run'].split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        base={'REUSE_RUN':'39112675356','REUSE_COMMIT':'20af3d2cad9da2350f0e775f0e5605630f834443',
              'RUNTIME_SCOPE':'sync-revocation','WEBVIEW_PREVIEW':'true'}
        for changes,allowed in (({},True),({'RUNTIME_SCOPE':'sync-conflicts'},True),
                ({'WEBVIEW_PREVIEW':'false'},True),({'RUNTIME_SCOPE':'sync-initial'},False),
                ({'SEMANTIC_PREVIEW':'true'},False),({'WEBVIEW_CYCLE':'true'},False)):
            with self.subTest(changes=changes),patch.dict(os.environ,dict(base,**changes),clear=True):
                if allowed:exec(compile(code,'workflow-preview-admission','exec'),{})
                else:
                    with self.assertRaises(AssertionError):exec(compile(code,'workflow-preview-admission','exec'),{})

if __name__=='__main__':unittest.main()
