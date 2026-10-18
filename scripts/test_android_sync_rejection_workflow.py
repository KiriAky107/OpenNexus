from pathlib import Path
import ast,unittest,yaml
from test_android_desktop_workflow import admitted

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

if __name__=='__main__':unittest.main()
