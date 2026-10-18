import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from android_canvas_graph_probe import (PATH,FIXTURE,STEPS,A,B,E,graph,saved_original,projection,run_canvas_graph)

class GraphNativeSave(unittest.TestCase):
    def setUp(self):
        self.raw=FIXTURE;self.file='d58f7b80-8bf1-42da-a619-f1e08c70c301';self.op='e58f7b80-8bf1-42da-a619-f1e08c70c301'
        sha=hashlib.sha256(self.raw).hexdigest()
        self.saved={'saves':[dict(operation_id=self.op,state='committed',error=None,path=PATH,content=self.raw.decode(),file_id=None,expected='')],
            'files':[dict(id=self.file,path=PATH,hash=sha,revision=1,deleted=0)],
            'drafts':[dict(file_id=self.file,content=self.raw.decode(),generation=1,saved_generation=1)],
            'operations':[dict(operation_id=self.op,state='committed',fingerprint='a'*64,result=json.dumps(dict(file_id=self.file,path=PATH,hash=sha,revision=1,deleted=False)))],
            'outbox':[dict(operation_id=self.op,file_id=self.file,path=PATH,hash=sha,revision=1,operation='put',state='pending')]}
    def test_creation_retains_NULL_intent_and_binds_native_allocated_identity(self):
        result=saved_original(None,self.saved,self.raw);self.assertEqual(result['file_id'],self.file);self.assertIsNone(self.saved['saves'][0]['file_id'])
    def test_changed_receipt_or_duplicate_operation_does_not_count_as_save(self):
        for key in ('operations','outbox'):
            value=copy.deepcopy(self.saved);value[key].append(copy.deepcopy(value[key][0]))
            with self.assertRaises(RuntimeError):saved_original(None,value,self.raw)
        value=copy.deepcopy(self.saved);value['operations'][0]['result']=json.dumps(dict(file_id='foreign',path=PATH,hash='a'*64,revision=1,deleted=False))
        with self.assertRaises(RuntimeError):saved_original(None,value,self.raw)
    def test_pending_draft_or_wrong_bytes_cannot_pass(self):
        for change in ('content','saved_generation'):
            value=copy.deepcopy(self.saved);value['drafts'][0][change]='wrong' if change=='content' else 0
            with self.assertRaises(RuntimeError):saved_original(None,value,self.raw)
    def update(self):
        after=copy.deepcopy(self.saved);op='e58f7b80-8bf1-42da-a619-f1e08c70c302'
        after['files'][0]['revision']=2
        after['saves'].append(dict(after['saves'][0],operation_id=op,expected=after['files'][0]['hash'],file_id=self.file))
        receipt=dict(after['operations'][0],operation_id=op,fingerprint='b'*64)
        result=json.loads(receipt['result']);result['revision']=2;receipt['result']=json.dumps(result)
        after['operations'].append(receipt);after['outbox'].append(dict(after['outbox'][0],operation_id=op,revision=2))
        return after
    def test_new_save_is_bound_to_same_file_CAS_receipt_and_single_new_outbox(self):
        after=self.update();self.assertEqual(saved_original(self.saved,after,self.raw)['file_id'],self.file)
    def test_old_intent_or_CAS_must_remain_exact(self):
        for key,value in [('expected','wrong'),('file_id',None)]:
            after=self.update();after['saves'][-1][key]=value
            with self.assertRaises(RuntimeError):saved_original(self.saved,after,self.raw)
        after=self.update();after['saves'][0]['content']='changed original'
        with self.assertRaises(RuntimeError):saved_original(self.saved,after,self.raw)
    def test_read_projection_excludes_only_mutable_draft_session(self):
        other=copy.deepcopy(self.saved);other['drafts'][0]['edit_session']='new'
        self.assertEqual(projection(self.saved),projection(other))
        other['outbox'][0]['state']='changed';self.assertNotEqual(projection(self.saved),projection(other))
    def test_seed_is_explicitly_synthetic_with_original_unknown_fields(self):
        value=graph(FIXTURE);self.assertEqual([x['id'] for x in value['nodes']],[A,B]);self.assertEqual(value['edges'][0]['id'],E)
        self.assertTrue(all('vendor' in x for x in [*value['nodes'],*value['edges']]));self.assertIn('vendor-root',value)
    def test_fixed_steps_are_bounded_and_do_not_replay_old_passed_scopes(self):
        self.assertEqual(len(STEPS),len(set(STEPS)));self.assertEqual(len(STEPS),19)
        self.assertEqual(STEPS[0],'graph-seed');self.assertEqual(STEPS[-1],'graph-cold')
        self.assertFalse(any('file-actions' in x or 'canvas-drag'==x or 'canvas-image'==x for x in STEPS))
    def test_nonhosted_phone_refuses_before_original_intent_or_action(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            probe=SimpleNamespace(args=SimpleNamespace(serial='owned-physical',scope='canvas-graph'),output=Path(folder))
            with self.assertRaises(RuntimeError):run_canvas_graph(probe)
            self.assertFalse(list(Path(folder).iterdir()))

if __name__=='__main__':unittest.main()
