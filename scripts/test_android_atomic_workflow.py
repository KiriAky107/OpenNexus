from pathlib import Path
import unittest
import yaml
from test_android_note_search_workflow import admitted

class AtomicWorkflow(unittest.TestCase):
    def test_only_explicit_fixed_APK_scope_runs_without_product_rebuild(self):
        w=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        self.assertIn('write-interruption',w['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for reuse in ('','39078195102'):self.assertFalse(admitted(w['jobs']['debug-apk']['if'],'write-interruption',reuse=reuse))
        self.assertTrue(admitted(w['jobs']['emulator-runtime']['if'],'write-interruption',reuse='39078195102',build='skipped'))
        self.assertFalse(admitted(w['jobs']['emulator-runtime']['if'],'write-interruption',reuse='',build='skipped'))
    def test_C_compiler_and_boundary_checks_are_confined_to_this_scope(self):
        w=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        scoped=[s for s in w['jobs']['emulator-runtime']['steps'] if s.get('if')=="env.OWNED_RUNTIME_SCOPE == 'write-interruption'"]
        self.assertTrue(any('test_android_atomic_probe.py' in s.get('run','') for s in scoped))
        compiler=next(s for s in scoped if 'x86_64-linux-android24-clang' in s.get('run',''))
        self.assertIn('30.0.16248370',compiler['run'])
        self.assertNotIn('setenforce',compiler['run']);self.assertNotIn('adb root',compiler['run'])

if __name__=='__main__':unittest.main()
