"""Real SQLite copies and original local ASGI hold/release; no device or PG/S3."""
import asyncio
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import android_auth_fixture as controls
import android_sync_fixture as fixture
from android_sync_stop_host import OriginalHeldRevision
import android_release_sync_stop_snapshot as snapshots
import android_release_sync_stop_validation as validation
import android_release_sync_recovery_snapshot as recovery

SAVE='11111111-1111-4111-8111-111111111111'
UPLOAD='22222222-2222-4222-8222-222222222222'
FILE='33333333-3333-4333-8333-333333333333'
DRAFT='44444444-4444-4444-8444-444444444444'
ASSOCIATION='55555555-5555-4555-8555-555555555555'
BINDING='66666666-6666-4666-8666-666666666666'
HOLD='77777777-7777-4777-8777-777777777777'
RELEASE='88888888-8888-4888-8888-888888888888'
APPLY='99999999-9999-4999-8999-999999999999'
RENAME='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
DEVICE='a'*32
VAULT='c'*32


def receipt():
    return dict(sequence=1,file_id=FILE,base_revision=0,path=snapshots.NOTE,operation='put',hash=snapshots.HASH,
        size=len(snapshots.BODY.encode()),device_id=DEVICE,operation_id=UPLOAD,fingerprint='e'*64,vault_id=VAULT)


def refresh(raw):
    m=json.loads((raw/'snapshot.json').read_bytes())
    for name in m['files']:
        f=raw/name;m['files'][name]=dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) if f.is_file() else None
    (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')


def original_fixture(root,stage,*,wal=False):
    raw=root/'raw';raw.mkdir();(raw/'vault').mkdir();(raw/'vault'/snapshots.NOTE).write_bytes(snapshots.BODY.encode())
    dbs=[]
    for relative in snapshots.DATABASES:
        path=raw/relative;path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(path);dbs.append(db)
        if wal:db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
    draft,host=dbs
    draft.executescript('CREATE TABLE drafts(id,file_id,path,base_hash,generation,saved_generation,content);'
        'CREATE TABLE save_intents(operation_id,draft_id,generation,file_id,path,expected,content,state,error);'
        'CREATE TABLE forbidden_credentials(secret);INSERT INTO forbidden_credentials VALUES("private-sentinel-never-query");')
    draft.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',(DRAFT,FILE,snapshots.NOTE,snapshots.HASH,3,3,snapshots.BODY))
    draft.execute('INSERT INTO save_intents VALUES(?,?,?,?,?,?,?,?,?)',(SAVE,DRAFT,3,None,snapshots.NOTE,'',snapshots.BODY,'committed',None));draft.commit()
    host.executescript('CREATE TABLE files(id,path,hash,revision,deleted);'
        'CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state);'
        'CREATE TABLE sync_bindings(id,remote_vault,state,cursor,endpoint,account);'
        'CREATE TABLE mobile_sync_binding_intents(operation_id,request_hash,request,state,attempt_revision,binding);'
        'CREATE TABLE forbidden_credentials(secret);INSERT INTO forbidden_credentials VALUES("private-sentinel-never-query");')
    for _,(table,columns) in snapshots.TABLES.items():host.execute('CREATE TABLE '+table+'(binding,'+columns+')')
    for column in ('operation','base_revision','upload_id'):host.execute('ALTER TABLE sync_jobs ADD COLUMN '+column)
    host.execute('INSERT INTO files VALUES(?,?,?,?,?)',(FILE,snapshots.NOTE,snapshots.HASH,1,0))
    host.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',(SAVE,FILE,1,snapshots.NOTE,snapshots.HASH,'put','pending' if stage=='seeded' else 'archived'))
    bound=stage!='seeded';complete=stage in ('reconciled','cold')
    if bound:
        host.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',(UPLOAD,FILE,1,snapshots.NOTE,snapshots.HASH,'put','pending' if stage=='associated' else 'acked' if complete else 'queued'))
        host.execute('INSERT INTO sync_bindings VALUES(?,?,?,?,?,?)',(BINDING,VAULT,'active',1 if complete else 0,'https://owned.example/','owned-a'))
        request=dict(operation_id=ASSOCIATION,local_vault=BINDING,remote_vault=VAULT,preview_fingerprint='b'*64,
            selection=dict(revision=1,endpoint='https://owned.example/',account='owned-a',operation_id=ASSOCIATION,generation=1))
        binding=dict(id=BINDING,endpoint='https://owned.example/',remote_vault=VAULT,account='owned-a',cursor=0)
        host.execute('INSERT INTO mobile_sync_binding_intents VALUES(?,?,?,?,?,?)',(ASSOCIATION,'c'*64,json.dumps(request),'bound',1,json.dumps(binding)))
    if stage not in ('seeded','associated'):
        host.execute('INSERT INTO sync_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (BINDING,UPLOAD,FILE,snapshots.NOTE,snapshots.HASH,len(snapshots.BODY.encode()),'acked' if complete else 'committing',1 if complete else None,None,'put',0,'d'*32))
        host.execute('INSERT INTO sync_attempts VALUES(?,?,?,?,?)',(BINDING,UPLOAD,1,'interrupted',None))
        host.execute('INSERT INTO mobile_sync_attempt_devices VALUES(?,?,?)',(BINDING,UPLOAD,DEVICE))
        if complete:
            remote={k:v for k,v in receipt().items() if k not in ('fingerprint','device_id')}
            host.execute('INSERT INTO sync_heads VALUES(?,?,?,?,?)',(BINDING,FILE,1,snapshots.NOTE,snapshots.HASH))
            host.execute('INSERT INTO sync_inbox VALUES(?,?,?,?,?,?)',(BINDING,1,json.dumps(remote),APPLY,RENAME,'applied'))
    host.commit()
    if not wal:
        for db in dbs:db.close()
        dbs=[]
    expected={'vault/'+snapshots.NOTE}|{name+suffix for name in snapshots.DATABASES for suffix in ('','-wal','-shm')}
    (raw/'snapshot.json').write_text(json.dumps(dict(package='cc.kronecker.opennexus',app_stopped_before_and_after=True,
        credential_store_copied=False,SQL_executed_on_device=False,files={name:None for name in expected})),'utf-8');refresh(raw)
    return raw,dbs


def projection(root,stage):
    root.mkdir();raw,_=original_fixture(root,stage);return snapshots.project_stop(raw,root/'working',stage=stage)


def providers():
    before=dict(vault=dict(id=VAULT,sequence=0,used=0),rows=dict(revisions=[],files=[],objects=[],uploads=[],device_progress=[]),
        delivery=dict(counts={'changes:200':2},events=[]))
    after=deepcopy(before);after['vault'].update(sequence=1,used=len(snapshots.BODY.encode()))
    after['rows'].update(revisions=[{k:v for k,v in receipt().items() if k!='vault_id'}],
        files=[dict(file_id=FILE,sequence=1,path_key=snapshots.NOTE,deleted=0)],objects=[dict(hash=snapshots.HASH,size=len(snapshots.BODY.encode()))])
    after['delivery']['counts']['revision:200']=1
    after['delivery']['events']=[dict(action='revision',service_status=200,delivery_status=None,fault_operation=HOLD)]
    hold=dict(operation_id=HOLD,mode='hold_revision',expected_revision=0,result_revision=1)
    held=dict(operation_id=HOLD,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
    release=dict(operation_id=RELEASE,mode='release',expected_revision=1,result_revision=2)
    released=dict(operation_id=HOLD,original_response_send_attempted=True,ASGI_send_returned=True,client_receipt_known=False)
    return before,after,hold,held,release,released


class StopProjection(unittest.TestCase):
    def test_all_six_original_SQLite_stages_only_open_new_readonly_copies_and_keep_original_bytes(self):
        for stage in snapshots.STAGES:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,stage)
                original={f.relative_to(raw).as_posix():f.read_bytes() for f in raw.rglob('*') if f.is_file()};queries=[];read_tables=[];connect=sqlite3.connect
                def checked(path,*args,**kwargs):
                    self.assertIn('?mode=ro',str(path));self.assertIn('/working/',str(path));self.assertNotIn('/raw/',str(path))
                    db=connect(path,*args,**kwargs);db.set_trace_callback(queries.append)
                    db.set_authorizer(lambda action,table,column,*other:read_tables.append(table) or sqlite3.SQLITE_OK if action==sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
                    return db
                with patch.object(sqlite3,'connect',side_effect=checked):value=snapshots.project_stop(raw,root/'working',stage=stage)
                self.assertEqual(original,{f.relative_to(raw).as_posix():f.read_bytes() for f in raw.rglob('*') if f.is_file()})
                self.assertNotIn('forbidden_credentials',read_tables);self.assertFalse(any('SELECT *' in q for q in queries))
                self.assertNotIn('private-sentinel',json.dumps(value));self.assertFalse(value['credentials_selected']);self.assertFalse(value['native_RAM_invocation_id_observed'])
                self.assertEqual(value['all_Save_ids'],[SAVE]);self.assertEqual(value['original_association_upload_id'],None if stage=='seeded' else UPLOAD)
                if stage not in ('seeded','associated'):self.assertEqual(value['native']['attempts'],[dict(operation_id=UPLOAD,attempts=1,outcome='interrupted',error=None)])

    def test_real_DB_WAL_SHM_bytes_are_preserved_and_only_the_new_copy_is_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,open_writers=original_fixture(root,'stopped',wal=True)
            try:
                original={f.relative_to(raw).as_posix():f.read_bytes() for f in raw.rglob('*') if f.is_file()}
                self.assertTrue(all((raw/(db+'-wal')).is_file() and (raw/(db+'-shm')).is_file() for db in snapshots.DATABASES))
                value=snapshots.project_stop(raw,root/'working',stage='stopped')
                self.assertEqual(len(value['raw_sha256']),7)
                self.assertEqual(original,{f.relative_to(raw).as_posix():f.read_bytes() for f in raw.rglob('*') if f.is_file()})
            finally:
                for db in open_writers:db.close()

    def test_Save_NULL_archive_and_distinct_association_upload_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);seed=projection(root/'seed','seeded');associated=projection(root/'associated','associated')
            snapshots.preserve_initial_outbox(seed,associated)
            for stage in ('stopped','stopped-read','reconciled','cold'):
                value=projection(root/stage,stage);snapshots.preserve_note(seed,value)
                self.assertEqual(value['native']['outbox'][0],associated['native']['outbox'][0]);self.assertEqual(value['original_association_upload_id'],UPLOAD)
            for mode in ('old-state','old-id','new-id','new-hash','missing-archive'):
                value=deepcopy(associated)
                if mode=='old-state':value['native']['outbox'][0]['state']='pending'
                if mode=='old-id':value['native']['outbox'][0]['operation_id']=HOLD
                if mode=='new-id':value['native']['outbox'][1]['operation_id']=SAVE
                if mode=='new-hash':value['native']['outbox'][1]['hash']='0'*64
                if mode=='missing-archive':value['native']['outbox'].pop(0)
                with self.subTest(mode=mode),self.assertRaises((RuntimeError,ValueError)):snapshots.preserve_initial_outbox(seed,value)

    def test_SQLite_rejects_hidden_Save_draft_outbox_or_foreign_transfer_binding(self):
        for mode in ('draft','save','NULL-Save-rewritten','missing-archive','reactivated-archive','third-outbox','foreign-job','foreign-attempt','foreign-association','many-rows'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'stopped')
                with closing(sqlite3.connect(raw/snapshots.DATABASES[0 if mode in ('draft','save','NULL-Save-rewritten','many-rows') else 1])) as db:
                    if mode=='draft':db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',(RELEASE,FILE,'hidden.md','',0,0,'unapproved-content'))
                    if mode=='save':db.execute('INSERT INTO save_intents SELECT * FROM save_intents')
                    if mode=='NULL-Save-rewritten':db.execute('UPDATE save_intents SET file_id=?',(FILE,))
                    if mode=='missing-archive':db.execute('DELETE FROM outbox WHERE operation_id=?',(SAVE,))
                    if mode=='reactivated-archive':db.execute("UPDATE outbox SET state='pending' WHERE operation_id=?",(SAVE,))
                    if mode=='third-outbox':db.execute('INSERT INTO outbox SELECT * FROM outbox WHERE operation_id=?',(SAVE,))
                    if mode=='foreign-job':db.execute('UPDATE sync_jobs SET binding=?',(RELEASE,))
                    if mode=='foreign-attempt':db.execute('UPDATE sync_attempts SET binding=?',(RELEASE,))
                    if mode=='foreign-association':db.execute('INSERT INTO mobile_sync_binding_intents SELECT * FROM mobile_sync_binding_intents')
                    if mode=='many-rows':db.executemany('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',[(str(i),FILE,snapshots.NOTE,'',0,0,'') for i in range(65)])
                    db.commit()
                refresh(raw)
                with self.assertRaises(RuntimeError):snapshots.project_stop(raw,root/'working',stage='stopped')

    def test_wrong_attempt_state_error_count_payload_or_premature_apply_is_rejected(self):
        changes=("UPDATE sync_attempts SET outcome='failed'","UPDATE sync_attempts SET outcome='running'","UPDATE sync_attempts SET outcome='succeeded'",
            'UPDATE sync_attempts SET attempts=2',"UPDATE sync_attempts SET error='SYNC_REMOTE_ERROR'","UPDATE sync_jobs SET error='private-error-body'",
            'UPDATE sync_jobs SET base_revision=9',"UPDATE sync_jobs SET operation='delete'",'UPDATE sync_jobs SET remote_revision=1',
            'UPDATE sync_bindings SET cursor=1',"INSERT INTO sync_heads VALUES('"+BINDING+"','"+FILE+"',1,'"+snapshots.NOTE+"','"+snapshots.HASH+"')")
        for sql in changes:
            with self.subTest(sql=sql),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'stopped')
                with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:db.execute(sql);db.commit()
                refresh(raw)
                with self.assertRaises(RuntimeError):snapshots.project_stop(raw,root/'working',stage='stopped')

    def test_forged_revision_extra_fields_or_duplicate_keys_are_rejected(self):
        for mode in ('extra','duplicate','wrong-operation','wrong-sequence'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'reconciled')
                with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                    v=json.loads(db.execute('SELECT revision FROM sync_inbox').fetchone()[0])
                    if mode=='extra':v['password']='never-export'
                    if mode=='wrong-operation':v['operation_id']=RELEASE
                    if mode=='wrong-sequence':v['sequence']=2
                    encoded=json.dumps(v)
                    if mode=='duplicate':encoded=encoded[:-1]+',"sequence":1}'
                    db.execute('UPDATE sync_inbox SET revision=?',(encoded,));db.commit()
                refresh(raw)
                with self.assertRaises(RuntimeError):snapshots.project_stop(raw,root/'working',stage='reconciled')

    def test_changed_raw_bytes_private_paths_alias_nested_or_reused_copy_refused(self):
        for mode in ('bytes','extra-auth','not-stopped','nested','reused','alias','bad-stage'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');working=root/'working'
                m=json.loads((raw/'snapshot.json').read_bytes())
                if mode=='bytes':(raw/'vault'/snapshots.NOTE).write_bytes(b'changed')
                if mode=='extra-auth':m['files']['mobile-sync-auth.sqlite3']=None
                if mode=='not-stopped':m['app_stopped_before_and_after']=False
                if mode=='nested':working=raw/'working'
                if mode=='reused':working.mkdir()
                if mode=='alias':working=root/'alias'/'..'/'working'
                (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
                with self.assertRaises(RuntimeError):snapshots.project_stop(raw,working,stage='unknown' if mode=='bad-stage' else 'associated')

    def test_read_pairs_keep_original_attempt_upload_handle_and_all_bytes_except_DB_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for a,b in (('stopped','stopped-read'),('reconciled','cold')):
                first=projection(root/a,a);second=projection(root/b,b);snapshots.preserve_read(first,second)
                second['raw_sha256'][snapshots.DATABASES[1]]='0'*64;snapshots.preserve_read(first,second)
                for field in ('attempt','issuer','commit-upload','file-bytes'):
                    bad=deepcopy(second)
                    if field=='attempt':bad['native']['attempts'][0]['outcome']='succeeded'
                    if field=='issuer':bad['native']['issuers'][0]['device_id']='f'*32
                    if field=='commit-upload':bad['original_job_commit'][0]['upload_id']='replaced'
                    if field=='file-bytes':bad['raw_sha256']['vault/'+snapshots.NOTE]='f'*64
                    with self.subTest(field=field),self.assertRaises(RuntimeError):snapshots.preserve_read(first,bad)

    def test_old_recovery_failed_attempt_contract_is_not_loosened_for_interrupted_Stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,_=original_fixture(root,'stopped')
            with self.assertRaises(RuntimeError):recovery.project_recovery(raw,root/'old-working',stage='unknown')
            self.assertEqual(snapshots.project_stop(raw,root/'stop-working',stage='stopped')['native']['attempts'][0]['outcome'],'interrupted')


class StopValidators(unittest.TestCase):
    def test_stopped_original_once_committed_held_and_late_release_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);associated=projection(root/'associated','associated');stopped=projection(root/'stopped','stopped')
            p0,p1,hold,held,release,released=providers()
            self.assertEqual(validation.validate_stopped(associated,stopped,p0,p1,DEVICE,hold,held,release,released),receipt())
            for mode in ('drop503','drop-marker','wrong-vault','extra-revision','other-revision-event','premature-head','wrong-issuer','relabelled','client-ACK','wrong-release-base','counter-bool'):
                before,after,h,marker,rel,delivered=deepcopy((p0,p1,hold,held,release,released));native=deepcopy(stopped)
                if mode=='drop503':after['delivery']['events'][0]['delivery_status']=503
                if mode=='drop-marker':marker['mode']='drop_revision'
                if mode=='wrong-vault':after['vault']['id']='f'*32
                if mode=='extra-revision':after['delivery']['counts']['revision:200']=2
                if mode=='other-revision-event':after['delivery']['events'].append(dict(action='revision',service_status=200,delivery_status=200,fault_operation=None))
                if mode=='premature-head':native['native']['heads']=[dict(file_id=FILE)]
                if mode=='wrong-issuer':native['native']['issuers'][0]['device_id']='f'*32
                if mode=='relabelled':native['native']['attempts'][0]['outcome']='failed'
                if mode=='client-ACK':delivered['client_receipt_known']=True
                if mode=='wrong-release-base':rel['expected_revision']=0
                if mode=='counter-bool':after['delivery']['counts']['revision:200']=True
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_stopped(associated,native,before,after,DEVICE,h,marker,rel,delivered)

    def test_explicit_changes_read_keeps_interrupted_attempt_and_never_reposts_or_substitutes_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'before','stopped-read');after=projection(root/'after','reconciled')
            _,p0,*_=providers();p1=deepcopy(p0);p1['delivery']['counts']['changes:200']+=1
            p1['rows']['device_progress']=[dict(device_id=DEVICE,offered_sequence=1,committed_sequence=None)]
            validation.validate_reconciled(before,after,p0,p1,receipt())
            for mode in ('attempt','count','upload-handle','Save-archive','new-operation','new-revision','new-object','new-upload','no-changes','extra-write-event','not-read-before'):
                native=deepcopy(after);prior=deepcopy(before);provider=deepcopy(p1)
                if mode=='attempt':native['native']['attempts'][0]['outcome']='succeeded'
                if mode=='count':native['native']['attempts'][0]['attempts']=2
                if mode=='upload-handle':native['original_job_commit'][0]['upload_id']='replacement'
                if mode=='Save-archive':native['native']['outbox'][0]['state']='pending'
                if mode=='new-operation':native['native']['outbox'][1]['operation_id']=RELEASE
                if mode=='new-revision':provider['delivery']['counts']['revision:200']=2
                if mode=='new-object':provider['rows']['objects'].append(dict(hash='f'*64,size=1))
                if mode=='new-upload':provider['rows']['uploads']=[dict(id='replacement')]
                if mode=='no-changes':provider['delivery']['counts']['changes:200']=p0['delivery']['counts']['changes:200']
                if mode=='extra-write-event':provider['delivery']['events'].append(deepcopy(provider['delivery']['events'][0]))
                if mode=='not-read-before':prior['stop_stage']='stopped'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_reconciled(prior,native,p0,provider,receipt())


class OriginalHoldProtocol(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,fail_send):
        with tempfile.TemporaryDirectory() as folder:
            owner=Path(folder);root=fixture.initialize_control(owner);fixture.freeze_target(owner,VAULT)
            hold=fixture.arm_control(owner,HOLD,'hold_revision',0);original=OriginalHeldRevision(owner,VAULT,hold)
            calls=[];sent=[];private=b'private-original-service-response-not-recorded'
            async def app(scope,receive,send):
                calls.append((scope['method'],scope['path']));await receive()
                await send(dict(type='http.response.start',status=200,headers=[]));await send(dict(type='http.response.body',body=private))
            async def receive():return dict(type='http.request',body=b'private-request-not-recorded',more_body=False)
            async def send(message):
                if fail_send:raise RuntimeError('controlled disconnected ASGI sender')
                sent.append(message)
            proxy=fixture.TargetedDeliveryProxy(app,owner)
            pending=asyncio.create_task(proxy(dict(type='http',method='POST',path='/sync/v1/vaults/'+VAULT+'/revisions',query_string=b''),receive,send))
            try:
                async with asyncio.timeout(3):
                    marker=None
                    while marker is None:
                        marker=original.read_held();await asyncio.sleep(0)
                self.assertFalse(pending.done());self.assertEqual(len(calls),1);self.assertFalse(sent)
                held_counts=controls.read_json(root/'request-counts.json');self.assertEqual(held_counts['counts'],{'revision:200':1})
                self.assertIsNone(held_counts['events'][0]['delivery_status'])
                output=owner/'evidence';output.mkdir();release=original.begin_release(output,'original-stop')
                if fail_send:
                    with self.assertRaises(RuntimeError):await pending
                else:await pending
                released=original.read_released();self.assertEqual(released['ASGI_send_returned'],not fail_send)
                self.assertFalse(released['client_receipt_known']);self.assertEqual(len(calls),1)
                self.assertEqual(controls.read_json(root/'control.json')['revision'],2)
                validation.validate_controls(hold,marker,release,released)
                self.assertFalse(any(private in f.read_bytes() or b'private-request-not-recorded' in f.read_bytes() for f in root.rglob('*.json')))
            finally:
                if not pending.done():
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):await pending

    async def test_actual_local_hold_once_body_free_marker_and_original_response_release(self):await self.exercise(False)
    async def test_failed_ASGI_send_keeps_client_receipt_unknown_without_another_service_call(self):await self.exercise(True)


if __name__=='__main__':unittest.main()
