"""New lost-response scope boundaries; no Android, service or network is started."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from android_auth_fixture import DeliveryProxy,arm_control,initialize_control
from android_release_auth_recovery import PHASES,ReleaseAuthRecovery,validate_recovery
import test_android_release_auth as auth_examples

OPERATION='11111111-2222-3333-4444-555555555555'


class ReleaseRecovery(unittest.TestCase):
    def sequence(self):
        provider,native,sealed=auth_examples.ReleaseAuthBoundaries().sequence()
        control=dict(operation_id=OPERATION,mode='drop_refresh',expected_revision=0,result_revision=1)
        marker=dict(operation_id=OPERATION,mode='drop_refresh',revision=1,action='refresh',service_status=200,service_response_complete=True,raw_body_recorded=False)
        before=provider[0];after=provider[2]
        before['requests']['events']=[dict(action='login',service_status=200,delivery_status=200,fault_operation=None)]
        after['requests']['events']=before['requests']['events']+[dict(action='refresh',service_status=200,delivery_status=503,fault_operation=OPERATION)]
        pending=native[2];pending['intents'][1]['phase']='refresh_pending'
        cold=deepcopy(pending);cold['intents'][1]['phase']='unknown'
        return [before,after,deepcopy(after)],[native[0],pending,cold],[deepcopy(sealed[0]) for _ in range(3)],control,marker

    def test_original_provider_commit_pending_and_only_cold_unknown_transition(self):
        values=self.sequence();result=validate_recovery(*values)
        self.assertEqual(result['original_refresh_id'],'original-refresh')
        self.assertEqual(result['fault_control_id'],OPERATION)
        for mode in ('repost','service-failed','wrong-fault','new-selection','new-cold-ID','cipher-rotated','silent-active'):
            provider,native,cipher,control,marker=deepcopy(values)
            if mode=='repost':provider[2]['requests']['counts']['refresh:200']=2
            if mode=='service-failed':marker['service_status']=503
            if mode=='wrong-fault':provider[1]['requests']['events'][-1]['fault_operation']='other'
            if mode=='new-selection':native[2]['selection']['operation_id']='other'
            if mode=='new-cold-ID':native[2]['intents'][1]['operation_id']='other'
            if mode=='cipher-rotated':cipher[1]['owned.bin']['sha256']='f'*64
            if mode=='silent-active':native[2]['intents'][1]['phase']='active'
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):validate_recovery(provider,native,cipher,control,marker)

    def test_actual_existing_fault_proxy_marker_matches_new_scope_without_exporting_response(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);initialize_control(root);control=arm_control(root,OPERATION,'drop_refresh',0)
            messages=[]
            async def app(scope,receive,send):
                await send(dict(type='http.response.start',status=200,headers=[]))
                await send(dict(type='http.response.body',body=b'{"access_token":"never-export-response"}'))
            async def send(value):messages.append(value)
            async def receive():return dict(type='http.request',body=b'')
            asyncio.run(DeliveryProxy(app,root)(dict(type='http',method='POST',path='/sync/v1/auth/refresh'),receive,send))
            marker=json.loads((root/'fault-events'/(OPERATION+'.json')).read_bytes());values=self.sequence()
            validate_recovery(*values[:3],control,marker)
            self.assertEqual(messages[0]['status'],503);self.assertNotIn('never-export',str(messages))
            self.assertFalse(marker['raw_body_recorded'])

    def test_recovery_entry_does_not_admit_normal_refresh_logout_or_arbitrary_phase(self):
        subject=object.__new__(ReleaseAuthRecovery);subject.stop=Mock()
        self.assertEqual(subject.allowed_phases,PHASES)
        for name in ('current','refresh','logout','login-b-held','invalid-certificate','../refresh-unknown'):
            with self.subTest(name=name),self.assertRaises(RuntimeError):subject.phase(name,'forbidden')
        subject.stop.assert_not_called()

    def test_unknown_original_refresh_is_not_retried_or_followed_by_another_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            subject=object.__new__(ReleaseAuthRecovery);subject.output=Path(folder);subject.owner=Path(folder)
            subject.prepare=Mock();subject.control=Mock(return_value=OPERATION);subject.phases=[]
            subject.phase=Mock(side_effect=[None,RuntimeError('original result unknown')])
            provider,native,cipher,control,marker=self.sequence()
            subject.observe=Mock(return_value=provider[0]);subject.snapshot=Mock(return_value=native[0])
            subject.observer=Mock();subject.observer.ciphertext_fingerprints.return_value=cipher[0]
            with patch('android_release_auth_recovery.read_json',return_value=control):
                with self.assertRaisesRegex(RuntimeError,'original result unknown'):subject.run()
            self.assertEqual(subject.phase.call_args_list[0].args,('login-a','bootstrap-login'))
            self.assertEqual(subject.phase.call_args_list[1].args,('refresh-unknown','lost-refresh'))
            self.assertEqual(subject.phase.call_count,2);subject.control.assert_called_once_with('drop_refresh')
            subject.prepare.assert_called_once();self.assertEqual(subject.observe.call_count,1)


if __name__=='__main__':unittest.main()
