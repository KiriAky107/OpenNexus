from pathlib import Path
import unittest
import yaml
from test_android_desktop_workflow import admitted

class OfflinePendingWorkflow(unittest.TestCase):
    def setUp(self):
        self.workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        self.job=self.workflow['jobs']['emulator-runtime']
    def test_batch_can_only_reuse_fixed_APK_and_excludes_all_desktop_jobs(self):
        for scope in ('offline-pending','offline-pending-final'):
            self.assertIn(scope,self.workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
            for reuse in ('','39078195102'):
                self.assertFalse(admitted(self.workflow['jobs']['debug-apk']['if'],scope,reuse=reuse))
            self.assertTrue(admitted(self.job['if'],scope,reuse='39078195102',build='skipped'))
            self.assertFalse(admitted(self.job['if'],scope,reuse='',build='skipped'))
        for key in ['desktop-startup','desktop-pair','android-desktop-pair']:
            if key in self.workflow['jobs']:
                self.assertIn("inputs.runtime_scope == 'desktop-",self.workflow['jobs'][key]['if'])
    def test_only_two_pending_scenes_run_separately_without_fail_fast(self):
        strategy=self.job['strategy'];self.assertEqual(strategy['fail-fast'],'false');self.assertEqual(strategy['max-parallel'],'2')
        scene=strategy['matrix']['scene']
        self.assertIn("inputs.runtime_scope == 'offline-pending'",scene)
        self.assertIn('["note-search","file-actions-remaining"]',scene)
        self.assertIn("inputs.runtime_scope == 'offline-pending-final'",scene)
        self.assertIn('["note-search","canvas-file-delete"]',scene)
        self.assertIn(chr(91)+chr(34)+chr(34)+chr(93),scene)
        self.assertEqual(self.job['env']['OWNED_RUNTIME_SCOPE'],"${{ matrix.scene || inputs.runtime_scope || 'all' }}")
    def test_scoped_checks_launcher_and_artifacts_use_effective_scene(self):
        steps=self.job['steps'];checks=[s for s in steps if s.get('if')=="env.OWNED_RUNTIME_SCOPE == 'note-search'"]
        self.assertTrue(any('test_android_note_search_probe.py' in s.get('run','') for s in checks))
        self.assertTrue(any(s.get('if')=="env.OWNED_RUNTIME_SCOPE == 'file-actions' || env.OWNED_RUNTIME_SCOPE == 'file-actions-remaining' || env.OWNED_RUNTIME_SCOPE == 'canvas-file-delete'" for s in steps))
        validators=[s for s in steps if s.get('name')=='Validate the immutable reuse selection'];self.assertEqual(validators[0]['env']['RUNTIME_SCOPE'],"${{ env.OWNED_RUNTIME_SCOPE }}")
        launcher=next(s for s in steps if 'scripts/android_runtime_probe.py --commit' in s.get('run',''))
        self.assertIn('--scope "${{ env.OWNED_RUNTIME_SCOPE }}"',launcher['run'])
        artifacts=[s.get('with',{}).get('name','') for s in steps if s.get('uses','').startswith('actions/upload-artifact')]
        self.assertTrue(any("'OpenNexus-android-runtime-{0}-{1}', matrix.scene, env.COMPILED_SHA" in name and "format('OpenNexus-android-runtime-{0}', env.COMPILED_SHA)" in name for name in artifacts))

if __name__=='__main__':unittest.main()
