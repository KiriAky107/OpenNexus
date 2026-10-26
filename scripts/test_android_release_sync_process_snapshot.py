"""Synthetic SQLite originals and the fixed open SQL; no Android/process/PG/S3 run."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import android_release_sync_process_snapshot as snapshots
import android_release_sync_process_validation as validation
import android_release_sync_stop_snapshot as old_stop
import android_release_sync_recovery_snapshot as old_recovery
from test_android_release_sync_stop_snapshot import (original_fixture as stop_fixture,refresh,providers,receipt,
    SAVE,UPLOAD,FILE,DRAFT,ASSOCIATION,BINDING,HOLD,RELEASE,DEVICE,VAULT)

PRODUCT='d118b2ba63c922142f9fadee8772b6ec15791f48'
IDENTITY=dict(avd='opennexus-ci-17-1',qemu='1',api='36',build_type='userdebug',debuggable='1',boot_id=ASSOCIATION)


def original_fixture(root,stage,*,outcome='running',wal=False):
    mapped={'killed':'stopped','killed-read':'stopped-read'}.get(stage,stage)
    raw,writers=stop_fixture(root,mapped,wal=wal)
    if stage=='killed':
        db=writers[1] if writers else sqlite3.connect(raw/snapshots.DATABASES[1])
        db.execute('UPDATE sync_attempts SET outcome=?',(outcome,));db.commit()
        if not writers:db.close()
        refresh(raw)
    manifest=json.loads((raw/'snapshot.json').read_bytes())
    manifest.update(identity=IDENTITY,normal_phone_permissions_verified=False)
    (raw/'snapshot.json').write_text(json.dumps(manifest),'utf-8')
    return raw,writers


def projection(root,stage,*,outcome='running'):
    root.mkdir();raw,_=original_fixture(root,stage,outcome=outcome)
    return snapshots.project_process(raw,root/'working',stage=stage)


def retained(raw):return {f.relative_to(raw).as_posix():f.read_bytes() for f in raw.rglob('*') if f.is_file()}


def mutate(raw,sql,args=(),*,draft=False):
    # Only new synthetic fixtures are generated here, before projection freezes
    # them. The projector itself never opens an original for SQLite writes.
    with closing(sqlite3.connect(raw/snapshots.DATABASES[0 if draft else 1])) as db:db.execute(sql,args);db.commit()
    refresh(raw)


class ProcessProjection(unittest.TestCase):
    def test_all_six_stages_and_both_raw_killed_outcomes_use_only_new_readonly_copies(self):
        for stage,outcome in [(s,'running') for s in snapshots.STAGES]+[('killed','interrupted')]:
            with self.subTest(stage=stage,outcome=outcome),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,stage,outcome=outcome);original=retained(raw)
                queries=[];tables=[];connect=sqlite3.connect
                def checked(path,*args,**kwargs):
                    self.assertIn('?mode=ro',str(path));self.assertIn('/working/',str(path));self.assertNotIn('/raw/',str(path))
                    db=connect(path,*args,**kwargs);db.set_trace_callback(queries.append)
                    def authorizer(action,table,column,*other):
                        if action==sqlite3.SQLITE_READ:tables.append(table)
                        return sqlite3.SQLITE_OK
                    db.set_authorizer(authorizer);return db
                with patch.object(sqlite3,'connect',side_effect=checked):value=snapshots.project_process(raw,root/'working',stage=stage)
                self.assertEqual(original,retained(raw));self.assertNotIn('forbidden_credentials',tables)
                self.assertFalse(any('SELECT *' in q or q.startswith('UPDATE ') for q in queries))
                self.assertFalse(value['credentials_selected']);self.assertFalse(value['native_RAM_invocation_id_observed'])
                self.assertNotIn('private-sentinel',json.dumps(value));self.assertTrue(value['original_manifest_scope_verified'])
                self.assertEqual(value['all_Save_ids'],[SAVE])
                if stage=='killed':self.assertEqual(value['native']['attempts'][0]['outcome'],outcome)
                elif stage not in ('seeded','associated'):self.assertEqual(value['native']['attempts'][0]['outcome'],'interrupted')

    def test_real_DB_WAL_SHM_running_original_bytes_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,writers=original_fixture(root,'killed',wal=True)
            try:
                original=retained(raw)
                self.assertTrue(all((raw/(db+'-wal')).is_file() and (raw/(db+'-shm')).is_file() for db in snapshots.DATABASES))
                value=snapshots.project_process(raw,root/'working',stage='killed')
                self.assertEqual(len(value['raw_sha256']),7);self.assertEqual(value['native']['attempts'][0]['outcome'],'running')
                self.assertEqual(original,retained(raw))
            finally:
                for db in writers:db.close()

    def test_fixed_product_open_SQL_on_separate_synthetic_fixture_only_changes_running_outcome(self):
        source=subprocess.check_output(['git','show',PRODUCT+':frontend/src-tauri/src/workspace.rs'],cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(hashlib.sha256(source).hexdigest(),'50e0a7dab5fe5a650ff482a1c5ffa1e74ceab4b8b6da92bfdc12da6aa1345b80')
        match=re.search(r'db\.execute_batch\("(UPDATE sync_attempts SET outcome=CASE WHEN EXISTS\(SELECT 1 FROM sync_jobs j WHERE j\.binding=sync_attempts\.binding.*? COMMIT;)"\)\?;',source.decode('utf-8'),re.S)
        self.assertIsNotNone(match);sql=match[1]
        for outcome in ('running','interrupted'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);first=root/'preopen';first.mkdir();raw,_=original_fixture(first,'killed',outcome=outcome)
                original=retained(raw);second=root/'opened';second.mkdir();opened=second/'raw';shutil.copytree(raw,opened)
                with closing(sqlite3.connect(opened/snapshots.DATABASES[1])) as db:db.executescript('BEGIN;'+sql)
                refresh(opened)
                before=snapshots.project_process(raw,first/'working',stage='killed')
                after=snapshots.project_process(opened,second/'working',stage='killed-read')
                result=snapshots.preserve_open(before,after)
                self.assertEqual(result['from_outcome'],outcome);self.assertEqual(result['to_outcome'],'interrupted')
                self.assertEqual(result['operation_id'],UPLOAD);self.assertEqual(result['attempts'],1)
                self.assertFalse(result['Native_RAM_invocation_id_observed']);self.assertEqual(original,retained(raw))

    def test_open_pair_rejects_other_record_changes_without_mutating_input_projections(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'before','killed');after=projection(root/'after','killed-read')
            original=deepcopy(before);snapshots.preserve_open(before,after);self.assertEqual(before,original)
            for mode in ('count','error','attempt-id','job-state','issuer','upload-handle','Save-archive','file-bytes','identity','not-raw-killed'):
                a,b=deepcopy((before,after))
                if mode=='count':b['native']['attempts'][0]['attempts']=2
                if mode=='error':b['native']['attempts'][0]['error']='PRIVATE_ERROR'
                if mode=='attempt-id':b['native']['attempts'][0]['operation_id']=RELEASE
                if mode=='job-state':b['native']['jobs'][0]['state']='acked'
                if mode=='issuer':b['native']['issuers'][0]['device_id']='f'*32
                if mode=='upload-handle':b['original_job_commit'][0]['upload_id']='replacement'
                if mode=='Save-archive':b['native']['outbox'][0]['state']='pending'
                if mode=='file-bytes':b['raw_sha256']['vault/'+snapshots.NOTE]='f'*64
                if mode=='identity':b['owned_snapshot_identity']={'boot_id':HOLD}
                if mode=='not-raw-killed':a['process_stage']='associated'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):snapshots.preserve_open(a,b)

    def test_only_raw_killed_accepts_running_and_all_attempt_error_count_job_boundaries_stay_closed(self):
        for stage in ('killed-read','reconciled','cold'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,stage);mutate(raw,"UPDATE sync_attempts SET outcome='running'")
                with self.assertRaises(RuntimeError):snapshots.project_process(raw,root/'working',stage=stage)
        for sql in ("UPDATE sync_attempts SET outcome='failed'","UPDATE sync_attempts SET outcome='succeeded'",'UPDATE sync_attempts SET attempts=2',
            "UPDATE sync_attempts SET error='private-error-body'","UPDATE sync_jobs SET error='private-error-body'",
            'UPDATE sync_jobs SET base_revision=9',"UPDATE sync_jobs SET operation='delete'",'UPDATE sync_jobs SET remote_revision=1',
            'UPDATE sync_bindings SET cursor=1'):
            with self.subTest(sql=sql),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'killed');mutate(raw,sql)
                with self.assertRaises(RuntimeError):snapshots.project_process(raw,root/'working',stage='killed')

    def test_original_NULL_Save_archived_outbox_and_distinct_initial_upload_lineage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=projection(root/'seed','seeded');after=projection(root/'associated','associated')
            snapshots.preserve_initial_outbox(before,after)
            for stage in ('killed','killed-read','reconciled','cold'):
                value=projection(root/stage,stage);snapshots.preserve_note(before,value)
                self.assertIsNone(value['saves'][snapshots.NOTE][0]['file_id'])
                self.assertEqual(value['native']['outbox'][0],after['native']['outbox'][0])
                self.assertEqual(value['original_association_upload_id'],UPLOAD)
            for mode in ('old-state','old-id','new-id','new-hash','missing-archive'):
                bad=deepcopy(after)
                if mode=='old-state':bad['native']['outbox'][0]['state']='pending'
                if mode=='old-id':bad['native']['outbox'][0]['operation_id']=HOLD
                if mode=='new-id':bad['native']['outbox'][1]['operation_id']=SAVE
                if mode=='new-hash':bad['native']['outbox'][1]['hash']='0'*64
                if mode=='missing-archive':bad['native']['outbox'].pop(0)
                with self.subTest(mode=mode),self.assertRaises((RuntimeError,ValueError)):snapshots.preserve_initial_outbox(before,bad)

    def test_hidden_draft_Save_outbox_association_transfer_binding_and_65_rows_are_refused(self):
        for mode in ('draft','save','NULL-Save-rewritten','third-outbox','hidden-association','foreign-job','foreign-attempt','foreign-issuer','many-rows'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'killed')
                if mode=='draft':mutate(raw,'INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',(RELEASE,FILE,'hidden.md','',0,0,'private-foreign-content'),draft=True)
                if mode=='save':mutate(raw,'INSERT INTO save_intents SELECT * FROM save_intents',draft=True)
                if mode=='NULL-Save-rewritten':mutate(raw,'UPDATE save_intents SET file_id=?',(FILE,),draft=True)
                if mode=='third-outbox':mutate(raw,'INSERT INTO outbox SELECT * FROM outbox WHERE operation_id=?',(SAVE,))
                if mode=='hidden-association':mutate(raw,'INSERT INTO mobile_sync_binding_intents SELECT * FROM mobile_sync_binding_intents')
                if mode=='foreign-job':mutate(raw,'UPDATE sync_jobs SET binding=?',(RELEASE,))
                if mode=='foreign-attempt':mutate(raw,'UPDATE sync_attempts SET binding=?',(RELEASE,))
                if mode=='foreign-issuer':mutate(raw,'UPDATE mobile_sync_attempt_devices SET binding=?',(RELEASE,))
                if mode=='many-rows':
                    for _ in range(64):mutate(raw,'INSERT INTO sync_attempts SELECT * FROM sync_attempts LIMIT 1')
                with self.assertRaises(RuntimeError):snapshots.project_process(raw,root/'working',stage='killed')

    def test_manifest_private_files_duplicates_bounds_identity_and_flags_are_refused_before_SQL(self):
        for mode in ('extra-auth','extra-original','duplicate-key','byte-bool','bad-hash','not-stopped','copied-auth','extra-key','identity','oversize'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');m=json.loads((raw/'snapshot.json').read_bytes())
                if mode=='extra-auth':m['files']['sync-auth.sqlite3']=None
                if mode=='extra-original':(raw/'private-auth-unapproved').write_bytes(b'never-read')
                if mode=='byte-bool':m['files']['vault/'+snapshots.NOTE]['bytes']=True
                if mode=='bad-hash':m['files']['vault/'+snapshots.NOTE]['sha256']='not-a-hash'
                if mode=='not-stopped':m['app_stopped_before_and_after']=False
                if mode=='copied-auth':m['credential_store_copied']=True
                if mode=='extra-key':m['unknown_private_value']='never-read'
                if mode=='identity':m['identity']=dict(avd='unowned',qemu='1',api='36',build_type='userdebug',debuggable='1',boot_id=HOLD)
                encoded=json.dumps(m)
                if mode=='duplicate-key':encoded=encoded[:-1]+',"package":"cc.kronecker.opennexus"}'
                if mode=='oversize':encoded+=' '*16384
                (raw/'snapshot.json').write_text(encoded,'utf-8')
                with patch.object(sqlite3,'connect') as sql,self.assertRaises(RuntimeError):snapshots.project_process(raw,root/'working',stage='associated')
                sql.assert_not_called();self.assertFalse((root/'working').exists())

    def test_alias_nested_reused_working_and_linked_parent_admission_precedes_SQL(self):
        for mode in ('nested','reused','alias','linked-parent'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');working=root/'working'
                if mode=='nested':working=raw/'working'
                if mode=='reused':working.mkdir()
                if mode=='alias':working=root/'alias'/'..'/'working'
                original_symlink=Path.is_symlink
                def linked(path):return mode=='linked-parent' and path==raw/'vault' or original_symlink(path)
                with patch.object(Path,'is_symlink',linked),patch.object(sqlite3,'connect') as sql,self.assertRaises(RuntimeError):
                    snapshots.project_process(raw,working,stage='associated')
                sql.assert_not_called()

    def test_association_and_inbox_JSON_duplicate_fields_and_forged_revisions_are_refused(self):
        for mode in ('association-duplicate','receipt-duplicate','revision-duplicate','revision-extra','wrong-operation','bool-sequence'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'reconciled')
                with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                    if mode in ('association-duplicate','receipt-duplicate'):
                        column='request' if mode=='association-duplicate' else 'binding'
                        text=db.execute('SELECT '+column+' FROM mobile_sync_binding_intents').fetchone()[0]
                        key='operation_id' if column=='request' else 'id';value=json.loads(text)[key]
                        db.execute('UPDATE mobile_sync_binding_intents SET '+column+'=?',(text[:-1]+','+json.dumps(key)+':'+json.dumps(value)+'}',))
                    else:
                        text=db.execute('SELECT revision FROM sync_inbox').fetchone()[0];value=json.loads(text)
                        if mode=='revision-extra':value['password']='private-sentinel'
                        if mode=='wrong-operation':value['operation_id']=RELEASE
                        if mode=='bool-sequence':value['sequence']=True
                        encoded=text[:-1]+',"sequence":1}' if mode=='revision-duplicate' else json.dumps(value)
                        db.execute('UPDATE sync_inbox SET revision=?',(encoded,))
                    db.commit()
                refresh(raw)
                with self.assertRaises(RuntimeError):snapshots.project_process(raw,root/'working',stage='reconciled')

    def test_old_Stop_and_recovery_contracts_still_refuse_the_same_raw_running_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,_=original_fixture(root,'killed')
            with self.assertRaises(RuntimeError):old_stop.project_stop(raw,root/'old-stop-working',stage='stopped')
            with self.assertRaises(RuntimeError):old_recovery.project_recovery(raw,root/'old-recovery-working',stage='unknown')
            value=snapshots.project_process(raw,root/'new-working',stage='killed')
            self.assertEqual(value['native']['attempts'][0]['outcome'],'running')

    def test_final_cold_pair_keeps_logical_records_and_note_bytes_despite_DB_layout_hash_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','reconciled');b=projection(root/'b','cold')
            b['raw_sha256'][snapshots.DATABASES[1]]='f'*64;snapshots.preserve_read(a,b)
            for mode in ('attempt','upload','bytes','wrong-pair'):
                before,after=deepcopy((a,b))
                if mode=='attempt':after['native']['attempts'][0]['outcome']='succeeded'
                if mode=='upload':after['original_job_commit'][0]['upload_id']='substituted'
                if mode=='bytes':after['raw_sha256']['vault/'+snapshots.NOTE]='f'*64
                if mode=='wrong-pair':before['process_stage']='killed';after['process_stage']='killed-read'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):snapshots.preserve_read(before,after)

    def test_original_identity_and_normal_phone_scope_fields_are_required_before_copy(self):
        for field in ('identity','normal_phone_permissions_verified'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');m=json.loads((raw/'snapshot.json').read_bytes());del m[field]
                (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
                with self.assertRaises(RuntimeError):snapshots._admit_original(raw,root/'working')
                self.assertFalse((root/'working').exists())

    def test_cross_stage_same_bytes_and_records_cannot_substitute_another_owned_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);associated=projection(root/'associated','associated')
            target=root/'killed';target.mkdir();raw,_=original_fixture(target,'killed')
            m=json.loads((raw/'snapshot.json').read_bytes());m['identity']['boot_id']=HOLD
            (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
            killed=snapshots.project_process(raw,target/'working',stage='killed')
            p0,p1,hold,held,release,released=providers()
            with self.assertRaises(RuntimeError):validation.validate_killed(associated,killed,p0,p1,DEVICE,hold,held,release,released)


class ProcessValidators(unittest.TestCase):
    def test_once_held_commit_accepts_only_the_two_observed_raw_attempt_outcomes(self):
        for outcome in ('running','interrupted'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);associated=projection(root/'associated','associated');killed=projection(root/'killed','killed',outcome=outcome)
                p0,p1,hold,held,release,released=providers()
                self.assertEqual(validation.validate_killed(associated,killed,p0,p1,DEVICE,hold,held,release,released),receipt())
                for mode in ('delivery503','extra-event','counter','issuer','client-ACK','attempt','cursor','provider-used-bool'):
                    native=deepcopy(killed);provider=deepcopy(p1);delivered=deepcopy(released)
                    if mode=='delivery503':provider['delivery']['events'][0]['delivery_status']=503
                    if mode=='extra-event':provider['delivery']['events'].append(deepcopy(provider['delivery']['events'][0]))
                    if mode=='counter':provider['delivery']['counts']['revision:200']=2
                    if mode=='issuer':native['native']['issuers'][0]['device_id']='f'*32
                    if mode=='client-ACK':delivered['client_receipt_known']=True
                    if mode=='attempt':native['native']['attempts'][0]['outcome']='succeeded'
                    if mode=='cursor':native['native']['binding']['cursor']=1
                    if mode=='provider-used-bool':provider['vault']['used']=True
                    with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_killed(associated,native,p0,provider,DEVICE,hold,held,release,delivered)

    def test_read_only_open_requires_exact_provider_and_only_same_attempt_outcome_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','killed');b=projection(root/'b','killed-read');_,provider,*_=providers()
            result=validation.validate_open_read(a,b,provider,deepcopy(provider));self.assertEqual(result['from_outcome'],'running')
            bad=deepcopy(provider);bad['delivery']['counts']['changes:200']+=1
            with self.assertRaises(RuntimeError):validation.validate_open_read(a,b,provider,bad)

    def test_explicit_changes_read_preserves_original_attempt_and_all_write_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','killed-read');b=projection(root/'b','reconciled');_,p0,*_=providers()
            p1=deepcopy(p0);p1['delivery']['counts']['changes:200']+=1
            validation.validate_reconciled(a,b,p0,p1,receipt())
            for mode in ('attempt','count','upload','Save','new-id','revision','object','new-upload','no-changes','event','not-open-read'):
                before,after,provider=deepcopy((a,b,p1))
                if mode=='attempt':after['native']['attempts'][0]['outcome']='succeeded'
                if mode=='count':after['native']['attempts'][0]['attempts']=2
                if mode=='upload':after['original_job_commit'][0]['upload_id']='replacement'
                if mode=='Save':after['native']['outbox'][0]['state']='pending'
                if mode=='new-id':after['native']['outbox'][1]['operation_id']=RELEASE
                if mode=='revision':provider['delivery']['counts']['revision:200']=2
                if mode=='object':provider['rows']['objects'].append(dict(hash='f'*64,size=1))
                if mode=='new-upload':provider['rows']['uploads']=[dict(id='new')]
                if mode=='no-changes':provider['delivery']['counts']['changes:200']=p0['delivery']['counts']['changes:200']
                if mode=='event':provider['delivery']['events'].append(deepcopy(provider['delivery']['events'][0]))
                if mode=='not-open-read':before['process_stage']='killed'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_reconciled(before,after,p0,provider,receipt())

    def test_lifecycle_journals_bind_same_command_lifetime_boot_parent_and_driver_until_release(self):
        import android_sync_process_host as host
        parent=dict(schema=1,phase=host.LOSS,parent_intent_id=SAVE,input_intent_id=UPLOAD,native_invocation_id_observed=False)
        lifetime=dict(package=host.APP,pid=4242,uid=12345,start_ticks=5678)
        identity=dict(avd='opennexus-ci-17-1',qemu='1',api='36',build_type='userdebug',debuggable='1',boot_id=ASSOCIATION)
        ready=dict(schema=1,phase=host.LOSS,action='cycle-start',parent_intent_id=SAVE,driver_intent_id=UPLOAD,package=host.APP,
            uid=12345,window=4,original=[100,200,500,600],visible=[100,200,500,600],x=300,y=400,elapsed_ms=1000)
        loss=dict(schema=1,phase=host.LOSS,parent_intent_id=SAVE,input_intent_id=UPLOAD,package=host.APP,uid=12345,window=4,
            elapsed_ms=1500,product_UI_observation_closed=True,process_absence_verified=False)
        intent=dict(operation_id=RELEASE,arguments=['shell','-T','am force-stop --user 0 '+host.APP],parent_intent_id=SAVE,input_intent_id=UPLOAD,
            original_lifetime=lifetime,owned_boot=identity,held_operation_id=HOLD,product_UI_observation_closed=True,
            automatic_retry=False,ordinary_cleanup_is_not_tested_process_loss=True,native_invocation_id_observed=False)
        empty=hashlib.sha256(b'').hexdigest()
        result=dict(returncode=0,unknown_result=False,automatic_retry=False,unvalidated_streams_exported=False,original_operation_id=RELEASE,
            stdout_bytes=0,stdout_sha256=empty,stderr_bytes=0,stderr_sha256=empty)
        owner=dict(original_lifetime=lifetime,same_original_foreground_PID_UID_boot=True,original_instrumentation_alive=True,ready_age_ms=200,ready_age_bound_ms=2000)
        absence=dict(package=host.APP,original_pid=4242,product_uid=12345,original_pid_absent=True,all_owned_package_processes_absent=True,
            user0_force_stopped=True,same_owned_root_boot=True,original_driver_alive=True,original_driver_completed=False,
            raw_process_list_exported=False,native_cycle_acknowledged=False)
        final=dict(absence,original_driver_alive=False,original_driver_completed=True)
        phase=dict(phase=host.LOSS,label='original',pid=4242,passed=True,original_parent_intent_id=SAVE,original_input_intent_id=UPLOAD,
            actual_process_absence_observed=True,same_live_instrumentation_through_original_release=True,Native_RAM_invocation_id_observed=False,
            native_original_state_verified=False,client_receipt_known=False)
        args=[phase,parent,ready,loss,intent,result,owner,absence,final,identity,HOLD]
        verified=validation.validate_lifecycle(*args);self.assertFalse(verified['native_apply_ACK'])
        self.assertFalse(verified['input_ready_freshness_independently_recomputed'])
        for mode in ('command','operation','start-bool','ready-parent','window','age-bool','age-late','unknown-result','bad-stream',
            'not-absent','early-driver','final-still-alive','wrong-boot','phase-ACK','phase-input'):
            v=deepcopy(args)
            if mode=='command':v[4]['arguments'][-1]+='; another-command'
            if mode=='operation':v[4]['operation_id']=SAVE
            if mode=='start-bool':v[4]['original_lifetime']['start_ticks']=True
            if mode=='ready-parent':v[2]['parent_intent_id']=FILE
            if mode=='window':v[3]['window']=5
            if mode=='age-bool':v[6]['ready_age_ms']=True
            if mode=='age-late':v[6]['ready_age_ms']=2001
            if mode=='unknown-result':v[5]['unknown_result']=True
            if mode=='bad-stream':v[5]['stdout_bytes']=1
            if mode=='not-absent':v[7]['original_pid_absent']=False
            if mode=='early-driver':v[7]['original_driver_alive']=False
            if mode=='final-still-alive':v[8]['original_driver_alive']=True
            if mode=='wrong-boot':v[9]=dict(v[9],boot_id=DRAFT)
            if mode=='phase-ACK':v[0]['native_original_state_verified']=True
            if mode=='phase-input':v[0]['original_input_intent_id']=FILE
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_lifecycle(*v)


if __name__=='__main__':unittest.main()
