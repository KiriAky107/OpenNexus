import os,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import yaml
from android_workflow_gate import admitted
from android_runtime_probe import Probe
from android_canvas_view_probe import PATH,STEPS,REMAINING_STEPS,FIXTURE,graph,run_canvas_view
from android_canvas_view_geometry import MODEL

class ViewWorkflow(unittest.TestCase):
    def test_remaining_only_fixed_package_scope_does_not_build_or_launch_desktop(self):
        workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        self.assertIn('canvas-view',workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for scope in ('canvas-view','canvas-view-remaining'):
            self.assertIn(scope,workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
            for reuse in ('','39112675356'):
                self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'],scope,reuse=reuse))
                self.assertEqual(admitted(workflow['jobs']['emulator-runtime']['if'],scope,reuse=reuse,build='skipped'),bool(reuse))
                for name in ('desktop-startup','desktop-pair','android-desktop-pair'):
                    self.assertFalse(admitted(workflow['jobs'][name]['if'],scope,reuse=reuse))
        check=next(s for s in workflow['jobs']['emulator-runtime']['steps'] if s.get('name')=='Check remaining Canvas view contracts without executing input')
        self.assertTrue(admitted(check['if'],'canvas-view'));self.assertTrue(admitted(check['if'],'canvas-view-remaining'))
        self.assertFalse(admitted(check['if'],'sync-revocation'))
        self.assertIn('PyYAML==6.0.3',check['run']);self.assertIn('canvas_view_expression.test.mjs',check['run'])

    def test_new_scene_starts_at_accepted_geometry_without_move_resize_or_other_graph_Saves(self):
        self.assertEqual(len(STEPS),9);self.assertEqual(STEPS[:2],('view-seed','view-open'))
        self.assertNotIn('view-move',STEPS);self.assertNotIn('view-resize',STEPS)
        self.assertNotEqual(PATH,'motion-original.canvas');self.assertIn(b'\r\n',FIXTURE)
        for node in graph(FIXTURE)['nodes']:
            self.assertEqual(tuple(node[key] for key in ('x','y','width','height')),MODEL[node['id']])
            self.assertIn('vendor',node)
        self.assertIn('vendor-root',graph(FIXTURE));self.assertIn('vendor',graph(FIXTURE)['edges'][0])
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='canvas-view');probe.boot=Mock()
        with patch('android_canvas_view_probe.run_canvas_view') as run:probe.run()
        run.assert_called_once_with(probe);probe.native_keystore_tests()

    def test_local_or_physical_device_does_not_acquire_owned_files_or_run_input(self):
        with patch.dict(os.environ,{},clear=True):
            probe=SimpleNamespace(args=SimpleNamespace(scope='canvas-view',serial='user-phone'))
            with self.assertRaises(RuntimeError):run_canvas_view(probe)

    def test_remaining_scene_contains_no_accepted_four_view_gestures(self):
        self.assertEqual(REMAINING_STEPS,('view-seed','view-open','view-background','view-cold','view-inspector'))
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='canvas-view-remaining');probe.boot=Mock()
        with patch('android_canvas_view_probe.run_canvas_view') as run,patch.object(probe,'phase',side_effect=AssertionError('No other runtime scene allowed')):
            probe.run()
        run.assert_called_once_with(probe);probe.native_keystore_tests()

if __name__=='__main__':unittest.main()
