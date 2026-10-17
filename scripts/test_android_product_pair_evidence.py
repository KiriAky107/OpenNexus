"""Synthetic transfer/source boundaries; no client, session or provider starts."""
from copy import deepcopy
import json
import unittest
import uuid

from android_desktop_handoff import HandoffError
from android_product_pair_data import ANDROID_CANVAS_TEXT,ANDROID_POSITION,CANVAS_SEED,DESKTOP_CANVAS_TEXT,DESKTOP_POSITION,TEXT_NODE
from android_product_pair_evidence import desktop_cold_files,desktop_outbound_files,match_provider_files,sha,validate_canvas_edit
from android_product_pair_stages import IMAGE_PATH


class OriginalProductEvidence(unittest.TestCase):
    def setUp(self):
        self.identity={'binding_id':str(uuid.uuid4()),'remote_vault_id':'e'*32,'current_device_id':'d'*32}
        self.bodies={'runtime-note.md':b'actual owned note\n','peer.canvas':b'{"nodes":[]}',IMAGE_PATH:b'original image'}
        self.native={'integrity':'ok','binding':{'id':self.identity['binding_id'],'remote_vault':'e'*32,'state':'active','cursor':4},
                     'jobs':[],'heads':[],'attempts':[],'inbox':[],'conflicts':[]}
        for seq,(path,body) in enumerate(self.bodies.items(),1):
            file,op=str(uuid.uuid4()),str(uuid.uuid4())
            self.native['jobs'].append({'file_id':file,'operation_id':op,'path':path,'hash':sha(body),'size':len(body),
                'operation':'put','state':'acked','base_revision':0,'remote_revision':seq,'error':None})
            self.native['heads'].append({'file_id':file,'path':path,'hash':sha(body),'revision':seq})
            self.native['attempts'].append({'operation_id':op,'outcome':'succeeded','attempts':1,'error':None})
            self.native['inbox'].append({'sequence':seq,'state':'applied'})

    def test_original_native_ack_file_bytes_and_attempts_are_required_while_other_preferences_are_allowed(self):
        files=desktop_outbound_files(self.native,self.identity,self.bodies)
        self.native['jobs'].append(dict(self.native['jobs'][0],path='.opennexus/preferences.json',operation_id=str(uuid.uuid4())))
        self.assertEqual(desktop_outbound_files(self.native,self.identity,self.bodies),files)
        for field,value in [('state','committing'),('hash','0'*64),('base_revision',True),('remote_revision',99),('error','SYNC_TEST_FAILURE')]:
            changed=deepcopy(self.native);changed['jobs'][0][field]=value
            with self.subTest(field=field),self.assertRaises(HandoffError):desktop_outbound_files(changed,self.identity,self.bodies)
        changed=deepcopy(self.native);changed['attempts'][0]['outcome']='pending'
        with self.assertRaises(HandoffError):desktop_outbound_files(changed,self.identity,self.bodies)

    def test_cold_reopen_requires_original_native_apply_without_reupload_or_replacing_unchanged_image(self):
        files=desktop_outbound_files(self.native,self.identity,self.bodies)
        self.assertEqual(desktop_cold_files(self.native,self.identity,self.bodies,self.native,files),files)
        for kind in ('reupload','not-applied','different-file','changed-image'):
            changed=deepcopy(self.native);bodies=dict(self.bodies)
            if kind=='reupload':changed['jobs'].append(dict(changed['jobs'][0],operation_id=str(uuid.uuid4())))
            elif kind=='not-applied':changed['inbox'][0]['state']='received'
            elif kind=='different-file':changed['heads'][0]['file_id']=str(uuid.uuid4())
            else:bodies[IMAGE_PATH]=b'reencoded image'
            with self.subTest(kind=kind),self.assertRaises(HandoffError):desktop_cold_files(changed,self.identity,bodies,self.native,files)

    def test_Canvas_only_changes_selected_text_and_coordinates_and_preserves_all_unknown_fields(self):
        desktop=json.loads(CANVAS_SEED);desktop['nodes'][0].update(text=DESKTOP_CANVAS_TEXT,x=DESKTOP_POSITION[0],y=DESKTOP_POSITION[1])
        raw=json.dumps(desktop,ensure_ascii=False,indent=2).encode()
        proof=validate_canvas_edit(CANVAS_SEED,raw,DESKTOP_CANVAS_TEXT,DESKTOP_POSITION)
        self.assertTrue(proof['unknown_fields_and_other_nodes_preserved']);self.assertFalse(proof['byte_identical_claimed_for_edited_JSON'])
        android=deepcopy(desktop);android['nodes'][0].update(text=ANDROID_CANVAS_TEXT,x=ANDROID_POSITION[0],y=ANDROID_POSITION[1])
        validate_canvas_edit(raw,json.dumps(android).encode(),ANDROID_CANVAS_TEXT,ANDROID_POSITION)
        for changed in (dict(android,vendor={}),{**android,'nodes':android['nodes'][:1]}):
            with self.assertRaisesRegex(HandoffError,'UNKNOWN_FIELDS_OR_OTHER_NODES_CHANGED'):
                validate_canvas_edit(raw,json.dumps(changed).encode(),ANDROID_CANVAS_TEXT,ANDROID_POSITION)

    def test_provider_must_match_original_issuer_operation_CAS_bytes_and_current_head(self):
        files=desktop_outbound_files(self.native,self.identity,self.bodies)
        provider={'vault':{'id':'e'*32},'secrets_selected':False,'object_IO':False,'rows':{'revisions':[],'files':[]}}
        for row in files:
            provider['rows']['revisions'].append({**{k:v for k,v in row.items() if k not in ('sha256','bytes')},
                                                'hash':row['sha256'],'size':row['bytes'],'operation':'put'})
            provider['rows']['files'].append({'file_id':row['file_id'],'sequence':row['sequence'],'deleted':0,'path_key':row['path'].casefold()})
        match_provider_files(files,provider,'e'*32)
        for field,value in [('operation_id',str(uuid.uuid4())),('device_id','0'*32),('base_revision',999),('hash','0'*64)]:
            changed=deepcopy(provider);changed['rows']['revisions'][0][field]=value
            with self.subTest(field=field),self.assertRaises(HandoffError):match_provider_files(files,changed,'e'*32)


if __name__=='__main__':unittest.main()
