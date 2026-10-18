from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib,json,os,tempfile,unittest
from android_note_search_probe import PATH,CONTROL_PATH,PATHS,SEED,ORIGINAL,CONTROL,QUERIES,STEPS,digest,intent,seed_bindings,readonly_projection,snapshot

class OriginalNoteSearch(unittest.TestCase):
    def fixture(self):
        value={'drafts':[],'saves':[],'files':[],'outbox':[],'operations':[]}
        for i,(path,body) in enumerate([(PATH,SEED),(CONTROL_PATH,CONTROL)]):
            file_id=f'11111111-1111-4111-8111-{i:012d}';op=f'22222222-2222-4222-8222-{i:012d}'
            value['drafts'].append({'file_id':file_id,'path':path,'generation':2,'saved_generation':2,'content':body.decode(),'edit_session':'original'})
            value['saves'].append({'operation_id':op,'file_id':None,'path':path,'expected':'','content':body.decode(),'state':'committed','error':None})
            value['files'].append({'id':file_id,'path':path,'hash':digest(body),'revision':1,'deleted':0})
            value['outbox'].append({'operation_id':op,'file_id':file_id,'path':path,'hash':digest(body),'revision':1,'operation':'put','state':'pending'})
            value['operations'].append({'operation_id':op,'state':'committed','fingerprint':'a'*64,'result':json.dumps({'file_id':file_id,'path':path,'hash':digest(body),'revision':1,'deleted':False})})
        return value
    def test_null_original_creation_is_bound_by_actual_native_result(self):
        value=self.fixture();self.assertEqual(set(seed_bindings(value,{PATH:SEED,CONTROL_PATH:CONTROL})),set(PATHS))
        self.assertTrue(all(x['file_id'] is None for x in value['saves']))
    def test_creation_is_not_rewritten_to_match_changed_current_bytes(self):
        value=self.fixture();value['files'][0].update(hash=digest(ORIGINAL),revision=2)
        seed_bindings(value,{PATH:SEED,CONTROL_PATH:CONTROL})
        value['saves'][0]['content']=ORIGINAL.decode()
        with self.assertRaises(RuntimeError):seed_bindings(value,{PATH:SEED,CONTROL_PATH:CONTROL})
    def test_original_native_receipt_wrong_file_or_body_refuses(self):
        for field,wrong in [('file_id','99999999-9999-4999-8999-999999999999'),('hash','b'*64),('path',CONTROL_PATH),('revision',3)]:
            value=self.fixture();result=json.loads(value['operations'][0]['result']);result[field]=wrong;value['operations'][0]['result']=json.dumps(result)
            with self.assertRaises(RuntimeError):seed_bindings(value,{PATH:SEED,CONTROL_PATH:CONTROL})
    def test_duplicate_extra_or_uncommitted_save_refuses(self):
        for mutate in [lambda v:v['saves'].append(deepcopy(v['saves'][0])),lambda v:v['operations'][0].update(state='pending'),lambda v:v['saves'][0].update(expected='old'),lambda v:v['outbox'][0].update(operation='delete')]:
            value=self.fixture();mutate(value)
            with self.assertRaises(RuntimeError):seed_bindings(value,{PATH:SEED,CONTROL_PATH:CONTROL})
    def test_read_projection_keeps_original_ids_and_all_outbox_rows(self):
        value=self.fixture();after=deepcopy(value);after['drafts'][0]['edit_session']='cold-new-original'
        self.assertEqual(readonly_projection(value),readonly_projection(after))
        after['outbox'].append(deepcopy(after['outbox'][0]));self.assertNotEqual(readonly_projection(value),readonly_projection(after))
    def test_note_payload_and_control_discriminate_literal_not_wildcard_queries(self):
        self.assertEqual(ORIGINAL.replace(b'\r\n',b'\n'),SEED)
        self.assertNotIn(b'\n',ORIGINAL.replace(b'\r\n',b''))
        for query in QUERIES[:3]:
            self.assertIn(query,SEED.decode());self.assertNotIn(query,CONTROL.decode())
        self.assertEqual(QUERIES[1:3],('%_','.*'));self.assertIn('😀',PATH)
    def test_local_or_phone_environment_refuses_before_writing_intent(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True):
            probe=SimpleNamespace(output=Path(d),args=SimpleNamespace(scope='note-search',serial='emulator-5562'))
            with self.assertRaises(RuntimeError):intent(probe,'search-browse')
            self.assertFalse(list(Path(d).iterdir()))
    def test_original_intent_cannot_overwrite_or_replay(self):
        with tempfile.TemporaryDirectory() as d,patch('android_note_search_probe.require_hosted_emulator'),patch.dict(os.environ,GITHUB_RUN_ID='123'):
            probe=SimpleNamespace(output=Path(d),args=SimpleNamespace(scope='note-search',serial='emulator-5562',tools_commit='a'*40,commit='b'*40))
            intent(probe,'search-query-1')
            with self.assertRaises(FileExistsError):intent(probe,'search-query-1')
            value=json.loads((Path(d)/'search-query-1-original-intent.json').read_text('utf-8'))
            self.assertFalse(value['automatic_retry']);self.assertFalse(value['native_operation_ID_assigned_by_driver'])
    def test_unknown_phase_refuses_before_snapshot_or_intent(self):
        with tempfile.TemporaryDirectory() as d,patch('android_note_search_probe.require_hosted_emulator'):
            probe=SimpleNamespace(output=Path(d),args=SimpleNamespace(scope='note-search',serial='emulator-5562'))
            with self.assertRaises(RuntimeError):intent(probe,'../other')
            with self.assertRaises(RuntimeError):snapshot(probe,'unrelated')
            self.assertFalse(list(Path(d).iterdir()))

if __name__=='__main__':unittest.main()
