"""Synthetic stage receipts check the contract, not any actual paired client."""
from copy import deepcopy
import json
import unittest
import uuid

from android_desktop_exchange import exchange_bytes,read_exchange
from android_desktop_handoff import HandoffError
from android_product_pair_stages import ACTIONS,DESKTOP_INSTALLER,IMAGE_PATH,PATHS,STAGES,validate_stage,validate_return,validate_final
from android_product_pair_clients import CLIENTS,fixed_android_client
import test_android_desktop_exchange as fixtures


class PairStages(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Exchange();self.fixture.setUp();self.context=self.fixture.context
        self.context['android_commit']=CLIENTS[0].source
        self.desktop=self.body('desktop-first')
        self.android=self.body('android-return')
        self.android['files']=deepcopy(self.desktop['files'])
        for sequence,path in enumerate(('runtime-note.md','peer.canvas'),9):
            row=next(x for x in self.android['files'] if x['path']==path)
            row.update(device_id=self.android['identity']['current_device_id'],operation_id=str(uuid.uuid4()),
                       base_revision=row['sequence'],sequence=sequence,sha256='d'*64)
        self.final=deepcopy(self.desktop);self.final['actions']=ACTIONS['desktop-final'];self.final['files']=deepcopy(self.android['files'])

    def body(self,stage):
        android=stage=='android-return';device=('0' if android else '1')*32
        return {'passed':True,'client_source':self.context['android_commit' if android else 'desktop_commit'],
                'client_artifact_sha256':fixed_android_client(self.context['android_commit']).apk_sha256 if android else DESKTOP_INSTALLER,'native_metadata_sha256':'b'*64,
                'identity':{'local_vault_id':str(uuid.uuid4()),'binding_id':str(uuid.uuid4()),
                            'remote_vault_id':'f'*32,'current_device_id':device},
                'files':[{'path':path,'file_id':str(uuid.uuid4()),'operation_id':str(uuid.uuid4()),'device_id':device,
                          'sequence':index+2,'base_revision':0,'sha256':'c'*64,'bytes':99} for index,path in enumerate(sorted(PATHS))],
                'actions':ACTIONS[stage],'actual_client_closed':True,'owned_state_preserved':True}

    def test_public_three_stage_roundtrip_keeps_same_originals_and_named_job_provenance(self):
        validate_return(self.context,self.desktop,self.android);validate_final(self.context,self.desktop,self.android,self.final)
        for stage,body in [('desktop-first',self.desktop),('android-return',self.android),('desktop-final',self.final)]:
            producer,step=STAGES[stage];jobs=deepcopy(self.fixture.jobs)
            job=next(x for x in jobs if x['name']==producer)
            job['steps'].append(dict(job['steps'][0],name=step))
            metadata,raw=self.fixture.archive(stage,exchange_bytes(self.context,stage,body))
            received,proof=read_exchange(self.context,stage,self.fixture.run,jobs,metadata,raw)
            self.assertEqual(received,body);self.assertEqual(proof['original_upload_step'],step)
            self.assertNotIn('password',json.dumps(received));self.assertNotIn('token',json.dumps(received))

    def test_extra_credentials_paths_wrong_source_and_nonboolean_claims_refuse(self):
        changes=[]
        for key,value in [('password','DO_NOT_EXPORT'),('client_source','9'*40),('passed',1),('actual_client_closed',False)]:
            changes.append(dict(self.desktop,**{key:value}))
        altered=deepcopy(self.desktop);altered['files'][0]['access_token']='DO_NOT_EXPORT';changes.append(altered)
        altered=deepcopy(self.desktop);altered['files'][0]['path']='../user-data.md';changes.append(altered)
        altered=deepcopy(self.desktop);altered['files'][0]['path']=[];changes.append(altered)
        altered=deepcopy(self.desktop);altered['actions']=dict(altered['actions'],source_edit_and_save=1);changes.append(altered)
        for body in changes:
            with self.subTest(change=body.keys()),self.assertRaises(HandoffError):validate_stage(self.context,'desktop-first',body)

    def test_android_return_requires_actual_Markdown_and_Canvas_edits_same_bases_and_original_PNG(self):
        for key in ('local_vault_id','binding_id','current_device_id'):
            altered=deepcopy(self.android);altered['identity'][key]=self.desktop['identity'][key]
            with self.subTest(key=key),self.assertRaises(HandoffError):validate_return(self.context,self.desktop,altered)
        for path,key,value in [('runtime-note.md','base_revision',0),('runtime-note.md','file_id',str(uuid.uuid4())),
                              (IMAGE_PATH,'sha256','e'*64),('peer.canvas','base_revision',0)]:
            altered=deepcopy(self.android);next(x for x in altered['files'] if x['path']==path)[key]=value
            with self.subTest(path=path,key=key),self.assertRaises(HandoffError):validate_return(self.context,self.desktop,altered)
        altered=deepcopy(self.android)
        altered['files']=[deepcopy(next(x for x in self.desktop['files'] if x['path']==r['path'])) if r['path']=='peer.canvas' else r for r in altered['files']]
        with self.assertRaisesRegex(HandoffError,'PAIR_STAGE_RETURN_ORIGINAL_INVALID'):validate_return(self.context,self.desktop,altered)

    def test_final_cold_desktop_cannot_substitute_new_device_binding_or_return_originals(self):
        for key,value in [('current_device_id','5'*32),('binding_id',str(uuid.uuid4())),('local_vault_id',str(uuid.uuid4()))]:
            altered=deepcopy(self.final);altered['identity'][key]=value
            with self.subTest(key=key),self.assertRaises(HandoffError):validate_final(self.context,self.desktop,self.android,altered)
        altered=deepcopy(self.final);next(x for x in altered['files'] if x['path']=='runtime-note.md')['operation_id']=str(uuid.uuid4())
        with self.assertRaises(HandoffError):validate_final(self.context,self.desktop,self.android,altered)

    def test_android_stage_cannot_substitute_an_old_APK_or_the_separately_signed_phone_derivative(self):
        for candidate in ('919a5e1512d1a77b70b0398b64022ccdd6613c920b3373f75476bd5167a85ee1',
                          '0cc4d68b946dc302e273aedc1c5e29f3efc431bd1a46f6b9befc955c4ebc1729','a'*64):
            changed=deepcopy(self.android);changed['client_artifact_sha256']=candidate
            with self.subTest(candidate=candidate),self.assertRaisesRegex(HandoffError,'PAIR_STAGE_CLIENT_INVALID'):
                validate_stage(self.context,'android-return',changed)

    def test_each_compiled_client_requires_its_own_actual_APK_in_return_receipt(self):
        for client in CLIENTS:
            self.context['android_commit']=client.source
            original=deepcopy(self.android)
            original.update(client_source=client.source,client_artifact_sha256=client.apk_sha256)
            validate_stage(self.context,'android-return',original)
            for other in CLIENTS:
                if other==client:continue
                changed=dict(original,client_artifact_sha256=other.apk_sha256)
                with self.assertRaisesRegex(HandoffError,'PAIR_STAGE_CLIENT_INVALID'):
                    validate_stage(self.context,'android-return',changed)


if __name__=='__main__':unittest.main(verbosity=2)
