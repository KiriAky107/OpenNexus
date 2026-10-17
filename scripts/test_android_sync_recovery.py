from copy import deepcopy
import hashlib,json,unittest
from unittest.mock import patch
from android_runtime_probe import DRAFT
from android_sync_recovery import network_observation,validate_offline_original,validate_unknown_original,validate_reconciled_original,RecoveryMixin


class RecoveryOriginals(unittest.TestCase):
    def original(self):
        return {'operation_id':'original-op','file_id':'original-file','revision':1,'path':'runtime-note.md',
            'hash':hashlib.sha256(DRAFT.encode()).hexdigest(),'operation':'put','state':'pending','payload_size':0}

    def offline(self):
        native={'binding':{'id':'binding','remote_vault':'1'*32,'cursor':0},'jobs':[],'attempts':[],
            'issuers':[],'heads':[],'inbox':[],'conflicts':[],'association':{'frozen':'original'},'outbox':[self.original()]}
        provider={'vault':{'id':'1'*32,'sequence':0},'rows':{'revisions':[],'files':[],'objects':[],'uploads':[],
            'device_progress':[]},'delivery':{'counts':{},'events':[]}}
        return native,provider

    def unknown(self):
        native,provider=self.offline();original=self.original()
        receipt={'vault_id':'1'*32,'sequence':1,'base_revision':0,'operation_id':original['operation_id'],
            'file_id':original['file_id'],'path':original['path'],'hash':original['hash'],'size':len(DRAFT.encode()),
            'operation':'put','device_id':'android','fingerprint':'2'*64}
        native['jobs']=[{k:receipt[k] for k in ('operation_id','file_id','path','hash','size')}|{'state':'committing','remote_revision':None,'error':None}]
        native['attempts']=[{'operation_id':original['operation_id'],'attempts':1,'outcome':'failed','error':'SERVICE_UNAVAILABLE'}]
        native['issuers']=[{'operation_id':original['operation_id'],'device_id':'android'}]
        native['outbox'][0]['state']='queued'
        provider['vault']['sequence']=1;provider['rows']['revisions']=[receipt]
        provider['rows']['files']=[{'file_id':original['file_id'],'sequence':1,'path_key':original['path'],'deleted':0}]
        provider['rows']['objects']=[{'hash':receipt['hash'],'size':receipt['size']}]
        control={'operation_id':'original-control','result_revision':1}
        marker={'operation_id':'original-control','mode':'drop_revision','revision':1,'action':'revision',
            'service_status':200,'service_response_complete':True,'raw_body_recorded':False}
        provider['delivery']={'counts':{'revision:200':1},'events':[{'action':'revision','service_status':200,
            'delivery_status':503,'fault_operation':'original-control'}]}
        return original,native,provider,control,marker,receipt

    def test_actual_offline_requires_default_network_none_and_all_original_settings(self):
        settings={'airplane_mode_on':'1','wifi_on':'0','mobile_data':'0'}
        self.assertFalse(network_observation(settings,'  Active default network: none\n')['default_network_present'])
        self.assertTrue(network_observation(settings,'Active default network: 101\n')['default_network_present'])
        for text in ('','Active default network: unknown','Active default network: none\nActive default network: 100'):
            with self.subTest(text=text),self.assertRaises(RuntimeError):network_observation(settings,text)
        for key in settings:
            with self.subTest(key=key),self.assertRaises(RuntimeError):network_observation(dict(settings,**{key:'null'}),'Active default network: none')

    def test_offline_observation_never_accepts_a_reached_service_or_changed_original(self):
        native,provider=self.offline()
        self.assertEqual(validate_offline_original(native,deepcopy(native),provider,deepcopy(provider)),self.original())
        changed=deepcopy(provider);changed['delivery']['counts']['changes:200']=1
        with self.assertRaises(RuntimeError):validate_offline_original(native,native,provider,changed)
        changed=deepcopy(native);changed['outbox'][0]['operation_id']='replacement'
        with self.assertRaises(RuntimeError):validate_offline_original(native,changed,provider,provider)
        changed=deepcopy(native);changed['jobs']=[{'unexpected':'capture'}]
        with self.assertRaises(RuntimeError):validate_offline_original(changed,changed,provider,provider)

    def test_unknown_commit_matches_original_body_native_issuer_and_actual_dropped_success(self):
        original,native,provider,control,marker,receipt=self.unknown()
        self.assertEqual(validate_unknown_original(original,native,provider,'android',control,marker),receipt)
        for field,value in (('operation_id','replacement'),('device_id','peer'),('base_revision',1),('hash','0'*64),('size',0)):
            changed=deepcopy(provider);changed['rows']['revisions'][0][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_unknown_original(original,native,changed,'android',control,marker)
        for field,value in (('service_status',503),('service_response_complete',False),('operation_id','other'),('raw_body_recorded',True)):
            with self.subTest(marker=field),self.assertRaises(RuntimeError):validate_unknown_original(original,native,provider,'android',control,dict(marker,**{field:value}))

    def test_unknown_original_cannot_hide_duplicate_attempt_or_early_ack(self):
        original,native,provider,control,marker,receipt=self.unknown()
        for table,key,value in (('jobs','state','acked'),('attempts','outcome','succeeded'),('attempts','attempts',2),('issuers','device_id','other')):
            changed=deepcopy(native);changed[table][0][key]=value
            with self.subTest(table=table,key=key),self.assertRaises(RuntimeError):validate_unknown_original(original,changed,provider,'android',control,marker)
        changed=deepcopy(provider);changed['delivery']['events']*=2
        with self.assertRaises(RuntimeError):validate_unknown_original(original,native,changed,'android',control,marker)
        for table in ('objects','files','uploads'):
            changed=deepcopy(provider);changed['rows'][table].append({'unexpected':'write'})
            with self.subTest(table=table),self.assertRaises(RuntimeError):validate_unknown_original(original,native,changed,'android',control,marker)

    def reconciled(self):
        original,before,provider,control,marker,receipt=self.unknown()
        native=deepcopy(before);native['binding']['cursor']=1
        native['jobs'][0].update(state='acked',remote_revision=1,error=None)
        native['heads']=[{'file_id':receipt['file_id'],'revision':1,'path':receipt['path'],'hash':receipt['hash']}]
        native['inbox']=[{'sequence':1,'revision':json.dumps({k:v for k,v in receipt.items() if k not in ('device_id','fingerprint')}),'state':'applied'}]
        native['outbox'][0]['state']='acked'
        after=deepcopy(provider);after['delivery']['counts']['changes:200']=1
        return before,native,provider,after,receipt

    def test_read_recovery_confirms_native_original_without_rewriting_failed_attempt_history(self):
        before,native,provider,after,receipt=self.reconciled()
        validate_reconciled_original(before,native,provider,after,receipt)
        for key in ('attempts','issuers','association','heads','inbox'):
            changed=deepcopy(native);changed[key]=[]
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_reconciled_original(before,changed,provider,after,receipt)
        changed=deepcopy(native);changed['attempts'][0]['outcome']='succeeded'
        with self.assertRaises(RuntimeError):validate_reconciled_original(before,changed,provider,after,receipt)
        changed=deepcopy(native);changed['outbox'][0]['operation_id']='replacement'
        with self.assertRaises(RuntimeError):validate_reconciled_original(before,changed,provider,after,receipt)

    def test_read_recovery_rejects_another_revision_POST_or_wrong_native_original_bytes(self):
        before,native,provider,after,receipt=self.reconciled()
        changed=deepcopy(after);changed['delivery']['counts']['revision:200']=2
        with self.assertRaises(RuntimeError):validate_reconciled_original(before,native,provider,changed,receipt)
        changed=deepcopy(native);value=json.loads(changed['inbox'][0]['revision']);value['operation_id']='other'
        changed['inbox'][0]['revision']=json.dumps(value)
        with self.assertRaises(RuntimeError):validate_reconciled_original(before,changed,provider,after,receipt)

    def test_recovery_refuses_local_or_physical_network_before_any_device_access(self):
        probe=RecoveryMixin();probe.args=type('Args',(),{'serial':'physical-phone'})()
        with patch.object(RecoveryMixin,'sync_phase',create=True) as phase:
            with self.assertRaises(RuntimeError):probe.recover_original(None,None,None)
            phase.assert_not_called()


if __name__=='__main__':unittest.main()
