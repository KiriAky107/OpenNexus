"""Original property scope contracts, not actual Native input acceptance."""
from pathlib import Path
from unittest.mock import Mock
import json,unittest,yaml
from android_workflow_gate import admitted
from android_canvas_graph_probe import snapshot
from android_canvas_properties import PATH,STEPS,TRANSACTIONS,driver_form

class PropertyProbeBoundaries(unittest.TestCase):
    def test_foreign_path_or_phase_is_rejected_before_private_reads(self):
        probe=Mock();probe.args.scope='canvas-properties'
        for path,phase in [('other.canvas','properties-text'),(PATH,'graph-open'),(PATH,'properties-text/../../other')]:
            with self.subTest(path=path,phase=phase),self.assertRaises(RuntimeError):snapshot(probe,phase,path=path)
        probe.database_rows.assert_not_called();probe.private.assert_not_called()

    def test_old_graph_scope_cannot_borrow_the_property_path(self):
        probe=Mock();probe.args.scope='canvas-graph'
        with self.assertRaises(RuntimeError):snapshot(probe,'graph-open',path=PATH)
        probe.database_rows.assert_not_called();probe.private.assert_not_called()

    def test_one_immutable_bounded_form_document_contains_exactly_the_five_original_phases(self):
        forms={v['phase']:driver_form(v['phase']) for v in TRANSACTIONS}
        self.assertEqual(tuple(forms),STEPS[2:-1]);self.assertEqual(sum(len(v['fields']) for v in forms.values()),14)
        self.assertLessEqual(len(json.dumps(forms,ensure_ascii=False).encode()),8192)
        for phase,form in forms.items():self.assertEqual(phase,form['phase'])

    def test_only_the_reused_original_runtime_runs_without_new_apk_or_other_clients(self):
        root=Path(__file__).resolve().parents[1];flow=yaml.load((root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for reuse in ('','39148381613'):
            for job in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(flow['jobs'][job]['if'],'canvas-properties',reuse=reuse,build='skipped'))
        self.assertTrue(admitted(flow['jobs']['emulator-runtime']['if'],'canvas-properties',reuse='39148381613',build='skipped'))

    def test_native_input_reads_the_original_form_and_never_assigns_a_model_or_save_id(self):
        root=Path(__file__).resolve().parents[1];java=(root/'scripts/android-runtime/RuntimeProbe.java').read_text('utf-8')
        method=java.split('private void remainingCanvasProperties(String phase)',1)[1].split('private void canvasStorageRecovery',1)[0]
        self.assertIn('.getJSONObject(phase)',method);self.assertIn('form.length()<=8192',method)
        self.assertEqual(method.count('setText(control,after)'),1);self.assertEqual(method.count('graphClosePanel();save();'),1)
        self.assertIn('propertyValue(control,name,before)',method);self.assertIn('android.widget.CheckedTextView',method)
        for forbidden in ('evaluateJavascript','Runtime.evaluate','operation_id','model.source','dispatchEvent'):
            self.assertNotIn(forbidden,method)

if __name__=='__main__':unittest.main()
