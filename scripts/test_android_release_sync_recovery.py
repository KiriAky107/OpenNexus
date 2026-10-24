"""Real bounded SQLite copies and controlled original response/state checks only."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import Mock, patch
import unittest

import android_release_sync_recovery as runtime
import android_release_sync_recovery_snapshot as snapshots
import test_android_release_sync as base
import test_android_sync_fixture as delivery
import android_sync_fixture as fixture

OP = '11111111-2222-4333-8444-555555555555'
FILE = '21111111-2222-4333-8444-555555555555'
DRAFT = '31111111-2222-4333-8444-555555555555'
CONTROL = '41111111-2222-4333-8444-555555555555'
ANDROID = 'a'*32


def refresh_manifest(raw):
    m = json.loads((raw/'snapshot.json').read_bytes())
    for name in m['files']:
        f = raw/name
        m['files'][name] = dict(bytes=f.stat().st_size,sha256=snapshots.hashlib.sha256(f.read_bytes()).hexdigest()) if f.exists() else None
    (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')


def original_receipt():
    return dict(operation_id=OP,file_id=FILE,base_revision=0,path=snapshots.NOTE,operation='put',hash=snapshots.HASH,
        size=len(runtime.BODY.encode()),sequence=1,vault_id=base.VAULT,device_id=ANDROID,fingerprint='c'*64)


def original_fixture(root, stage):
    raw = base.ReleaseSyncChecks().fixture(root,bound=stage!='seeded')
    canvas = raw/'vault/release-owned-original.canvas'
    canvas.unlink()
    with closing(sqlite3.connect(raw/snapshots.DATABASES[0])) as db:
        db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',(DRAFT,FILE,snapshots.NOTE,snapshots.HASH,1,1,runtime.BODY))
        # Actual new-note Save freezes file_id=NULL; observe_save updates the
        # draft's new file identity while retaining the original Save payload.
        db.execute('INSERT INTO save_intents VALUES(?,?,?,?,?,?,?,?,?)',(OP,DRAFT,1,None,snapshots.NOTE,'',runtime.BODY,'committed',None));db.commit()
    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
        db.execute('DELETE FROM files WHERE path=?',('release-owned-original.canvas',))
        db.execute('UPDATE files SET id=?',(FILE,))
        state = 'pending' if stage in ('seeded','associated') else 'queued' if stage in ('unknown','unknown-read') else 'acked'
        db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',(OP,FILE,1,snapshots.NOTE,snapshots.HASH,'put',state))
        db.execute('ALTER TABLE sync_jobs ADD COLUMN operation'); db.execute('ALTER TABLE sync_jobs ADD COLUMN base_revision')
        if stage not in ('seeded','associated'):
            complete = stage in ('reconciled','cold')
            db.execute('INSERT INTO sync_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (base.ID,OP,FILE,snapshots.NOTE,snapshots.HASH,len(runtime.BODY.encode()),'acked' if complete else 'committing',1 if complete else None,None,'put',0))
            db.execute('INSERT INTO sync_attempts VALUES(?,?,?,?,?)',(base.ID,OP,1,'failed','SERVICE_UNAVAILABLE'))
            db.execute('INSERT INTO mobile_sync_attempt_devices VALUES(?,?,?)',(base.ID,OP,ANDROID))
            if complete:
                r=original_receipt();r={k:v for k,v in r.items() if k not in ('device_id','fingerprint')}
                db.execute('UPDATE sync_bindings SET cursor=1')
                db.execute('INSERT INTO sync_heads VALUES(?,?,?,?,?)',(base.ID,FILE,1,snapshots.NOTE,snapshots.HASH))
                db.execute('INSERT INTO sync_inbox VALUES(?,?,?,?,?,?)',(base.ID,1,json.dumps(r),DRAFT,CONTROL,'applied'))
        db.commit()
    manifest=json.loads((raw/'snapshot.json').read_bytes());manifest['files'].pop('vault/release-owned-original.canvas')
    (raw/'snapshot.json').write_text(json.dumps(manifest),'utf-8');refresh_manifest(raw)
    return raw


def projection(root, stage):
    root.mkdir();return snapshots.project_recovery(original_fixture(root,stage),root/'working',stage=stage)


def providers():
    receipt=original_receipt();remote={k:v for k,v in receipt.items() if k!='vault_id'}
    before=dict(vault=dict(id=base.VAULT,sequence=0),rows=dict(revisions=[],files=[],objects=[],uploads=[],device_progress=[]),
        delivery=dict(counts={'changes:200':1},events=[]))
    after=deepcopy(before);after['vault']['sequence']=1
    after['rows'].update(revisions=[remote],objects=[dict(hash=snapshots.HASH,size=len(runtime.BODY.encode()))],
        files=[dict(file_id=FILE,sequence=1,path_key=snapshots.NOTE,deleted=0)])
    after['delivery']['counts']['revision:200']=1
    after['delivery']['events']=[dict(action='revision',service_status=200,delivery_status=503,fault_operation=CONTROL)]
    control=dict(operation_id=CONTROL,mode='drop_revision',expected_revision=0,result_revision=1)
    marker=dict(operation_id=CONTROL,mode='drop_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
    return before,after,control,marker


class ReleaseRecoveryChecks(unittest.TestCase):
    def test_auth_provider_requires_exact_Android_and_setup_sessions_without_new_login_refresh_or_logout(self):
        value=dict(users=[dict(devices=[dict(id=ANDROID),dict(id='b'*32)],
            session_counts=[dict(device_id=ANDROID,sessions=1),dict(device_id='b'*32,sessions=1)]),dict(devices=[],session_counts=[])],
            requests=dict(counts={'login:200':2}))
        runtime.validate_auth_provider(value,ANDROID,'b'*32)
        for mode in ('extra-login','refresh','extra-device','duplicate-device','session-erased','other-account'):
            changed=deepcopy(value)
            if mode=='extra-login':changed['requests']['counts']['login:200']=3
            if mode=='refresh':changed['requests']['counts']['refresh:200']=1
            if mode=='extra-device':changed['users'][0]['devices'].append(dict(id='c'*32))
            if mode=='duplicate-device':changed['users'][0]['devices'].append(dict(id=ANDROID))
            if mode=='session-erased':changed['users'][0]['session_counts'][0]['sessions']=0
            if mode=='other-account':changed['users'][1]['devices']=[dict(id='c'*32)]
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_auth_provider(changed,ANDROID,'b'*32)

    def test_actual_six_stage_SQLite_projection_only_opens_new_readonly_copies_and_keeps_originals(self):
        for stage in snapshots.STAGES:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=original_fixture(root,stage);before={str(f.relative_to(raw)):f.read_bytes() for f in raw.rglob('*') if f.is_file()}
                connect=sqlite3.connect;queries=[]
                def checked(path,*args,**kwargs):
                    self.assertIn('?mode=ro',str(path));self.assertNotIn('/raw/',str(path))
                    db=connect(path,*args,**kwargs);db.set_trace_callback(queries.append);return db
                with patch.object(sqlite3,'connect',side_effect=checked):
                    value=snapshots.project_recovery(raw,root/'working',stage=stage)
                self.assertEqual(before,{str(f.relative_to(raw)):f.read_bytes() for f in raw.rglob('*') if f.is_file()})
                self.assertNotIn('never-read-this',json.dumps(value));self.assertFalse(any('forbidden_credentials' in q for q in queries))
                self.assertEqual(value['all_Save_ids'],[OP]);self.assertFalse(value['native_RAM_invocation_id_observed'])

    def test_hidden_drafts_saves_foreign_transfer_and_relabelled_attempt_are_rejected(self):
        for mode in ('draft','save','frozen-Save-rewritten','foreign-job','extra-outbox','raw-error','relabelled-attempt','changed-commit-base'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=original_fixture(root,'unknown')
                which=0 if mode in ('draft','save','frozen-Save-rewritten') else 1
                with closing(sqlite3.connect(raw/snapshots.DATABASES[which])) as db:
                    if mode=='draft':db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',('foreign','foreign','other.md','',0,0,'hidden'))
                    if mode=='save':db.execute('INSERT INTO save_intents VALUES(?,?,?,?,?,?,?,?,?)',('other','other',0,'other','other.md','','hidden','prepared',None))
                    if mode=='frozen-Save-rewritten':db.execute('UPDATE save_intents SET file_id=?',(FILE,))
                    if mode=='foreign-job':db.execute('INSERT INTO sync_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',('foreign',OP,FILE,snapshots.NOTE,snapshots.HASH,1,'committing',None,None,'put',0))
                    if mode=='extra-outbox':db.execute('INSERT INTO outbox SELECT * FROM outbox')
                    if mode=='raw-error':db.execute('UPDATE sync_attempts SET error=?',('secret body must never be projected',))
                    if mode=='relabelled-attempt':db.execute("UPDATE sync_attempts SET outcome='succeeded'")
                    if mode=='changed-commit-base':db.execute('UPDATE sync_jobs SET base_revision=9')
                    db.commit()
                refresh_manifest(raw)
                with self.assertRaises(RuntimeError):snapshots.project_recovery(raw,root/'working',stage='unknown')

    def test_scope_changed_raw_bytes_or_working_copy_reuse_cannot_be_accepted(self):
        for mode in ('extra-auth','changed-note','not-stopped','nested-copy','reuse-copy'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=original_fixture(root,'associated');m=json.loads((raw/'snapshot.json').read_bytes());working=root/'working'
                if mode=='extra-auth':m['files']['mobile-sync-auth.sqlite3']=None
                if mode=='changed-note':(raw/'vault'/snapshots.NOTE).write_bytes(b'changed')
                if mode=='not-stopped':m['app_stopped_before_and_after']=False
                if mode=='nested-copy':working=raw/'working'
                if mode=='reuse-copy':working.mkdir()
                (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
                with self.assertRaises(RuntimeError):snapshots.project_recovery(raw,working,stage='associated')

    def test_cold_read_accepts_only_layout_change_and_preserves_every_original_ID(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'before','unknown');after=projection(root/'after','unknown-read')
            snapshots.same_recovery(before,after)
            after['raw_sha256'][snapshots.DATABASES[1]]='0'*64;snapshots.same_recovery(before,after)
            for table,key,value in [('attempts','attempts',2),('outbox','operation_id',CONTROL),('jobs','remote_revision',1)]:
                changed=deepcopy(after);changed['native'][table][0][key]=value
                with self.assertRaises(RuntimeError):snapshots.same_recovery(before,changed)

    def test_original_drop_validator_rejects_foreign_commit_marker_or_second_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'before','associated');after=projection(root/'after','unknown')
            p0,p1,control,marker=providers()
            self.assertEqual(runtime.validate_unknown(before,after,p0,p1,ANDROID,control,marker),original_receipt())
            for field,value in [('device_id','other'),('base_revision',1),('operation_id',CONTROL),('hash','0'*64),('size',0)]:
                changed=deepcopy(p1);changed['rows']['revisions'][0][field]=value
                with self.subTest(field=field),self.assertRaises(RuntimeError):runtime.validate_unknown(before,after,p0,changed,ANDROID,control,marker)
            for field,value in [('service_status',503),('service_response_complete',False),('raw_body_recorded',True),('operation_id',OP)]:
                with self.subTest(marker=field),self.assertRaises(RuntimeError):runtime.validate_unknown(before,after,p0,p1,ANDROID,control,dict(marker,**{field:value}))
            changed=deepcopy(p1);changed['delivery']['counts']['revision:200']=2
            with self.assertRaises(RuntimeError):runtime.validate_unknown(before,after,p0,changed,ANDROID,control,marker)

    def test_reconcile_needs_changes_read_same_original_and_preserved_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'before','unknown');after=projection(root/'after','reconciled')
            _,p0,_,_=providers();p1=deepcopy(p0);p1['delivery']['counts']['changes:200']+=1
            runtime.validate_reconciled(before,after,p0,p1,original_receipt())
            for table,key,value in [('attempts','outcome','succeeded'),('attempts','attempts',2),('issuers','device_id','other'),('outbox','operation_id',CONTROL)]:
                changed=deepcopy(after);changed['native'][table][0][key]=value
                with self.subTest(table=table,key=key),self.assertRaises(RuntimeError):runtime.validate_reconciled(before,changed,p0,p1,original_receipt())
            for mode in ('revision','object','upload','no-changes'):
                changed=deepcopy(p1)
                if mode=='revision':changed['delivery']['counts']['revision:200']+=1
                if mode=='object':changed['rows']['objects'].append(dict(hash='0'*64,size=2))
                if mode=='upload':changed['rows']['uploads'].append(dict(id='new'))
                if mode=='no-changes':changed['delivery']['counts']['changes:200']=p0['delivery']['counts']['changes:200']
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_reconciled(before,after,p0,changed,original_receipt())

    def test_unknown_original_Save_stops_before_login_setup_control_or_cycle(self):
        subject=object.__new__(runtime.ReleaseSyncRecovery);subject.prepare=Mock();subject.enable_test_ime=Mock();subject.restore_test_ime=Mock();subject.phase=Mock();subject.recovery_snapshot=Mock()
        with patch.object(runtime.ReleaseUpgrade,'phase',side_effect=TimeoutError()) as seed,patch.object(runtime,'Setup') as setup,patch.object(runtime,'arm_control') as control,self.assertRaises(TimeoutError):subject.run()
        seed.assert_called_once_with(subject,'markdown','recovery-seed-note');subject.restore_test_ime.assert_called_once()
        subject.phase.assert_not_called();subject.recovery_snapshot.assert_not_called();setup.assert_not_called();control.assert_not_called()

    def test_recovery_wrapper_restores_original_entry_after_unknown_without_relaunch(self):
        subject=object.__new__(runtime.ReleaseSyncRecovery);subject.driver_entry='ReleaseSyncRuntimeProbe'
        with patch.object(runtime,'run_hosted_recovery_cycle',side_effect=TimeoutError()) as hosted,self.assertRaises(TimeoutError):subject.phase('cycle-drop-original','drop-original')
        hosted.assert_called_once_with(subject,'cycle-drop-original','drop-original');self.assertEqual(subject.driver_entry,'ReleaseSyncRuntimeProbe')
        subject.driver_entry='foreign'
        with patch.object(runtime,'run_hosted_recovery_cycle') as hosted,self.assertRaises(RuntimeError):subject.phase('cycle-drop-original','drop-original')
        hosted.assert_not_called()


class OriginalDropContract(unittest.IsolatedAsyncioTestCase):
    async def test_actual_body_free_service200_delivery503_marker_matches_new_validator_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            owner=Path(folder);root=fixture.initialize_control(owner);fixture.freeze_target(owner,base.VAULT)
            control=fixture.arm_control(owner,CONTROL,'drop_revision',0);calls=[]
            old=delivery.OriginalSyncDeliveryTests()
            proxy=fixture.TargetedDeliveryProxy(old.app(calls,status=200,body=b'private-response-not-recorded'),owner)
            value=await old.request(proxy,path='/sync/v1/vaults/'+base.VAULT+'/revisions')
            self.assertEqual(value[0]['status'],503);self.assertEqual(len(calls),1)
            marker=json.loads((root/'fault-events'/(CONTROL+'.json')).read_bytes())
            self.assertEqual(marker,providers()[3]);self.assertEqual(control,providers()[2])
            self.assertFalse(any(b'private-response-not-recorded' in f.read_bytes() for f in root.rglob('*.json')))


if __name__ == '__main__':unittest.main()
