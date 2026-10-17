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
            'application_or_native_IPC_sent':False,'Debugger_enabled':False,'raw_resource_bytes_exported':False}
        self.probe.command=Mock(side_effect=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(self.result).encode(),stderr=b''))

    def test_original_package_PID_and_resource_metadata_are_bound_without_native_bug_claim(self):
        result=renderer.read_original_renderer(self.probe,self.context,self.manifest,self.value)
        self.assertEqual(result,self.result);self.probe.command.assert_called_once()
        proof=json.loads((self.probe.output/'original-renderer-resource-proof.json').read_text('utf-8'))
        self.assertEqual(proof['APK_sha256'],self.probe.binding['sha256'])
        self.assertTrue(proof['code_presence_not_a_real_native_declared_code_failure'])
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


if __name__=='__main__':unittest.main()
