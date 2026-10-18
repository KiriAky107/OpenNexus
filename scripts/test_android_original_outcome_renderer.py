"""Renderer metadata linkage only; fake command result, no app/CDP execution."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from android_desktop_handoff import HandoffError
import android_original_outcome_renderer as renderer
import test_android_original_outcome_process as process_fixture


class RendererLink(unittest.TestCase):
    def setUp(self):
        self.owner=process_fixture.OriginalStop('test_one_owned_stop_and_original_observation_no_replay_on_second_call')
        self.owner.setUp();self.addCleanup(self.owner.doCleanups)
        self.probe,self.context,self.manifest=self.owner.probe,self.owner.context,self.owner.manifest
        self.value={'configuration':{'nonce':self.manifest['nonce'],'original_pid':321},'context':{}}
        self.result={'nonce':self.manifest['nonce'],'original_pid':321,'declared_safe_code_list':True,'declared_presentation_case':True,
            'application_or_native_IPC_sent':False,'Debugger_enabled':True,'Debugger_pause_breakpoint_or_evaluation_sent':False,
            'page_fetch_or_CSP_change_sent':False,'digest_scope':'UTF-8 bytes of decoded V8 script source, not HTTP wire encoding',
            'resource_read_transport':'Debugger.getScriptSource','resource_bytes_transferred_to_owned_reader':True,'resource_bytes_written_to_artifacts':False}
        self.probe.command=Mock(side_effect=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(self.result).encode(),stderr=b''))

    def test_original_package_PID_and_resource_metadata_are_bound_without_native_bug_claim(self):
        result=renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.assertEqual(result,self.result);self.probe.command.assert_called_once()
        proof=json.loads((self.probe.output/'original-renderer-resource-proof.json').read_text('utf-8'))
        self.assertEqual(proof['APK_sha256'],self.probe.binding['sha256'])
        self.assertTrue(proof['code_presence_not_a_real_native_declared_code_failure'])
        self.assertTrue(proof['original_renderer']['Debugger_enabled'])
        call=self.probe.command.call_args
        self.assertEqual(call.args[0][2].name,'original_compiled_scripts.mjs')
        self.assertEqual(call.kwargs['timeout'],30)
        with self.assertRaises(FileExistsError):renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.probe.command.assert_called_once()

    def test_wrong_PID_or_missing_markers_are_unverified_without_native_input_or_retry(self):
        self.result['original_pid']=322
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.assertFalse((self.probe.output/'original-renderer-resource-proof.json').exists())
        self.probe.command.assert_called_once()

    def test_foreign_nonce_does_not_read_a_resource_or_create_an_intent(self):
        self.value['configuration']['nonce']='00000000-0000-4000-8000-000000000099'
        with self.assertRaisesRegex(HandoffError,'ORIGINAL_REFUSED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.probe.command.assert_not_called();self.assertEqual(list(self.probe.output.iterdir()),[])

    def test_failed_child_keeps_only_allowlisted_diagnostic_and_never_retries(self):
        self.probe.command=Mock(return_value=SimpleNamespace(returncode=1,stderr=b'PRIVATE STDERR',
            stdout=json.dumps({'error':'OWNED_COMPILED_RESOURCE_UNCONFIRMED','stage':'resource-content','reason':'reply-refused'}).encode()))
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        path=self.probe.output/'original-renderer-resource-diagnostic.json';value=json.loads(path.read_text('utf-8'))
        self.assertEqual(value['stage'],'resource-content');self.assertEqual(value['reason'],'reply-refused')
        self.assertEqual(value['stderr_bytes_discarded'],len(b'PRIVATE STDERR'))
        self.assertNotIn('PRIVATE',path.read_text('utf-8'));self.probe.command.assert_called_once()
        self.assertFalse((self.probe.output/'original-renderer-resource-proof.json').exists())

    def test_arbitrary_child_json_or_error_fields_are_not_exported(self):
        self.probe.command=Mock(return_value=SimpleNamespace(returncode=1,stderr=b'',
            stdout=b'{"error":"PRIVATE","stage":"PRIVATE","reason":"PRIVATE","body":"PRIVATE"}'))
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        path=self.probe.output/'original-renderer-resource-diagnostic.json';value=json.loads(path.read_text('utf-8'))
        self.assertEqual(value['stage'],'admission');self.assertEqual(value['reason'],'unconfirmed')
        self.assertNotIn('PRIVATE',path.read_text('utf-8'));self.probe.command.assert_called_once()

    def test_safe_actual_tree_counts_are_preserved_without_paths_or_resource_bodies(self):
        summary={'frame_present':True,'frame_id_valid':True,'frame_origin_owned':True,'resources_array':True,
                 'resource_count_bounded':2,'owned_script_candidates':0,'owned_other_js_candidates':1,
                 'types':{'Script':0,'Other':1,'Image':1,'Stylesheet':0,'Fetch':0,'XHR':0,'Unknown':0}}
        self.probe.command=Mock(return_value=SimpleNamespace(returncode=1,stderr=b'',
            stdout=json.dumps({'error':'OWNED_COMPILED_RESOURCE_UNCONFIRMED','stage':'resource-tree',
                               'reason':'no-owned-script','tree_summary':summary}).encode()))
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        value=json.loads((self.probe.output/'original-renderer-resource-diagnostic.json').read_text('utf-8'))
        self.assertEqual(value['tree_summary'],summary);self.probe.command.assert_called_once()

    def test_tree_summary_refuses_hidden_fields_unknown_type_names_and_invalid_bounds(self):
        summary={'frame_present':True,'frame_id_valid':True,'frame_origin_owned':True,'resources_array':True,
                 'resource_count_bounded':1,'owned_script_candidates':1,'owned_other_js_candidates':0,
                 'types':{'Script':1,'Other':0,'Image':0,'Stylesheet':0,'Fetch':0,'XHR':0,'Unknown':0}}
        from copy import deepcopy
        for change in (lambda v:v.update(url='PRIVATE_URL'),lambda v:v['types'].update(PRIVATE_TYPE=1),
                       lambda v:v.update(resource_count_bounded=999),lambda v:v.update(owned_script_candidates=True),
                       lambda v:v.update(resource_count_bounded=0)):
            value=deepcopy(summary);change(value)
            with self.assertRaises(HandoffError):renderer.safe_tree_summary(value)

    def test_parsed_script_diagnostics_keep_bounded_flags_without_IDs_URLs_or_source(self):
        summary={'parsed_notifications':13,'owned_candidates':2,'protocol_admitted':True,
                 'Debugger_enable_reply_seen':True,'unexpected_pause_observed':False}
        self.probe.command=Mock(return_value=SimpleNamespace(returncode=1,stderr=b'',
            stdout=json.dumps({'error':'OWNED_COMPILED_RESOURCE_UNCONFIRMED','stage':'script-content',
                               'reason':'reply-refused','script_summary':summary}).encode()))
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        value=json.loads((self.probe.output/'original-renderer-resource-diagnostic.json').read_text('utf-8'))
        self.assertEqual(value['script_summary'],summary);self.probe.command.assert_called_once()

    def test_parsed_summary_refuses_extra_source_and_unbounded_or_inconsistent_flags(self):
        from copy import deepcopy
        summary={'parsed_notifications':13,'owned_candidates':2,'protocol_admitted':True,
                 'Debugger_enable_reply_seen':True,'unexpected_pause_observed':False}
        for change in (lambda v:v.update(scriptSource='PRIVATE'),lambda v:v.update(parsed_notifications=True),
                       lambda v:v.update(owned_candidates=26),lambda v:v.update(parsed_notifications=1),
                       lambda v:v.update(protocol_admitted=False),lambda v:v.update(unexpected_pause_observed='yes')):
            value=deepcopy(summary);change(value)
            with self.assertRaises(HandoffError):renderer.safe_script_summary(value)

    def test_old_cache_read_or_a_paused_evaluated_reader_is_not_a_parsed_source_proof(self):
        self.result.update(Debugger_enabled=False,resource_read_transport='Page.getResourceContent')
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.probe.command.assert_called_once()
        self.assertFalse((self.probe.output/'original-renderer-resource-proof.json').exists())

    def test_a_reader_claiming_pause_or_evaluation_cannot_advance_original_native_input(self):
        self.result['Debugger_pause_breakpoint_or_evaluation_sent']=True
        with self.assertRaisesRegex(HandoffError,'READ_UNCONFIRMED'):
            renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.probe.command.assert_called_once()
        self.assertFalse((self.probe.output/'original-renderer-resource-proof.json').exists())


if __name__=='__main__':unittest.main()
