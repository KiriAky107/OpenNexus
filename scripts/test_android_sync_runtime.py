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
from android_sync_runtime import PEER_NOTE,PEER_CANVAS,IMAGE_PATH,SyncProbe,read_original_object,validate_upload,validate_incoming,validate_peer_applied,validate_stopped_original,validate_stop_resumed_original


class InitialSyncBoundaries(unittest.TestCase):
    def test_failed_sync_keeps_original_observation_when_safe_renderer_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=SyncProbe.__new__(SyncProbe);probe.output=Path(directory)
            probe.read_native_styles=Mock(side_effect=RuntimeError('untrusted-private-error-must-not-be-written'))
            with patch('android_auth_runtime.AuthProbe.failure_observations') as original:
                probe.failure_observations()
                original.assert_called_once_with()
            probe.read_native_styles.assert_called_once_with('sync-failure')
            value=(probe.output/'sync-failure-renderer-unavailable.json').read_text('utf-8')
            self.assertNotIn('untrusted-private',value)
            self.assertEqual(json.loads(value),{'error':'RuntimeError','no_mutation_retried':True})

    def test_failed_sync_only_adds_readonly_renderer_observation_after_original_records(self):
        order=[];probe=SyncProbe.__new__(SyncProbe)
        probe.read_native_styles=Mock(side_effect=lambda phase:order.append(phase))
        with patch('android_auth_runtime.AuthProbe.failure_observations',side_effect=lambda:order.append('original-records')):
            probe.failure_observations()
        self.assertEqual(order,['original-records','sync-failure'])

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
            probe.association_observer=Mock()
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
                    CREATE TABLE sync_inbox(binding,sequence,revision,operation_id,rename_id,state);
                    CREATE TABLE sync_conflicts(binding,sequence,file_id,local_path,local_hash,state);
                    CREATE TABLE mobile_sync_binding_intents(operation_id,request_hash,request,state,attempt_revision,binding);
                    CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state,content);
                    CREATE TABLE payloads(operation_id,hash,size);
                    CREATE TABLE sync_resolutions(binding,sequence,choice,destination,expected,operation_id,rename_id,copy_id,state,retire_id,restore_id);
                    CREATE TABLE sync_review_receipts(binding,sequence,fingerprint,expected,choice,destination,guards);
                    CREATE TABLE unrelated_private_values(secret);
                    INSERT INTO unrelated_private_values VALUES('must-never-be-selected-or-uploaded');''')
                db.execute('INSERT INTO sync_bindings VALUES(?,?,?,?,?,?)',('binding',endpoint,'1'*32,'owned-author','active',0))
                db.execute('INSERT INTO mobile_sync_binding_intents VALUES(?,?,?,?,?,?)',('binding-original','digest',request,'bound',1,receipt))
                db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?)',('owned-op','owned-file',1,'runtime-note.md',hashlib.sha256(DRAFT.encode()).hexdigest(),'put','pending',b''))
                db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?)',('private-op','private-file',1,'private.md','private-hash','put','pending',b'must-never-be-selected-or-uploaded'))
                db.execute('INSERT INTO payloads VALUES(?,?,?)',('owned-op',hashlib.sha256(DRAFT.encode()).hexdigest(),len(DRAFT.encode())))
                db.execute('INSERT INTO payloads VALUES(?,?,?)',('private-op','must-never-be-selected-or-uploaded',99))
                db.execute('INSERT INTO sync_resolutions VALUES(?,?,?,?,?,?,?,?,?,?,?)',('binding',2,'copy','copy.md','local-hash','original-write','original-rename','original-copy','completed','original-retire','original-restore'))
                db.execute('INSERT INTO sync_review_receipts VALUES(?,?,?,?,?,?,?)',('binding',2,'original-review','local-hash','copy','copy.md','[]'))
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
            owned=probe.host_snapshot('owned',include_outbox=True)
            self.assertEqual(owned['outbox'],[{'operation_id':'owned-op','file_id':'owned-file','revision':1,
                'path':'runtime-note.md','hash':hashlib.sha256(DRAFT.encode()).hexdigest(),'operation':'put','state':'pending','payload_size':0}])
            self.assertEqual(source.read_bytes(),original)
            self.assertNotIn('private-file',(probe.output/'owned-host.json').read_text())
            self.assertNotIn('must-never-be-selected-or-uploaded',(probe.output/'owned-host.json').read_text())
            payloads=probe.host_snapshot('payloads',include_outbox=True,include_payloads=True)
            self.assertEqual(payloads['payloads'],[dict(operation_id='owned-op',hash=hashlib.sha256(DRAFT.encode()).hexdigest(),size=len(DRAFT.encode()))])
            self.assertNotIn('must-never-be-selected-or-uploaded',(probe.output/'payloads-host.json').read_text())
            self.assertEqual(source.read_bytes(),original)
            probe.args=SimpleNamespace(conflicts=True)
            decisions=probe.host_snapshot('decisions')['decisions']
            self.assertEqual(decisions,[{'sequence':2,'choice':'copy','destination':'copy.md','expected_hash':'local-hash',
                'fingerprint':'original-review','operation_id':'original-write','rename_id':'original-rename','copy_id':'original-copy',
                'retire_id':'original-retire','restore_id':'original-restore','state':'completed'}])
            self.assertEqual(source.read_bytes(),original)
            self.assertNotIn('must-never-be-selected-or-uploaded',(probe.output/'decisions-host.json').read_text())
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

    def media(self,delete=False):
        receipt={'vault_id':'1'*32,'operation_id':'original-peer-op','device_id':'peer','file_id':'original-peer-file',
            'base_revision':2,'path':'peer-renamed.md','operation':'delete' if delete else 'put',
            'hash':None if delete else hashlib.sha256(PEER_NOTE).hexdigest(),'size':0 if delete else len(PEER_NOTE),
            'sequence':3,'fingerprint':'2'*64}
        provider={'vault':{'id':'1'*32,'sequence':3},'rows':{'revisions':[receipt.copy()],
            'files':[{'file_id':receipt['file_id'],'sequence':3,'path_key':receipt['path'],'deleted':int(delete)}],
            'device_progress':[{'device_id':'android','offered_sequence':3},{'device_id':'peer','committed_sequence':3}]}}
        native={'binding':{'remote_vault':'1'*32,'cursor':3},'conflicts':[],
            'heads':[{'file_id':receipt['file_id'],'revision':3,'path':receipt['path'],'hash':'' if delete else receipt['hash']}],
            'inbox':[{'sequence':3,'revision':json.dumps({k:v for k,v in receipt.items() if k not in ('device_id','fingerprint')}),
                'operation_id':'distinct-local-apply-id','state':'applied'}]}
        return receipt,provider,native

    def test_peer_generated_pages_cannot_substitute_for_original_native_inbox_apply(self):
        receipt,provider,native=self.media();validate_peer_applied([receipt],provider,native,'android')
        for field,value in (('operation_id','other'),('device_id','android'),('file_id','other'),('path','other.md'),
                ('hash','3'*64),('fingerprint','3'*64),('base_revision',0)):
            changed=deepcopy(native);row=json.loads(changed['inbox'][0]['revision']);row[field]=value
            changed['inbox'][0]['revision']=json.dumps(row)
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_peer_applied([receipt],provider,changed,'android')
        changed=deepcopy(native);changed['inbox'][0]['state']='pending'
        with self.assertRaises(RuntimeError):validate_peer_applied([receipt],provider,changed,'android')
        for field in ('device_id','fingerprint'):
            changed=deepcopy(provider);changed['rows']['revisions'][0][field]='other'
            with self.subTest(provider=field),self.assertRaises(RuntimeError):validate_peer_applied([receipt],changed,native,'android')
        with self.assertRaises(RuntimeError):validate_peer_applied([receipt,receipt],provider,native,'android')

    def test_original_delete_requires_actual_tombstone_and_native_empty_hash_head(self):
        receipt,provider,native=self.media(delete=True);validate_peer_applied([receipt],provider,native,'android')
        for table,field,value in (('files','deleted',False),('files','sequence',2),('files','path_key','peer-note.md')):
            changed=deepcopy(provider);changed['rows'][table][0][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_peer_applied([receipt],changed,native,'android')
        changed=deepcopy(native);changed['heads'][0]['hash']='stale-hash'
        with self.assertRaises(RuntimeError):validate_peer_applied([receipt],provider,changed,'android')

    def test_received_canvas_keeps_original_CRLF_and_canonical_original_image_reference(self):
        from android_runtime_probe import IMAGE_BYTES
        canvas=json.loads(PEER_CANVAS)
        self.assertTrue(PEER_CANVAS.endswith(b'\r\n'));self.assertNotIn(b'\n',PEER_CANVAS.replace(b'\r\n',b''))
        self.assertEqual(canvas['nodes'][1]['file'],IMAGE_PATH)
        self.assertEqual(IMAGE_PATH,'attachments/'+hashlib.sha256(IMAGE_BYTES).hexdigest()[:2]+'/'+hashlib.sha256(IMAGE_BYTES).hexdigest()+'.png')
        self.assertEqual(canvas['vendor'],{'original':'保留原始属性与 CRLF'})

    def stopped(self):
        before={'binding':{'id':'owned-binding','cursor':1},'heads':[{'revision':1,'hash':'original'}]}
        provider={'vault':{'id':'owned-vault','sequence':2},'rows':{'revisions':[{'original':True},{'pending_original':True}],
            'files':[{'original':True}],'objects':[{'original':True}],'uploads':[],
            'device_progress':[{'device_id':'android','committed_sequence':1,'committed_at':10,'offered_sequence':0,'offered_at':9}]},
            'delivery':{'counts':{'changes:200':3,'revision:200':1},'events':[]}}
        current=deepcopy(provider);current['rows']['device_progress'][0].update(offered_sequence=2,offered_at=11)
        current['delivery']['counts']['changes:200']=4
        current['delivery']['events']=[{'action':'changes','service_status':200,'delivery_status':None,'fault_operation':'control-original'}]
        held={'operation_id':'control-original','revision':1,'mode':'hold_changes','action':'changes',
            'service_status':200,'service_response_complete':True,'raw_body_recorded':False}
        return before,provider,current,held

    def test_generated_original_page_is_not_a_native_apply_or_new_write_after_Stop(self):
        before,provider,current,held=self.stopped();validate_stopped_original(before,deepcopy(before),provider,current,'android',held)
        changed=deepcopy(before);changed['binding']['cursor']=2
        with self.assertRaises(RuntimeError):validate_stopped_original(before,changed,provider,current,'android',held)
        for table in ('revisions','files','objects','uploads'):
            changed=deepcopy(current);changed['rows'][table].append({'unexpected_write':True})
            with self.subTest(table=table),self.assertRaises(RuntimeError):validate_stopped_original(before,before,provider,changed,'android',held)
        changed=deepcopy(current);changed['delivery']['counts']['revision:200']=2
        with self.assertRaises(RuntimeError):validate_stopped_original(before,before,provider,changed,'android',held)

    def test_Stop_requires_one_original_held_success_and_preserves_commit_identity(self):
        before,provider,current,held=self.stopped()
        for field,value in (('mode','normal'),('action','revision'),('service_status',503),
                ('service_response_complete',False),('raw_body_recorded',True)):
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                validate_stopped_original(before,before,provider,current,'android',dict(held,**{field:value}))
        changed=deepcopy(current);changed['delivery']['events']*=2
        with self.assertRaises(RuntimeError):validate_stopped_original(before,before,provider,changed,'android',held)
        for field,value in (('device_id','other'),('committed_sequence',2),('committed_at',11),('offered_sequence',0)):
            changed=deepcopy(current);changed['rows']['device_progress'][0][field]=value
            with self.subTest(progress=field),self.assertRaises(RuntimeError):validate_stopped_original(before,before,provider,changed,'android',held)

    def test_Stop_requires_original_unapplied_content_not_an_already_current_empty_page(self):
        before,provider,current,held=self.stopped()
        before['binding']['cursor']=provider['vault']['sequence']
        with self.assertRaises(RuntimeError):validate_stopped_original(before,before,provider,current,'android',held)

    def test_explicit_Stop_resume_applies_only_pending_original_and_preserves_acked_records(self):
        receipt,provider,native=self.media()
        provider['rows'].update(objects=[{'hash':receipt['hash'],'size':receipt['size']}],uploads=[])
        provider['delivery']={'counts':{'changes:200':2,'revision:200':2}}
        native['binding']['id']='original-binding'
        for key in ('jobs','attempts','issuers'):native[key]=[{'original':key}]
        native['association']={'original':'frozen'}
        old_head={'file_id':'old','revision':1,'path':'runtime-note.md','hash':'old'}
        old_inbox={'sequence':1,'state':'applied','original':'inbox'}
        before=deepcopy(native);before['binding']['cursor']=2;before['heads']=[old_head];before['inbox']=[old_inbox]
        native['heads'].append(old_head);native['inbox'].append(old_inbox)
        after=deepcopy(provider);after['delivery']['counts']['changes:200']=3
        validate_stop_resumed_original(before,native,provider,after,'android',receipt)
        for key in ('jobs','attempts','issuers','association','heads','inbox'):
            changed=deepcopy(native);changed[key]=[]
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                validate_stop_resumed_original(before,changed,provider,after,'android',receipt)
        changed=deepcopy(after);changed['delivery']['counts']['revision:200']=3
        with self.assertRaises(RuntimeError):validate_stop_resumed_original(before,native,provider,changed,'android',receipt)
        changed=deepcopy(after);changed['rows']['revisions'].append(dict(receipt,operation_id='unexpected-write',sequence=4))
        with self.assertRaises(RuntimeError):validate_stop_resumed_original(before,native,provider,changed,'android',receipt)

    def test_Stop_controller_cannot_arm_unrelated_faults_before_owner_access(self):
        probe=object.__new__(SyncProbe)
        with patch('android_sync_runtime.control_root') as root:
            for mode in ('normal','drop_revision','../release','hold_login'):
                with self.assertRaises(RuntimeError):probe.sync_control(mode)
            root.assert_not_called()


if __name__=='__main__':unittest.main()
