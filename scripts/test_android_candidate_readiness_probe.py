"""Candidate admission and original-state comparators, not actual Android UI evidence."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hashlib,json,tempfile,unittest,yaml
import android_candidate_readiness_probe as candidate
from android_runtime_probe import Probe,BODY,DRAFT,RICH_TEXT
from android_workflow_gate import admitted

class CandidateReadinessBoundaries(unittest.TestCase):
    @patch.object(candidate,'require_hosted_emulator')
    def test_language_can_start_after_export_has_intentionally_stopped_the_app(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root)
            def original_pid(*args,**kwargs):
                if probe.phase.call_count<3:raise RuntimeError('Previous export deliberately stopped this app; no PID yet')
                return SimpleNamespace(stdout=b'201' if probe.phase.call_count==3 else b'202')
            probe.shell.side_effect=original_pid
            with patch.object(candidate,'snapshot',side_effect=self.state):candidate.verify_candidate(probe)
            self.assertEqual(probe.shell.call_count,2)
            proof=json.loads((probe.output/'candidate-readiness-proof.json').read_bytes())
            self.assertTrue(proof['passed']);self.assertEqual(proof['cold_PIDs'],[201,202])

    def test_remaining_mode_does_not_run_the_already_accepted_native_suites(self):
        for scope in ('lifecycle-remaining','lifecycle-editor-remaining','candidate-languages'):
            probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope=scope)
            probe.device=Mock();probe.shell=Mock()
            probe.native_keystore_tests();probe.device.assert_not_called();probe.shell.assert_not_called()

    def test_remaining_workflow_can_only_reuse_a_fixed_apk(self):
        root=Path(__file__).resolve().parents[1];flow=yaml.load((root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for scope in ('lifecycle-remaining','lifecycle-editor-remaining','candidate-languages'):
            for reuse in ('','39148381613'):
                for job in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                    self.assertFalse(admitted(flow['jobs'][job]['if'],scope,reuse=reuse,build='skipped'))
            self.assertTrue(admitted(flow['jobs']['emulator-runtime']['if'],scope,reuse='39148381613',build='skipped'))

    def test_standalone_language_scope_does_not_replay_old_content_phases(self):
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='candidate-languages');probe.boot=Mock();probe.phase=Mock()
        with patch.object(candidate,'run_languages') as run:probe.run()
        probe.boot.assert_called_once();run.assert_called_once_with(probe);probe.phase.assert_not_called()

    def test_language_fixture_is_declared_crlf_with_one_original_text_node(self):
        value=json.loads(candidate.LOCALE_FIXTURE);self.assertEqual(len(value['nodes']),1);self.assertEqual(value['edges'],[])
        self.assertEqual(value['nodes'][0]['text'],candidate.NODE);self.assertIn('vendor',value['nodes'][0]);self.assertIn('vendor-root',value)
        self.assertIn(b'\r\n',candidate.LOCALE_FIXTURE);self.assertNotIn(b'\n',candidate.LOCALE_FIXTURE.replace(b'\r\n',b''))

    @patch.object(candidate,'require_hosted_emulator')
    def test_language_runner_refuses_native_suite_or_old_scope_before_seed(self,_):
        for scope,suite in [('candidate-languages',{'tests':5}),('lifecycle',None)]:
            probe=Mock();probe.args=SimpleNamespace(scope=scope,serial='emulator-5562');probe.keystore_test_result=suite
            with self.assertRaises(RuntimeError):candidate.run_languages(probe)
            probe.phase.assert_not_called();probe.device.assert_not_called()

    @patch.object(candidate,'require_hosted_emulator')
    def test_language_setup_has_one_gui_save_and_declared_fixture_before_readonly_verify(self,_):
        self._language_seed(False)

    @patch.object(candidate,'require_hosted_emulator')
    def test_changed_setup_file_identity_stops_before_any_language_input(self,_):
        self._language_seed(True)

    def _language_seed(self,changed):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'GITHUB_RUN_ID':'1'}):
            probe=Mock();probe.args=SimpleNamespace(scope='candidate-languages',serial='emulator-5562',tools_commit='tools',commit='compiled')
            probe.output=Path(root);probe.binding={'sha256':'fixed'};probe.keystore_test_result=None;probe.root='owned/'
            probe.original.side_effect=[b'{"nodes":[],"edges":[]}',candidate.LOCALE_FIXTURE,candidate.LOCALE_FIXTURE]
            before={'files':[{'id':'original'}]};after={'files':[{'id':'changed' if changed else 'original'}]}
            with patch.object(candidate,'snapshot',side_effect=[before,after]),patch('android_canvas_graph_probe.saved_original',return_value={'operation_id':'original'}),patch.object(candidate,'verify_candidate') as verify:
                if changed:
                    with self.assertRaises(RuntimeError):candidate.run_languages(probe)
                    verify.assert_not_called()
                else:candidate.run_languages(probe);verify.assert_called_once_with(probe)
            self.assertEqual([v.args[0] for v in probe.phase.call_args_list],list(candidate.SEED_STEPS))
            probe.enable_test_ime.assert_not_called();self.assertEqual(probe.device.call_count,1)
            self.assertEqual(probe.device.call_args.kwargs['input'],candidate.LOCALE_FIXTURE)
            proof=json.loads((probe.output/'candidate-languages-seed-proof.json').read_bytes());self.assertEqual(proof['passed'],not changed)
            self.assertTrue(proof['declared_synthetic_one_node_fixture_not_GUI_node_creation'])

    def test_content_remaining_excludes_the_original_auth_read_and_theme_actions(self):
        self._remaining_ui('lifecycle-editor-remaining',False)

    def test_remaining_ui_routes_past_old_theme_diagnostics_and_keeps_auth_index_read(self):
        self._remaining_ui('lifecycle-remaining',True)

    def _remaining_ui(self,scope,auth):
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope=scope)
        probe.binding={'native_auth_gateway':{'present':True}};probe.boot=Mock();probe.check_empty_native_auth_index=Mock()
        probe.enable_test_ime=Mock();probe.finish_editing=Mock();current={'note':BODY.encode()}
        def phase(name,**kwargs):
            if name=='recover':current['note']=DRAFT.encode()
        probe.phase=Mock(side_effect=phase)
        probe.original=Mock(side_effect=lambda path:current['note'] if path=='runtime-note.md' else ('**'+RICH_TEXT+'**\n').encode())
        probe.draft_rows=Mock(return_value=[('runtime-note.md',DRAFT,2,1)])
        with patch.object(candidate,'verify_candidate') as verify:probe.run()
        self.assertEqual([c.args[0] for c in probe.phase.call_args_list],(['auth-read'] if auth else [])+['markdown','rich-markdown','rich-reopen','draft','recover'])
        self.assertEqual(probe.check_empty_native_auth_index.call_count,int(auth));probe.finish_editing.assert_called_once();verify.assert_called_once_with(probe)

    @patch.object(candidate,'require_hosted_emulator')
    def test_remaining_bilingual_report_does_not_impersonate_the_original_native_suites(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root);probe.args.scope='lifecycle-remaining';probe.keystore_test_result=None
            (probe.output/'window-insets-device-proof.json').unlink()
            with patch.object(candidate,'snapshot',side_effect=self.state):candidate.verify_candidate(probe)
            proof=json.loads((probe.output/'candidate-readiness-proof.json').read_bytes())
            self.assertTrue(proof['passed']);self.assertFalse(proof['native_suites_executed_here'])
            self.assertTrue(proof['prior_native_suites_not_impersonated']);self.assertIsNone(proof['Native_tests']);self.assertIsNone(proof['native_insets'])

    @patch.object(candidate,'require_hosted_emulator')
    def test_remaining_mode_refuses_a_current_native_suite_result(self,_):
        with tempfile.TemporaryDirectory() as root:
            probe=self.probe(root);probe.args.scope='lifecycle-remaining'
            with self.assertRaises(RuntimeError):candidate.verify_candidate(probe)
            probe.phase.assert_not_called()

    def test_readonly_auth_scroll_is_owned_bounded_and_never_sends_credentials(self):
        root=Path(__file__).resolve().parents[1];java=(root/'scripts/android-runtime/RuntimeProbe.java').read_text('utf-8')
        method=java.split('private void readonlyAuthStatus()',1)[1].split('\n    private ',1)[0]
        for required in ('within(node,"Settings")','within(node,"Saved connections")','matches.size()<=1','if(!shown)','ACTION_SHOW_ON_SCREEN','bounds.height()>0','window.contains','credential_input=0'):
            self.assertIn(required,method)
        for forbidden in ('setText(', 'inputText(', 'activate(', 'save();', 'Sign in'):
            self.assertNotIn(forbidden,method)
        auth=java.split('case "auth-read":',1)[1].split('case "pair-note-reopen":',1)[0]
        self.assertLess(auth.index('empty password'),auth.index('readonlyAuthStatus()'));self.assertIn('pressBack();',auth)

    def test_lifecycle_waits_for_original_image_receipt_and_checks_after_the_single_save(self):
        for scope in ('lifecycle','lifecycle-remaining','lifecycle-editor-remaining'):
            with self.subTest(scope=scope):self._original_image_scope(scope)

    def _original_image_scope(self,scope):
        with tempfile.TemporaryDirectory() as root:
            probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope=scope);probe.output=Path(root)
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
        probe.shell.side_effect=[SimpleNamespace(stdout=str(i).encode()) for i in [201,202]]
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
