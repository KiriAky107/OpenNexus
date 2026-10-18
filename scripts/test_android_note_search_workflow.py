from pathlib import Path
import unittest
import yaml
from test_android_desktop_workflow import admitted

class NoteSearchWorkflow(unittest.TestCase):
    def test_new_owned_search_requires_fixed_APK_and_never_debug_builds(self):
        workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        self.assertIn('note-search',workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for reuse in ('','39078195102'):
            self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'],'note-search',reuse=reuse))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'],'note-search',reuse='39078195102',build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'],'note-search',reuse='',build='skipped'))
        steps=workflow['jobs']['emulator-runtime']['steps']
        self.assertTrue(any(step.get('if')=="inputs.runtime_scope == 'note-search'" and 'test_android_note_search_probe.py' in step.get('run','') for step in steps))

if __name__=='__main__':unittest.main()
