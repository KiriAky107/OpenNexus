from copy import deepcopy
import hashlib,json,unittest,uuid
from types import SimpleNamespace
from unittest.mock import patch
from android_sync_conflict import LOCAL,DIRTY,REMOTE,COPY,IDS,ConflictMixin,validate_retained_conflict,validate_original_copy


class ConflictOriginals(unittest.TestCase):
    def conflict(self):
        original={'operation_id':'uploaded-original','file_id':'original-file','path':'runtime-note.md','state':'acked','remote_revision':1}
        first={'jobs':[original],'heads':[{'file_id':'original-file','revision':1,'path':'runtime-note.md','hash':'base'}],
            'attempts':[{'original':'attempt'}],'issuers':[{'device_id':'android'}],'association':{'frozen':'original'}}
        receipt={'vault_id':'1'*32,'sequence':2,'base_revision':1,'operation_id':'peer-original','file_id':'original-file',
            'path':'runtime-note.md','hash':hashlib.sha256(REMOTE).hexdigest(),'size':len(REMOTE),'device_id':'peer','operation':'put','fingerprint':'3'*64}
        native={**deepcopy(first),'binding':{'cursor':2,'remote_vault':'1'*32},'decisions':[],
            'conflicts':[{'sequence':2,'file_id':'original-file','local_path':'runtime-note.md','local_hash':hashlib.sha256(LOCAL).hexdigest(),'state':'open'}],
            'heads':[{'file_id':'original-file','revision':2,'path':'runtime-note.md','hash':receipt['hash']}],
            'inbox':[{'sequence':2,'revision':json.dumps(receipt),'state':'conflict'}]}
        native['jobs'].append({'operation_id':'saved-local-original','file_id':'original-file','path':'runtime-note.md',
            'hash':hashlib.sha256(LOCAL).hexdigest(),'size':len(LOCAL),'state':'conflict'})
        provider={'vault':{'sequence':2,'id':'1'*32},'rows':{'revisions':[{'original':'upload'},{k:v for k,v in receipt.items() if k!='vault_id'}]}}
        return first,native,provider,receipt

    def test_conflict_retains_saved_local_job_original_remote_head_and_distinct_incoming(self):
        first,native,provider,receipt=self.conflict()
        self.assertEqual(validate_retained_conflict(first,native,provider,receipt)['operation_id'],'saved-local-original')
        for key,value in (('file_id','replacement'),('base_revision',0),('device_id','android'),('hash','0'*64),('size',0)):
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                validate_retained_conflict(first,native,provider,dict(receipt,**{key:value}))

    def test_a_generated_remote_page_or_silently_accepted_local_job_is_not_a_conflict(self):
        first,native,provider,receipt=self.conflict()
        for field,value in (('heads',first['heads']),('inbox',[]),('decisions',[{'unexpected':'approval'}]),('conflicts',[])):
            changed=deepcopy(native);changed[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)
        changed=deepcopy(native);changed['jobs'][1]['state']='acked'
        with self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)
        changed=deepcopy(native);changed['jobs'][1]['hash']=hashlib.sha256(DIRTY).hexdigest()
        with self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)

    def resolved(self):
        first,native,provider,receipt=self.conflict()
        frozen={'sequence':2,'choice':'copy','destination':COPY,'fingerprint':'4'*64}
        decision={**frozen,'expected_hash':hashlib.sha256(LOCAL).hexdigest(),'state':'completed',**{key:str(uuid.uuid4()) for key in IDS}}
        native['decisions']=[decision];native['conflicts'][0]['state']='resolved'
        return native,frozen,receipt

    def test_original_copy_requires_all_five_native_ids_and_exact_frozen_content(self):
        native,frozen,receipt=self.resolved();value=validate_original_copy(native,frozen,receipt)
        self.assertEqual(len({value[key] for key in IDS}),5)
        for key,new in (('choice','remote'),('destination','replacement.md'),('expected_hash',hashlib.sha256(DIRTY).hexdigest()),
                ('fingerprint','5'*64),('state','pending'),('sequence',3),('copy_id','not-an-id')):
            changed=deepcopy(native);changed['decisions'][0][key]=new
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)
        changed=deepcopy(native);changed['decisions'][0]['copy_id']=changed['decisions'][0]['operation_id']
        with self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)

    def test_confirmed_copy_requires_original_resolved_head_not_only_a_completed_receipt(self):
        native,frozen,receipt=self.resolved()
        for field,value in (('heads',[]),('conflicts',[]),('binding',{'cursor':1})):
            changed=deepcopy(native);changed[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)
        changed=deepcopy(native);changed['heads'][0]['hash']=hashlib.sha256(LOCAL).hexdigest()
        with self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)

    def test_unowned_device_and_unrelated_driver_record_are_refused_before_shell_or_UI(self):
        probe=ConflictMixin();probe.args=SimpleNamespace(serial='physical-phone')
        with patch.object(ConflictMixin,'host_snapshot',create=True) as snapshot:
            with self.assertRaises(RuntimeError):probe.conflict_copy(None,None,None)
            snapshot.assert_not_called()
        with patch.object(ConflictMixin,'shell',create=True) as shell:
            with self.assertRaises(RuntimeError):probe.original_conflict_driver('unrelated-private-input')
            shell.assert_not_called()


if __name__=='__main__':unittest.main()
