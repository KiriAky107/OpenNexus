"""Owned runtime observation and secret-delivery boundaries; no device starts."""
from argparse import Namespace
import json
import unittest
from unittest.mock import Mock,patch
import android_auth_runtime as module


class AuthRuntimeBoundaries(unittest.TestCase):
    def test_live_background_driver_cannot_start_on_a_developer_host_or_with_a_nonoriginal_control_id(self):
        probe=object.__new__(module.AuthProbe);probe.args=Namespace(serial='emulator-5562')
        probe.start_app=Mock();probe.shell=Mock();probe.control=Mock()
        with patch.object(module.subprocess,'Popen') as spawn:
            with patch.object(module,'require_hosted_emulator',side_effect=RuntimeError('owned hosted emulator required')):
                with self.assertRaises(RuntimeError):probe.held_background('not-an-original-id',{})
            with patch.object(module,'require_hosted_emulator'):
                with self.assertRaises(ValueError):probe.held_background('not-an-original-id',{})
            spawn.assert_not_called()
        probe.start_app.assert_not_called();probe.shell.assert_not_called();probe.control.assert_not_called()

    def test_resumed_activity_observation_selects_only_own_static_identity_without_persisting_the_dump(self):
        from subprocess import CompletedProcess
        probe=object.__new__(module.AuthProbe)
        probe.shell=Mock(return_value=CompletedProcess([],0,('topResumedActivity=ActivityRecord{owned u0 '+module.APP+
            '/cc.kronecker.opennexus.MainActivity}\nprivate-activity-detail-not-an-artifact').encode()))
        self.assertTrue(probe.app_is_resumed())
        probe.shell.return_value=CompletedProcess([],0,b'topResumedActivity=ActivityRecord{u0 system.launcher/.Home}\n')
        self.assertFalse(probe.app_is_resumed())

    def test_auth_only_scope_requires_the_original_worker_binding_without_repeating_keystore_tests(self):
        probe=object.__new__(module.AuthProbe);probe.binding={'native_tests':{'expected_tests':5}}
        probe.shell=Mock();probe.device=Mock()
        probe.native_keystore_tests()
        probe.shell.assert_not_called();probe.device.assert_not_called()
        probe.binding['native_tests']['expected_tests']=4
        with self.assertRaises(RuntimeError):probe.native_keystore_tests()

    def test_ui_failure_preserves_provider_and_native_outcomes_even_if_one_read_is_unavailable(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            probe=object.__new__(module.AuthProbe);probe.output=Path(directory)
            probe.observe=Mock(side_effect=RuntimeError('not exported provider detail'))
            probe.snapshot=Mock();probe.ciphertext_fingerprints=Mock(return_value={'opaque-native-name':'a'*64})
            probe.failure_observations()
            probe.snapshot.assert_called_once_with('failure')
            self.assertEqual(json.loads((probe.output/'failure-provider-unavailable.json').read_text()),
                {'error':'RuntimeError','no_mutation_retried':True})
            self.assertNotIn('not exported', ''.join(f.read_text() for f in probe.output.iterdir()))
            self.assertEqual(json.loads((probe.output/'failure-ciphertext-fingerprints.json').read_text()),
                {'opaque-native-name':'a'*64})

    def test_current_recovery_may_mark_original_unknown_without_replacing_its_id_generation_device_or_selection(self):
        from copy import deepcopy
        before={'selection':{'operation_id':'original','revision':3},'intents':[
            {'operation_id':'original','phase':'refresh_pending','generation':2,'device_id':'device-a'}]}
        after=deepcopy(before);after['intents'][0]['phase']='unknown'
        module.expect_original_recovery(before,after)
        candidates=[]
        for key,value in [('phase','active'),('operation_id','new'),('generation',3),('device_id','device-b')]:
            changed=deepcopy(after);changed['intents'][0][key]=value;candidates.append(changed)
        changed=deepcopy(after);changed['selection']['revision']=4;candidates.append(changed)
        for changed in candidates:
            with self.assertRaises(RuntimeError):module.expect_original_recovery(before,changed)

    def configuration(self):
        return {'endpoint':'https://owned-unit-only.trycloudflare.com','accounts':[
            {'username':'android-owned-'+kind+'-'+'a'*12,'password':'unit_only_private_password_'+'b'*20,
             'device_name':'Owned Android '+kind+' unit'} for kind in ('a','b')]}

    def test_password_only_enters_the_exclusive_driver_file_on_stdin_and_never_command_arguments(self):
        probe=object.__new__(module.AuthProbe);probe.args=Namespace(serial='emulator-5562');probe.shell=Mock();probe.device=Mock()
        config=self.configuration()
        with patch.object(module,'require_hosted_emulator'):
            probe.stage_owned_input(config)
        call=probe.device.call_args
        self.assertEqual(json.loads(call.kwargs['input']),config)
        self.assertNotIn(config['accounts'][0]['password'],str(call.args))
        self.assertIn('test ! -e no_backup/owned-auth.json',str(call.args))
        self.assertIn('umask 077',str(call.args))

    def test_foreign_host_endpoint_or_identity_is_refused_before_adb_or_secret_delivery(self):
        for variant in ('developer-host','endpoint','identity'):
            probe=object.__new__(module.AuthProbe);probe.args=Namespace(serial='emulator-5562');probe.shell=Mock();probe.device=Mock()
            config=self.configuration()
            if variant=='endpoint':config['endpoint']='https://production.example'
            if variant=='identity':config['accounts'][0]['username']='existing-user'
            with patch.object(module,'require_hosted_emulator',side_effect=RuntimeError('owned required') if variant=='developer-host' else None):
                with self.assertRaises(RuntimeError):probe.stage_owned_input(config)
            probe.shell.assert_not_called();probe.device.assert_not_called()

    def test_provider_evidence_refuses_any_additional_post_or_unexpected_auth_error(self):
        expected={'requests':{'counts':{'login:200':1,'refresh:200':1}}}
        module.expect_requests(expected,1,1,0)
        for changed in ({'login:200':2,'refresh:200':1},{'login:200':1,'refresh:200':1,'login:401':1}):
            with self.assertRaises(RuntimeError):module.expect_requests({'requests':{'counts':changed}},1,1,0)

    def test_historical_active_record_cannot_prove_a_new_or_unconfirmed_selection(self):
        record={'operation_id':'original','kind':'login','phase':'active','device_id':'device-a'}
        valid={'selection':{'operation_id':'original'},'intents':[record]}
        self.assertEqual(module.active_intent(valid,'login','device-a'),record)
        for selection,phase,kind,device in [('other','active','login','device-a'),('original','refresh_pending','login','device-a'),
            ('original','active','refresh','device-a'),('original','active','login','device-b')]:
            with self.assertRaises(RuntimeError):module.active_intent(
                {'selection':{'operation_id':selection},'intents':[{**record,'phase':phase}]},kind,device)


if __name__=='__main__':unittest.main()
