"""Synthetic contracts only; no ptrace, native build, device or user file access."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from android_atomic_probe import APP,NOTE,ORIGINAL,UPDATED,digest,start_time,target_path,decode_event,event,pending,recovered,run_atomic

class AtomicContracts(unittest.TestCase):
    def fixture(self):
        file_id='11111111-1111-4111-8111-111111111111';old='22222222-2222-4222-8222-222222222222';new='33333333-3333-4333-8333-333333333333'
        saved=dict(operation_id=old,state='committed',file_id=None,path=NOTE,content=ORIGINAL.decode(),expected='',generation=1,error=None,draft_id='own')
        original_result=dict(file_id=file_id,path=NOTE,hash=digest(ORIGINAL),revision=1,deleted=False)
        before={'saves':[saved],'files':[dict(id=file_id,path=NOTE,hash=digest(ORIGINAL),revision=1,deleted=0)],
            'drafts':[dict(id='own',file_id=file_id,content=ORIGINAL.decode(),generation=1,saved_generation=1)],
            'outbox':[dict(operation_id=old,file_id=file_id,path=NOTE,hash=digest(ORIGINAL),revision=1,operation='put',state='pending')],
            'operations':[dict(operation_id=old,fingerprint='a'*64,state='committed',result=json.dumps(original_result))],
            'journal':[],'payloads':[dict(operation_id=old,hash=digest(ORIGINAL),size=len(ORIGINAL))]}
        held=deepcopy(before);held['saves'].append(dict(saved,operation_id=new,state='prepared',file_id=file_id,content=UPDATED.decode(),expected=digest(ORIGINAL),generation=2))
        held['drafts'][0].update(content=UPDATED.decode(),generation=2)
        held['operations'].append(dict(operation_id=new,fingerprint='b'*64,state='pending',result=None))
        held['journal'].append(dict(operation_id=new,file_id=file_id,path=NOTE,expected=digest(ORIGINAL),state='pending'))
        held['payloads'].append(dict(operation_id=new,hash=digest(UPDATED),size=len(UPDATED)))
        cold=deepcopy(held);cold['saves'][1].update(state='committed');cold['drafts'][0]['saved_generation']=2
        cold['files'][0].update(hash=digest(UPDATED),revision=2);cold['journal']=[]
        result=dict(original_result,hash=digest(UPDATED),revision=2);cold['operations'][1].update(state='committed',result=json.dumps(result))
        cold['outbox'].append(dict(before['outbox'][0],operation_id=new,hash=digest(UPDATED),revision=2))
        return before,held,cold
    def test_no_device_or_ADB_before_exact_hosted_admission(self):
        p=Mock();p.args=SimpleNamespace(serial='phone',scope='write-interruption')
        with patch.dict(os.environ,{},clear=True),self.assertRaises(RuntimeError):run_atomic(p)
        p.shell.assert_not_called();p.device.assert_not_called()
    def test_exact_process_stat_starttime_with_embedded_comm_space(self):
        body=b'123 (own app) '+b' '.join([b'S']+[b'1']*18+[b'9876']+[b'0']*4)
        self.assertEqual(start_time(body),9876)
        for raw in [b'unknown',b'1 (x) S 1',b'x'*4096]:
            with self.assertRaises(RuntimeError):start_time(raw)
    def test_only_original_app_user0_canonical_home_and_no_traversal(self):
        for home in ['/data/user/0/'+APP,'/data/data/'+APP]:self.assertEqual(target_path(home,'./files/'),home+'/files/vault/'+NOTE)
        for home,root in [('/data/user/10/'+APP,'./'),('/data/user/0/foreign','./'),('/data/user/0/'+APP,'./../'),('/data/user/0/'+APP,'/files/')]:
            with self.assertRaises(RuntimeError):target_path(home,root)
    def test_trace_decoder_rejects_duplicate_keys_nonfinite_and_unbounded_raw(self):
        for raw in [b'{"pid":1,"pid":2}',b'{"pid":NaN}',b'x'*2049,b'\xff']:
            with self.assertRaises((RuntimeError,ValueError)):decode_event(raw)
    def test_trace_event_requires_exact_original_nonce_pid_uid_and_fresh_thread_bound(self):
        value=dict(event='ready',nonce='a'*32,pid=123,uid=10123,threads=17)
        self.assertEqual(event(value,'ready','a'*32,123,10123),value)
        for change in [dict(pid=124),dict(nonce='b'*32),dict(uid=0),dict(threads=257),dict(threads=True),dict(extra=1)]:
            with self.assertRaises(RuntimeError):event(dict(value,**change),'ready','a'*32,123,10123)
    def test_entry_is_original_supported_rename_with_distinct_owned_files(self):
        value=dict(event='entry',nonce='a'*32,pid=123,tid=124,syscall=264,temporary='.tmpA12345',old_inode=10,temp_inode=11,device=2,old_bytes=len(ORIGINAL),temp_bytes=len(UPDATED))
        self.assertEqual(event(value,'entry','a'*32,123,10123),value)
        for change in [dict(syscall=87),dict(temp_inode=10),dict(temporary='../original.md'),dict(temp_bytes=1),dict(tid=True),dict(event='exit')]:
            with self.assertRaises(RuntimeError):event(dict(value,**change),'entry','a'*32,123,10123)
    def test_resumed_original_syscall_cannot_count_as_interrupted(self):
        value=dict(event='stopped',nonce='a'*32,pid=123,original_syscall_resumed=False)
        event(value,'stopped','a'*32,123,10123)
        with self.assertRaises(RuntimeError):event(dict(value,original_syscall_resumed=True),'stopped','a'*32,123,10123)
    def test_original_pending_native_rows_bind_one_unchanged_GUI_intent_and_payload(self):
        before,held,_=self.fixture();self.assertEqual(pending(before,held),held['saves'][1])
    def test_ENOSPC_or_other_error_is_not_this_original_entry_interruption(self):
        before,held,_=self.fixture()
        for error in ['FILESYSTEM_ERROR','ATOMIC_REPLACE_FAILED']:
            changed=deepcopy(held);changed['saves'][1]['error']=error
            with self.assertRaises(RuntimeError):pending(before,changed)
    def test_premature_replacement_or_outbox_and_lost_original_draft_refuse(self):
        before,held,_=self.fixture()
        for table,field,value in [('files','hash',digest(UPDATED)),('drafts','saved_generation',2),('drafts','id','other')]:
            changed=deepcopy(held);changed[table][0][field]=value
            with self.assertRaises(RuntimeError):pending(before,changed)
        changed=deepcopy(held);changed['outbox'].append(dict(changed['outbox'][0]))
        with self.assertRaises(RuntimeError):pending(before,changed)
    def test_missing_or_changed_original_journal_and_fingerprint_refuse(self):
        before,held,_=self.fixture()
        for key in ['journal','payloads','operations']:
            changed=deepcopy(held);changed[key]=[]
            with self.assertRaises(RuntimeError):pending(before,changed)
        changed=deepcopy(held);changed['operations'][0]['fingerprint']='c'*64
        with self.assertRaises(RuntimeError):pending(before,changed)
    def test_cold_same_original_operation_and_one_receipt_outbox(self):
        before,held,cold=self.fixture();value=recovered(before,held,cold)
        self.assertEqual(value['operation_id'],held['saves'][1]['operation_id']);self.assertEqual(value['fingerprint'],'b'*64)
    def test_replacement_operation_identity_and_duplicate_outbox_cannot_pass_recovery(self):
        before,held,cold=self.fixture()
        for table,key,value in [('saves','operation_id','44444444-4444-4444-8444-444444444444'),('operations','fingerprint','c'*64)]:
            changed=deepcopy(cold);changed[table][1][key]=value
            with self.assertRaises(RuntimeError):recovered(before,held,changed)
        changed=deepcopy(cold);changed['outbox'].append(deepcopy(changed['outbox'][-1]))
        with self.assertRaises(RuntimeError):recovered(before,held,changed)
    def test_pending_journal_or_changed_native_revision_refuses_cold_completion(self):
        before,held,cold=self.fixture()
        for change in ['journal','revision']:
            changed=deepcopy(cold)
            if change=='journal':changed['journal']=deepcopy(held['journal'])
            else:changed['files'][0]['revision']=3
            with self.assertRaises(RuntimeError):recovered(before,held,changed)

if __name__=='__main__':unittest.main()
