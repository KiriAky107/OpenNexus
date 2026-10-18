from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,tempfile,unittest
from unittest.mock import Mock,patch
from android_sync_rejection import OriginalRevocation,validate_rejected_sync,validate_revoked_accounts,validate_rejected_refresh,original_driver_intent

TARGET='a'*32;ACTOR='b'*32;VAULT='c'*32;OP='c4427c0c-463a-4217-b31a-4b96edbca901';FILE='c4427c0c-463a-4217-b31a-4b96edbca902'

class RejectionBoundaries(unittest.TestCase):
    def controller(self,directory):
        owner=Path(directory)
        setup=SimpleNamespace(owner=owner,endpoint='https://owned-negative.trycloudflare.com',
            session=lambda:{'access_token':'x'*43,'device_id':ACTOR})
        with patch('android_sync_rejection.owned_root',return_value=owner):return OriginalRevocation(setup)

    def listing(self,revoked):return 200,json.dumps({'items':[{'id':ACTOR,'name':'owned actor','revoked':0,'authenticated_at':None},
        {'id':TARGET,'name':'owned actual Android','revoked':int(revoked),'authenticated_at':123}],
        'observed_at':123,'activity_sample_seconds':60}).encode()

    def test_actual_delete_has_one_original_intent_and_later_calls_only_read_its_state(self):
        with tempfile.TemporaryDirectory() as directory:
            actor=self.controller(directory)
            def request(method,path,token):
                if method=='DELETE':
                    self.assertTrue((actor.root/'revoke-intent.json').is_file())
                    return 204,b''
                return self.listing(actor.request.call_count>1)
            actor.request=Mock(side_effect=request)
            result=actor.revoke(TARGET)
            self.assertEqual(result['device_id'],TARGET);self.assertEqual(result['delivery'],'original_204_then_actual_GET')
            receipt=(actor.root/'revoke-receipt.json').read_bytes()
            actor.request=Mock(return_value=self.listing(True));again=actor.revoke(TARGET)
            self.assertEqual(again,result);self.assertEqual((actor.root/'revoke-receipt.json').read_bytes(),receipt)
            self.assertEqual([call.args[0] for call in actor.request.call_args_list],['GET'])

    def test_unknown_delete_is_reconciled_by_actual_get_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            actor=self.controller(directory);actor.request=Mock(side_effect=[self.listing(False),TimeoutError(),self.listing(True)])
            result=actor.revoke(TARGET)
            self.assertEqual(result['delivery'],'unknown_response_then_actual_GET')
            self.assertEqual([call.args[0] for call in actor.request.call_args_list],['GET','DELETE','GET'])
            intent=json.loads((actor.root/'revoke-intent.json').read_bytes())
            self.assertEqual(intent['result'],'unknown');self.assertFalse(result['server_operation_id_available'])

    def test_an_unexecuted_unknown_intent_is_not_automatically_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            actor=self.controller(directory);actor.request=Mock(side_effect=[self.listing(False),TimeoutError(),self.listing(False)])
            with self.assertRaisesRegex(RuntimeError,'not confirmed'):actor.revoke(TARGET)
            actor.request=Mock(return_value=self.listing(False))
            with self.assertRaisesRegex(RuntimeError,'not confirmed'):actor.revoke(TARGET)
            self.assertEqual([call.args[0] for call in actor.request.call_args_list],['GET'])

    def test_foreign_or_setup_device_is_rejected_before_delete(self):
        for target in ('not-an-id',ACTOR,'f'*32):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                actor=self.controller(directory);actor.request=Mock(return_value=self.listing(False))
                with self.assertRaises(RuntimeError):actor.revoke(target)
                self.assertFalse(any(call.args[0]=='DELETE' for call in actor.request.call_args_list))

    def test_provider_revocation_changes_only_the_actual_owned_device_flag(self):
        before={'users':[{'devices':[{'id':TARGET,'revoked':0},{'id':ACTOR,'revoked':0}],'session_counts':[1,1]},{'devices':[],'session_counts':[]}]}
        after=deepcopy(before);after['users'][0]['devices'][0]['revoked']=1
        validate_revoked_accounts(before,after,TARGET,ACTOR)
        for change in ('other','sessions','duplicate'):
            wrong=deepcopy(after)
            if change=='other':wrong['users'][0]['devices'][1]['revoked']=1
            elif change=='sessions':wrong['users'][0]['session_counts']=[1,0]
            else:wrong['users'][0]['devices'].append(deepcopy(wrong['users'][0]['devices'][0]))
            with self.assertRaises(RuntimeError):validate_revoked_accounts(before,wrong,TARGET,ACTOR)

    def original(self):
        raw=b'original local note\r\n';digest=hashlib.sha256(raw).hexdigest()
        outbox=[dict(operation_id=OP,file_id=FILE,revision=1,path='runtime-note.md',hash=digest,operation='put',state='pending',payload_size=len(raw))]
        native=dict(binding={'id':'original binding','remote_vault':VAULT,'state':'active','cursor':0},
            association={'operation_id':'original association'},outbox=outbox,jobs=[],payloads=[],attempts=[],issuers=[],heads=[],inbox=[],conflicts=[])
        after=deepcopy(native);after['jobs']=[dict(operation_id=OP,file_id=FILE,path='runtime-note.md',hash=digest,size=len(raw),state='pending',remote_revision=None,error=None)]
        after['outbox'][0].update(state='queued',payload_size=0)
        after['payloads']=[dict(operation_id=OP,hash=digest,size=len(raw))]
        provider={'vault':{'id':VAULT,'sequence':0,'used':0},'rows':{key:[] for key in ('revisions','files','objects','uploads','device_progress')},
            'delivery':{'counts':{},'events':[]}}
        remote=deepcopy(provider);remote['delivery']={'counts':{'changes:401':1},'events':[{'action':'changes','service_status':401,'delivery_status':401,'fault_operation':None}]}
        return native,after,provider,remote,raw

    def test_real_rejection_keeps_original_outbox_bytes_cursor_and_provider_ledgers(self):
        before,after,provider,remote,raw=self.original()
        proof=validate_rejected_sync(before,after,provider,remote,raw,raw)
        self.assertEqual(proof['operation_id'],OP);self.assertEqual(proof['file_id'],FILE)
        for change in ('cursor','outbox','job','receipt','remote','bytes','fake_status'):
            wrong=deepcopy(after);p=deepcopy(remote);body=raw
            if change=='cursor':wrong['binding']['cursor']=1
            elif change=='outbox':wrong['outbox'][0]['operation_id']='replaced'
            elif change=='job':wrong['jobs'][0]['state']='acked'
            elif change=='receipt':wrong['attempts']=[{'outcome':'succeeded'}]
            elif change=='remote':p['rows']['revisions']=[{'sequence':1}]
            elif change=='bytes':body=b'changed'
            else:p['delivery']['events'][0]['service_status']=200
            with self.subTest(change=change),self.assertRaises(RuntimeError):validate_rejected_sync(before,wrong,provider,p,raw,body)

    def test_native_spooled_save_requires_the_original_payload_record_when_inline_bytes_are_empty(self):
        before,after,provider,remote,raw=self.original();before['outbox'][0]['payload_size']=0
        with self.assertRaises(RuntimeError):validate_rejected_sync(before,after,provider,remote,raw,raw)
        before['payloads']=deepcopy(after['payloads'])
        self.assertEqual(validate_rejected_sync(before,after,provider,remote,raw,raw)['operation_id'],OP)

    def test_association_archived_original_and_its_payload_are_preserved_beside_the_one_pending_operation(self):
        before,after,provider,remote,raw=self.original();archived='c4427c0c-463a-4217-b31a-4b96edbca904'
        before['outbox'][0]['payload_size']=0
        before['payloads']=deepcopy(after['payloads'])
        row=dict(before['outbox'][0],operation_id=archived,state='archived',payload_size=0)
        payload=dict(before['payloads'][0],operation_id=archived)
        for value in (before,after):
            value['outbox'].insert(0,deepcopy(row));value['payloads'].insert(0,deepcopy(payload))
        proof=validate_rejected_sync(before,after,provider,remote,raw,raw)
        self.assertEqual(proof['operation_id'],OP);self.assertEqual(proof['preserved_archived_operation_ids'],[archived])
        for change in ('archive_id','archive_state','archive_hash','lost_archive_payload','extra_archive','foreign_file'):
            left=deepcopy(before);right=deepcopy(after)
            if change=='archive_id':right['outbox'][0]['operation_id']='replaced'
            elif change=='archive_state':right['outbox'][0]['state']='acked'
            elif change=='archive_hash':left['outbox'][0]['hash']='f'*64
            elif change=='lost_archive_payload':right['payloads'].pop(0)
            elif change=='extra_archive':left['outbox'].append(deepcopy(row))
            else:left['outbox'][0]['file_id']='c4427c0c-463a-4217-b31a-4b96edbca905'
            with self.subTest(change=change),self.assertRaises(RuntimeError):validate_rejected_sync(left,right,provider,remote,raw,raw)

    def test_known_refresh_rejection_retains_exact_original_lineage_and_requires_native_401(self):
        login=dict(operation_id=OP,endpoint='owned endpoint',account='owned account',device_name='owned phone',kind='login',
            phase='active',device_id=TARGET,generation=1,server_logout_confirmed=0,previous_operation=None)
        selection=dict(revision=1,endpoint=login['endpoint'],account=login['account'],operation_id=OP,generation=1)
        before=dict(intents=[login],selection=selection,rejections=[])
        refresh={**login,'operation_id':'c4427c0c-463a-4217-b31a-4b96edbca903','generation':2,'kind':'refresh','phase':'unknown','previous_operation':OP}
        after=dict(intents=[login,refresh],selection={**selection,'revision':2,'operation_id':refresh['operation_id'],'generation':2},
            rejections=[dict(operation_id=refresh['operation_id'],status=401,code='SESSION_EXPIRED')])
        self.assertEqual(validate_rejected_refresh(before,after,TARGET),refresh['operation_id'])
        for field,value in [('phase','active'),('device_id',ACTOR),('previous_operation','changed'),('generation',3)]:
            wrong=deepcopy(after);wrong['intents'][1][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_rejected_refresh(before,wrong,TARGET)
        wrong=deepcopy(after);wrong['rejections'][0]['status']=200
        with self.assertRaises(RuntimeError):validate_rejected_refresh(before,wrong,TARGET)

    def test_original_driver_metadata_cannot_be_confused_with_native_IDs_or_export_extra_fields(self):
        import subprocess
        value=dict(driver_intent_id=OP,native_invocation_id_observed=False,action='Sync one cycle now')
        probe=SimpleNamespace(shell=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(value).encode(),b'')))
        self.assertEqual(original_driver_intent(probe,'cycle-revoked'),value)
        for changed in ({**value,'secret':'never-export'}, {**value,'native_invocation_id_observed':True}, {**value,'action':'Save'}):
            probe.shell.return_value=subprocess.CompletedProcess([],0,json.dumps(changed).encode(),b'')
            with self.assertRaises(RuntimeError):original_driver_intent(probe,'cycle-revoked')
        probe.shell.reset_mock()
        with self.assertRaises(RuntimeError):original_driver_intent(probe,'unrelated-phase')
        probe.shell.assert_not_called()

if __name__=='__main__':unittest.main()
