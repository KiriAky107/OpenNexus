from copy import deepcopy
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import subprocess
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from android_runtime_probe import DRAFT
from android_sync_runtime import PEER_NOTE,SyncProbe,read_original_object,validate_upload,validate_incoming


class InitialSyncBoundaries(unittest.TestCase):
    def exchange(self):
        content=DRAFT.encode();digest=hashlib.sha256(content).hexdigest()
        job={'operation_id':'original-op','file_id':'original-file','path':'runtime-note.md',
            'hash':digest,'size':len(content),'remote_revision':1,'state':'acked','error':None}
        remote={**{k:job[k] for k in ('operation_id','file_id','path','hash','size')},'device_id':'actual-android','sequence':1}
        native={'jobs':[job],'heads':[{'file_id':'original-file','revision':1,'hash':digest}],
            'attempts':[{'operation_id':'original-op','attempts':1,'outcome':'succeeded','error':None}],
            'issuers':[{'operation_id':'original-op','device_id':'actual-android'}]}
        provider={'rows':{'revisions':[remote]},'vault':{'sequence':1}}
        return provider,native

    def test_counts_alone_cannot_substitute_for_original_operation_and_device(self):
        provider,native=self.exchange();self.assertEqual(validate_upload(provider,native,'actual-android')['sequence'],1)
        for key,value in (('operation_id','other'),('device_id','setup-actor'),('file_id','other'),
                ('hash','0'*64),('size',0),('path','other.md'),('sequence',2)):
            changed=deepcopy(provider);changed['rows']['revisions'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_upload(changed,native,'actual-android')

    def test_duplicate_revisions_or_wrong_confirmed_head_are_not_success(self):
        provider,native=self.exchange()
        provider['rows']['revisions']*=2;native['jobs']*=2
        with self.assertRaises(RuntimeError):validate_upload(provider,native,'actual-android')

    def test_provider_commit_cannot_hide_unknown_native_acknowledgement_or_changed_issuer(self):
        provider,native=self.exchange()
        for key,value in (('state','committing'),('error','SYNC_NETWORK_ERROR')):
            changed=deepcopy(native);changed['jobs'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_upload(provider,changed,'actual-android')
        for key,value in (('attempts',2),('outcome','interrupted'),('error','SYNC_NETWORK_ERROR')):
            changed=deepcopy(native);changed['attempts'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_upload(provider,changed,'actual-android')
        for table,change in (('attempts',[]),('issuers',[]),('issuers',[{'operation_id':'original-op','device_id':'setup-actor'}])):
            changed=deepcopy(native);changed[table]=change
            with self.subTest(table=table),self.assertRaises(RuntimeError):validate_upload(provider,changed,'actual-android')
        provider,native=self.exchange();native['heads'][0]['revision']=0
        with self.assertRaises(RuntimeError):validate_upload(provider,native,'actual-android')

    def test_immutable_original_object_read_cannot_follow_a_redirect_or_accept_bad_bytes(self):
        raw=DRAFT.encode();digest=hashlib.sha256(raw).hexdigest()
        for status,body in ((200,raw),(302,raw),(200,b'changed')):
            connection=Mock();connection.getresponse.return_value=SimpleNamespace(status=status,read=lambda size:body)
            with patch('android_sync_runtime.http.client.HTTPSConnection',return_value=connection):
                if status==200 and body==raw:self.assertEqual(read_original_object('https://owned-origin.trycloudflare.com','1'*32,digest,'a'*40),raw)
                else:
                    with self.assertRaises(RuntimeError):read_original_object('https://owned-origin.trycloudflare.com','1'*32,digest,'a'*40)
            connection.request.assert_called_once();self.assertEqual(connection.request.call_args.args[0],'GET')
            connection.close.assert_called_once()

    def test_untrusted_original_object_target_rejects_before_credentials_or_network(self):
        with patch('android_sync_runtime.http.client.HTTPSConnection') as connect:
            for endpoint,vault,digest in (('http://owned-origin.trycloudflare.com','1'*32,'2'*64),
                    ('https://example.com','1'*32,'2'*64),('https://owned-origin.trycloudflare.com','../private','2'*64)):
                with self.assertRaises(RuntimeError):read_original_object(endpoint,vault,digest,'a'*40)
            connect.assert_not_called()

    def test_failed_sync_UI_retains_intent_and_never_replays_the_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=SyncProbe.__new__(SyncProbe);probe.output=Path(directory);probe.phases=[]
            probe.start_app=Mock();probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'OPENNEXUS_SYNC_PHASE_FAILED stage=static\n'))
            with self.assertRaises(RuntimeError):probe.sync_phase('associate')
            probe.shell.assert_called_once();self.assertEqual(probe.phases,[])
            intent=json.loads((probe.output/'sync-dispatch-1.json').read_text())
            self.assertFalse(intent['server_or_native_operation_id']);self.assertFalse(intent['automatic_retry'])

    def test_native_snapshot_reads_only_bounded_original_metadata_and_preserves_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.sqlite3'
            endpoint='https://owned-origin.trycloudflare.com/'
            request=json.dumps({'selection':{'operation_id':'auth-original','endpoint':endpoint,'account':'owned-author'},
                'remote_vault':'1'*32,'preview_fingerprint':'2'*64})
            # Ticket::record stores the original serialized Binding, not its ID.
            receipt=json.dumps({'id':'binding','endpoint':endpoint,'remote_vault':'1'*32,'account':'owned-author','cursor':0})
            with closing(sqlite3.connect(source)) as db:
                db.executescript('''CREATE TABLE sync_bindings(id,endpoint,remote_vault,account,state,cursor);
                    CREATE TABLE sync_jobs(binding,operation_id,file_id,path,hash,size,state,remote_revision,error);
                    CREATE TABLE sync_attempts(binding,operation_id,attempts,outcome,error);
                    CREATE TABLE sync_heads(binding,file_id,revision,path,hash);
                    CREATE TABLE sync_inbox(binding,sequence,operation_id,rename_id,state);
                    CREATE TABLE sync_conflicts(binding,sequence,file_id,local_path,local_hash,state);
                    CREATE TABLE mobile_sync_binding_intents(operation_id,request_hash,request,state,attempt_revision,binding);
                    CREATE TABLE unrelated_private_values(secret);
                    INSERT INTO unrelated_private_values VALUES('must-never-be-selected-or-uploaded');''')
                db.execute('INSERT INTO sync_bindings VALUES(?,?,?,?,?,?)',('binding',endpoint,'1'*32,'owned-author','active',0))
                db.execute('INSERT INTO mobile_sync_binding_intents VALUES(?,?,?,?,?,?)',('binding-original','digest',request,'bound',1,receipt))
                db.commit()
            original=source.read_bytes();probe=SyncProbe.__new__(SyncProbe)
            probe.tools=root/'tools';probe.tools.mkdir();probe.output=root/'evidence';probe.output.mkdir()
            probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'./files/owned/vault/.opennexus/host.sqlite3\n'))
            probe.private=Mock(side_effect=lambda path,**_:subprocess.CompletedProcess([],0,original)
                if path.endswith('host.sqlite3') else subprocess.CompletedProcess([],1,b''))
            observed=probe.host_snapshot('bound')
            self.assertEqual(observed['association']['operation_id'],'binding-original')
            self.assertEqual(observed['association']['original_binding_bytes_sha256'],hashlib.sha256(receipt.encode()).hexdigest())
            self.assertEqual(observed['issuers'],[]);self.assertEqual(source.read_bytes(),original)
            self.assertFalse(observed['credentials_selected']);self.assertFalse(observed['original_database_uploaded'])
            self.assertNotIn('must-never-be-selected-or-uploaded',(probe.output/'bound-host.json').read_text())
            for call in probe.private.call_args_list:self.assertRegex(call.args[0],r'host\.sqlite3(?:-wal|-shm)?$')
            # A receipt belonging to another scope is never accepted merely
            # because its ID matches. Original bytes/cursor stay independently bound.
            for key,value in (('id','other'),('remote_vault','3'*32),('endpoint','https://other.example/'),
                    ('account','other'),('cursor',-1),('cursor',1),('cursor',False)):
                changed=dict(json.loads(receipt),**{key:value})
                with closing(sqlite3.connect(source)) as db:
                    db.execute('UPDATE mobile_sync_binding_intents SET binding=?',(json.dumps(changed),));db.commit()
                invalid=source.read_bytes()
                probe.private=Mock(side_effect=lambda path,**_:subprocess.CompletedProcess([],0,invalid)
                    if path.endswith('host.sqlite3') else subprocess.CompletedProcess([],1,b''))
                with self.subTest(key=key,value=value),self.assertRaises(RuntimeError):probe.host_snapshot('invalid')
                self.assertEqual(source.read_bytes(),invalid)
                self.assertEqual(probe.host_observation_stage,'association')
            with closing(sqlite3.connect(source)) as db:
                db.execute('UPDATE mobile_sync_binding_intents SET binding=?',(receipt,))
                db.execute('UPDATE sync_bindings SET cursor=2');db.commit()
            progressed=source.read_bytes()
            probe.private=Mock(side_effect=lambda path,**_:subprocess.CompletedProcess([],0,progressed)
                if path.endswith('host.sqlite3') else subprocess.CompletedProcess([],1,b''))
            self.assertEqual(probe.host_snapshot('progressed')['binding']['cursor'],2)
            self.assertEqual(source.read_bytes(),progressed)

    def incoming(self):
        receipt={'vault_id':'1'*32,'operation_id':'peer-original','device_id':'peer-device','file_id':'peer-file',
            'path':'peer-note.md','operation':'put','hash':hashlib.sha256(PEER_NOTE).hexdigest(),'size':len(PEER_NOTE),'sequence':2}
        provider={'vault':{'id':'1'*32,'sequence':2},'rows':{'revisions':[receipt.copy()],
            'device_progress':[{'device_id':'android','offered_sequence':2,'committed_sequence':1},
                {'device_id':'peer-device','offered_sequence':1,'committed_sequence':2}]}}
        native={'heads':[{'file_id':'peer-file','hash':receipt['hash'],'revision':2,'path':'peer-note.md'}],
            'conflicts':[],'binding':{'remote_vault':'1'*32,'cursor':2}}
        return receipt,provider,native

    def test_generated_changes_do_not_replace_original_native_head_and_cursor_evidence(self):
        receipt,provider,native=self.incoming();validate_incoming(receipt,provider,native,'android')
        for changed in ({'heads':[]},{'conflicts':[{'state':'open'}]}, {'binding':{'remote_vault':'1'*32,'cursor':1}}):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):validate_incoming(receipt,provider,dict(native,**changed),'android')
        changed=deepcopy(native);changed['heads'][0]['hash']='0'*64
        with self.assertRaises(RuntimeError):validate_incoming(receipt,provider,changed,'android')

    def test_incoming_peer_must_preserve_vault_scope_and_actual_distinct_device_progress(self):
        receipt,provider,native=self.incoming()
        changed=deepcopy(provider);changed['rows']['device_progress'][0]['offered_sequence']=1
        with self.assertRaises(RuntimeError):validate_incoming(receipt,changed,native,'android')
        with self.assertRaises(RuntimeError):validate_incoming(dict(receipt,vault_id='other'),provider,native,'android')
        with self.assertRaises(RuntimeError):validate_incoming(dict(receipt,device_id='android'),provider,native,'android')


if __name__=='__main__':unittest.main()
