"""Candidate admission and original-state comparators, not actual Android UI evidence."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hashlib,json,tempfile,unittest
import android_candidate_readiness_probe as candidate
from android_runtime_probe import Probe

class CandidateReadinessBoundaries(unittest.TestCase):
    def test_lifecycle_waits_for_original_image_receipt_and_checks_after_the_single_save(self):
        with tempfile.TemporaryDirectory() as root:
            probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='lifecycle');probe.output=Path(root)
            probe.root='owned/';probe.activity='owned/Activity';probe.phases=[]
            probe.force_stop=Mock();probe.native_tls_startup=Mock();probe.capture=Mock()
            probe.read_native_styles=Mock();probe.capture_failed_editor_state=Mock()
            result=SimpleNamespace(stdout=b'OPENNEXUS_RUNTIME_PHASE_OK rich-image\n')
            probe.shell=Mock(return_value=result);probe.image_save_instrumentation=Mock(return_value=result)
            probe.image_save_rows=Mock(return_value={'after':'one original Save'})
            (probe.output/'rich-image-stable-before-save-receipts.json').write_text('{"before":"original import"}','utf-8')
            with patch('android_runtime_probe.validate_image_save_result') as validate:
                probe.phase('rich-image')
                probe.image_save_instrumentation.assert_called_once_with('rich-image')
                probe.image_save_rows.assert_called_once_with('rich-image','after-save')
                validate.assert_called_once_with({'before':'original import'},{'after':'one original Save'})
            self.assertFalse(any('instrument' in call.args for call in probe.shell.call_args_list))

    def probe(self,root,*,change=None):
        probe=Mock();probe.args=SimpleNamespace(scope='lifecycle',serial='emulator-5562',tools_commit='tools',commit='compiled')
        probe.output=Path(root);probe.binding={'sha256':'fixed APK'}
        probe.keystore_test_result={'tests':5,'Rust_worker_verified':True}
        (probe.output/'window-insets-device-proof.json').write_text(json.dumps({'passed':True,'tests':3,'actual_keyboard_visibility_verified':False}),'utf-8')
        original=json.dumps({'nodes':[{'id':'own-node','text':candidate.NODE}],'edges':[]},ensure_ascii=False).encode()
        probe.original.return_value=original
        probe.shell.side_effect=[SimpleNamespace(stdout=str(i).encode()) for i in [101,201,201,201,202]]
        def phase(name,**kwargs):
            if change=='bytes' and name=='candidate-language-zh':probe.original.return_value=original+b' '
            marker='CANDIDATE_LOCALE_READ '+name+' '
            log=''.join(marker+str(i)+' content_input=0\n' for i in range(7))
            if change=='extra-control' and name=='candidate-language-zh':log+=marker+'unreviewed\n'
            log+='CANDIDATE_LOCALE_OK '+name+' content_input=0 save=0\n'
            (probe.output/(name+'.log')).write_text(log,'utf-8')
        probe.phase.side_effect=phase
        return probe

    def state(self,probe,name):
        raw=json.dumps({'nodes':[{'id':'own-node','text':candidate.NODE}],'edges':[]},ensure_ascii=False).encode()
        return {'files':[{'id':'own-file','hash':hashlib.sha256(raw).hexdigest()}],'drafts':[],'saves':[],'outbox':[],'operations':[]}

    @patch.object(candidate,'require_hosted_emulator')
    def test_stale_native_hash_cannot_verify_the_read_original(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root);state=self.state(probe,'candidate-before');state['files'][0]['hash']='stale'
            with patch.object(candidate,'snapshot',return_value=state),self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            probe.phase.assert_not_called()

    def test_foreign_snapshot_label_or_scope_is_refused_before_private_reads(self):
        probe=Mock();probe.args.scope='lifecycle'
        with self.assertRaises(RuntimeError):candidate.snapshot(probe,'graph-open')
        probe.args.scope='canvas-properties'
        with self.assertRaises(RuntimeError):candidate.snapshot(probe,'candidate-before')
        probe.private.assert_not_called();probe.database_rows.assert_not_called()

    @patch.object(candidate,'require_hosted_emulator')
    def test_requires_actual_five_native_tests_before_any_candidate_ui(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root);probe.keystore_test_result['tests']=4
            with self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            probe.phase.assert_not_called();probe.original.assert_not_called()

    @patch.object(candidate,'require_hosted_emulator')
    def test_does_not_turn_synthetic_insets_into_actual_keyboard_evidence(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root)
            (probe.output/'window-insets-device-proof.json').write_text(json.dumps({'passed':True,'tests':3,'actual_keyboard_visibility_verified':True}),'utf-8')
            with self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            probe.phase.assert_not_called()

    @patch.object(candidate,'require_hosted_emulator')
    def test_exact_readonly_order_and_new_cold_pid(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root)
            with patch.object(candidate,'snapshot',side_effect=self.state):candidate.verify_candidate(probe)
            proof=json.loads((probe.output/'candidate-readiness-proof.json').read_bytes())
            self.assertTrue(proof['passed']);self.assertEqual(proof['cold_PIDs'],[201,202])
            self.assertEqual(proof['executed_steps'],list(candidate.STEPS))
            self.assertEqual([x.args[0] for x in probe.phase.call_args_list],list(candidate.STEPS))
            self.assertEqual([x.kwargs['continue_process'] for x in probe.phase.call_args_list],[False,True,True,False])
            self.assertEqual(proof['Save_actions'],0);self.assertFalse(proof['physical_phone_verified'])

    @patch.object(candidate,'require_hosted_emulator')
    def test_changed_original_bytes_stop_before_later_language_actions(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root,change='bytes')
            with patch.object(candidate,'snapshot',side_effect=self.state),self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            self.assertEqual([x.args[0] for x in probe.phase.call_args_list],list(candidate.STEPS[:2]))
            proof=json.loads((probe.output/'candidate-readiness-proof.json').read_bytes())
            self.assertFalse(proof['passed']);self.assertTrue(proof['failure_original_observed'])

    @patch.object(candidate,'require_hosted_emulator')
    def test_changed_native_receipt_or_draft_is_refused_even_with_equal_raw_bytes(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root)
            def changed(p,name):
                value=self.state(p,name)
                if name=='candidate-language-zh':value['saves']=[{'operation_id':'unexpected'}]
                return value
            with patch.object(candidate,'snapshot',side_effect=changed),self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            self.assertEqual(probe.phase.call_count,2)

    @patch.object(candidate,'require_hosted_emulator')
    def test_extra_native_read_marker_is_not_accepted_as_the_seven_reviewed_controls(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root,change='extra-control')
            with patch.object(candidate,'snapshot',side_effect=self.state),self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            self.assertEqual(probe.phase.call_count,2)

    @patch.object(candidate,'require_hosted_emulator')
    def test_reusing_the_same_pid_does_not_count_as_cold_acceptance(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root);probe.shell.side_effect=[SimpleNamespace(stdout=b'101') for _ in range(5)]
            with patch.object(candidate,'snapshot',side_effect=self.state),self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            self.assertFalse(json.loads((probe.output/'candidate-readiness-proof.json').read_bytes())['passed'])

    def test_native_language_phase_has_only_owned_preference_input_and_no_content_writer(self):
        root=Path(__file__).resolve().parents[1];java=(root/'scripts/android-runtime/RuntimeProbe.java').read_text('utf-8')
        method=java.split('private void candidateLanguageRead(String phase)',1)[1].split('private AccessibilityNodeInfo propertyControl',1)[0]
        for forbidden in ('setText(', 'inputText(', 'save();', 'evaluateJavascript', 'Runtime.evaluate', 'operation_id'):
            self.assertNotIn(forbidden,method)
        for required in ('android.widget.CheckedTextView','界面语言','Interface language','画布节点与连接','Canvas nodes and connections','propertyValue(found,name,NODE)','matches.size()<=1'):
            self.assertIn(required,method)

if __name__=='__main__':unittest.main()
