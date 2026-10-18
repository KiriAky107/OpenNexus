from pathlib import Path
import unittest
import yaml
from test_android_note_search_workflow import admitted

class GraphWorkflow(unittest.TestCase):
    def test_only_explicit_fixed_APK_scene_admitted_without_rebuild(self):
        workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        self.assertIn('canvas-graph',workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for reuse in ('','39078195102'):
            self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'],'canvas-graph',reuse=reuse))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'],'canvas-graph',reuse='39078195102',build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'],'canvas-graph',reuse='',build='skipped'))
        self.assertTrue(any(s.get('if')=="env.OWNED_RUNTIME_SCOPE == 'canvas-graph'" and 'test_android_canvas_graph_contract.py' in s.get('run','')
            and 'test_android_canvas_graph_probe.py' in s['run'] for s in workflow['jobs']['emulator-runtime']['steps']))

if __name__=='__main__':unittest.main()
