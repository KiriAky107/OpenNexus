"""Synthetic command handles only; no local ADB, app or emulator is started."""
from copy import deepcopy
import json,os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_original_outcome_process as process
from android_desktop_handoff import HandoffError
from android_runtime_probe import APP,DRIVER


class OriginalStop(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name).resolve();self.output=self.workspace/'.build/evidence';self.output.mkdir(parents=True)
        self.context={'run_id':1234,'tools_commit':'a'*40,'android_commit':'b'*40,'desktop_commit':'931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}
        self.packages=[{'package':name,'sha256':'c'*64,'uid':10200+i,'installed_path':'/data/app/~~owned/'+name+'-original/base.apk'} for i,name in enumerate([APP,DRIVER,'cc.kronecker.opennexus.testime'])]
        self.manifest={'nonce':'00000000-0000-4000-8000-000000000001','fingerprint':'d'*64,'original_pid':321,
                       'device':{'avd':'owned','boot_id':'00000000-0000-4000-8000-000000000002'},'packages':self.packages}
        self.authorization={'event':'owned-stop-authorized','nonce':self.manifest['nonce'],'fingerprint':self.manifest['fingerprint'],'original_pid':321}
        self.probe=SimpleNamespace(output=self.output,owner=self.workspace/'.build/owner',
            args=SimpleNamespace(serial='emulator-5562',commit='b'*40,tools_commit='a'*40),binding={'sha256':'c'*64})
        self.stopped=False;self.unknown=False;self.after_stderr=b'';self.connected=True
        def shell(*args,**kwargs):
            if args==('am','force-stop',APP):
                self.stopped=True
                if self.unknown:raise TimeoutError('PRIVATE_MESSAGE_MUST_NOT_ESCAPE')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            if args==('pidof',APP):return SimpleNamespace(returncode=1 if self.stopped else 0,
                stdout=b'' if self.stopped else b'321\n',stderr=self.after_stderr if self.stopped else b'')
            raise AssertionError('Unexpected command')
        self.probe.shell=Mock(side_effect=shell)
        self.probe.device=Mock(side_effect=lambda *a,**k:SimpleNamespace(returncode=0 if self.connected else 1,
            stdout=b'device\n' if self.connected else b'',stderr=b'' if self.connected else b'offline'))
        self.env=patch.dict(os.environ,{'GITHUB_WORKSPACE':str(self.workspace)});self.env.start();self.addCleanup(self.env.stop)
        for name,value in [('hosted_scope',None),('observe_device',self.manifest['device']),('observe_packages',self.packages)]:
            item=patch.object(process,name,return_value=value);mock=item.start();self.addCleanup(item.stop);setattr(self,name,mock)
    def writes(self):return [c for c in self.probe.shell.call_args_list if c.args==('am','force-stop',APP)]

    def test_one_owned_stop_and_original_observation_no_replay_on_second_call(self):
        value=process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertTrue(value['original_absence_observed']);self.assertEqual(value['original_exit_proof']['force_stop_returncode'],0)
        self.assertTrue(value['original_exit_proof']['original_pidof_stderr_empty']);self.assertEqual(len(self.writes()),1)
        calls=self.probe.shell.call_count
        with self.assertRaisesRegex(HandoffError,'ALREADY_REQUESTED'):process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertEqual(self.probe.shell.call_count,calls)

    def test_nonempty_error_on_before_PID_or_stop_reply_never_confirms_a_known_stop(self):
        base=self.probe.shell.side_effect
        def before_error(*args,**kwargs):
            value=base(*args,**kwargs)
            if args==('pidof',APP) and not self.stopped:value.stderr=b'error: stale transport'
            return value
        self.probe.shell.side_effect=before_error
        with self.assertRaisesRegex(HandoffError,'PID_CHANGED'):
            process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertEqual(len(self.writes()),0)
        def stop_error(*args,**kwargs):
            value=base(*args,**kwargs)
            if args==('am','force-stop',APP):value.stderr=b'error: unconfirmed command'
            return value
        self.probe.shell.side_effect=stop_error
        value=process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertEqual(value['command_outcome'],'unknown')
        self.assertIsNone(value['original_exit_proof']['force_stop_returncode'])
        self.assertEqual(len(self.writes()),1)

    def test_unknown_stop_is_readonly_reconciled_but_never_given_a_success_returncode_or_repeated(self):
        self.unknown=True
        value=process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertEqual(value['command_outcome'],'unknown');self.assertIsNone(value['original_exit_proof']['force_stop_returncode'])
        again=process.reconcile_original_stop(self.probe,self.context,self.manifest,'second')
        self.assertEqual(again['command_outcome'],'unknown');self.assertEqual(len(self.writes()),1)
        self.assertNotIn('PRIVATE_MESSAGE',''.join(p.read_text('utf-8') for p in self.output.glob('*.json')))

    def test_adb_error_with_empty_pid_output_is_not_proof_of_absence_even_after_reconnection(self):
        self.after_stderr=b'error: device offline'
        value=process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertFalse(value['original_absence_observed']);self.assertFalse(value['original_exit_proof']['original_pidof_stderr_empty'])
        self.assertTrue(value['original_exit_proof']['owned_emulator_still_connected']);self.assertEqual(len(self.writes()),1)

    def test_lost_connection_after_pid_query_does_not_prove_exit(self):
        self.connected=False
        value=process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertFalse(value['original_absence_observed']);self.assertFalse(value['original_exit_proof']['owned_emulator_still_connected'])
        self.assertEqual(len(self.writes()),1)

    def test_foreign_context_or_authorization_refuses_before_any_command_or_intent(self):
        self.hosted_scope.side_effect=RuntimeError('Host refused')
        with self.assertRaises(RuntimeError):process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.probe.shell.assert_not_called();self.assertEqual(list(self.output.iterdir()),[])
        self.hosted_scope.side_effect=None
        with self.assertRaisesRegex(HandoffError,'AUTHORIZATION_REFUSED'):
            process.stop_original_once(self.probe,self.context,self.manifest,dict(self.authorization,fingerprint='f'*64))
        self.probe.shell.assert_not_called();self.assertEqual(list(self.output.iterdir()),[])

    def test_replaced_device_or_original_package_refuses_before_stop_and_immutable_intent_cannot_be_adopted(self):
        self.observe_device.return_value={'avd':'other'}
        with self.assertRaisesRegex(HandoffError,'DEVICE_OR_PACKAGE_CHANGED'):process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        self.assertEqual(len(self.writes()),0);self.assertEqual(list(self.output.iterdir()),[])
        self.observe_device.return_value=self.manifest['device']
        process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        changed=deepcopy(self.manifest);changed['packages'][1]['uid']+=1;self.observe_packages.return_value=changed['packages']
        with self.assertRaisesRegex(HandoffError,'INTENT_CHANGED'):process.reconcile_original_stop(self.probe,self.context,changed,'foreign')
        self.assertEqual(len(self.writes()),1)

    def test_an_unknown_record_cannot_be_tampered_into_a_confirmed_zero_returncode(self):
        self.unknown=True;process.stop_original_once(self.probe,self.context,self.manifest,self.authorization)
        path=self.output/'original-native-stop-command.json';value=json.loads(path.read_text('utf-8'));value['returncode']=0;path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(HandoffError,'COMMAND_RECORD_REFUSED'):process.reconcile_original_stop(self.probe,self.context,self.manifest,'tampered')
        self.assertEqual(len(self.writes()),1)


if __name__=='__main__':unittest.main(verbosity=2)
