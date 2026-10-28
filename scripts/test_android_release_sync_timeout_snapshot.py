"""Synthetic SQLite originals and controlled host records, never Android or PG/S3."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import re
import tempfile
import unittest
from unittest.mock import patch

import android_release_sync_timeout_snapshot as snapshots
import android_release_sync_timeout_validation as validation
import android_release_sync_stop_snapshot as old_stop
import android_release_sync_process_snapshot as old_process
from test_android_release_sync_process_snapshot import IDENTITY, retained, mutate
from test_android_release_sync_stop_snapshot import (original_fixture as stop_fixture, refresh, providers, receipt,
    SAVE, UPLOAD, FILE, DRAFT, ASSOCIATION, HOLD, RELEASE, DEVICE)
from test_android_sync_timeout_host import Harness


def original_fixture(root, stage, *, wal=False):
    mapped = {'timed-out':'stopped','timed-out-read':'stopped-read'}.get(stage,stage)
    raw, writers = stop_fixture(root,mapped,wal=wal)
    # Generate new synthetic input before it is frozen as an original. No
    # projector or validator writes, opens or relabels an observed original.
    if stage not in ('seeded','associated'):
        db = writers[1] if writers else sqlite3.connect(raw/snapshots.DATABASES[1])
        db.execute("UPDATE sync_attempts SET outcome='failed',error='SYNC_NETWORK_ERROR'"); db.commit()
        if not writers: db.close()
        refresh(raw)
    manifest = json.loads((raw/'snapshot.json').read_bytes())
    manifest.update(identity=IDENTITY,normal_phone_permissions_verified=False)
    (raw/'snapshot.json').write_text(json.dumps(manifest),'utf-8')
    return raw,writers


def projection(root, stage):
    root.mkdir(); raw,_ = original_fixture(root,stage)
    return snapshots.project_timeout(raw,root/'working',stage=stage)


def host_records(root):
    h = Harness(root); h.execute()
    records = {p.name[len('original-'):-5]:json.loads(p.read_bytes()) for p in h.subject.output.glob('original-*.json')}
    return h.subject.phases[-1], records, (h.subject.output/'original-instrument.stdout').read_bytes(),h.held.control,h.held.release_control


class TimeoutProjection(unittest.TestCase):
    def test_original_changed_during_final_closed_metadata_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,_=original_fixture(root,'timed-out');original_rows=snapshots.rows
            def changed(db,query,args=()):
                result=original_rows(db,query,args)
                if query.startswith('SELECT binding,operation_id,file_id,operation,base_revision,upload_id '):
                    (raw/'vault'/snapshots.NOTE).write_bytes(b'changed-after-common-reader')
                return result
            with patch.object(snapshots,'rows',side_effect=changed),self.assertRaisesRegex(RuntimeError,'RETAINED_ORIGINAL_CHANGED'):
                snapshots.project_timeout(raw,root/'working',stage='timed-out')

    def test_fixed_product_open_SQL_preserves_failed_network_attempt_on_new_synthetic_fixture(self):
        product='d118b2ba63c922142f9fadee8772b6ec15791f48'
        source=subprocess.check_output(['git','show',product+':frontend/src-tauri/src/workspace.rs'],cwd=Path(__file__).resolve().parent.parent)
        # Match the exact source statement rather than emulate Workspace::open.
        match=re.search(r'db\.execute_batch\("(UPDATE sync_attempts SET outcome=CASE WHEN EXISTS\(SELECT 1 FROM sync_jobs j WHERE j\.binding=sync_attempts\.binding.*? COMMIT;)"\)\?;',source.decode('utf-8'),re.S)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,_=original_fixture(root,'timed-out')
            # This executes fixed SQL on a separate synthetic database, never
            # on an observed original or as a claim of a Rust/Android open.
            import shutil
            synthetic=root/'synthetic-open';shutil.copytree(raw,synthetic)
            with closing(sqlite3.connect(synthetic/snapshots.DATABASES[1])) as db:db.executescript('BEGIN;'+match[1])
            refresh(synthetic)
            before=snapshots.project_timeout(raw,root/'working-before',stage='timed-out')
            after=snapshots.project_timeout(synthetic,root/'working-after',stage='timed-out-read')
            snapshots.preserve_read(before,after)

    def test_six_stages_only_open_new_readonly_copies_and_preserve_every_original(self):
        for stage in snapshots.STAGES:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,stage);original=retained(raw);queries=[];tables=[];connect=sqlite3.connect
                def checked(path,*args,**kwargs):
                    self.assertIn('?mode=ro',str(path));self.assertIn('/working/',str(path));self.assertNotIn('/raw/',str(path))
                    db=connect(path,*args,**kwargs);db.set_trace_callback(queries.append)
                    def authorizer(action,table,column,*other):
                        if action==sqlite3.SQLITE_READ:tables.append(table)
                        return sqlite3.SQLITE_OK
                    db.set_authorizer(authorizer);return db
                with patch.object(sqlite3,'connect',side_effect=checked):value=snapshots.project_timeout(raw,root/'working',stage=stage)
                self.assertEqual(original,retained(raw));self.assertNotIn('forbidden_credentials',tables)
                self.assertFalse(any('SELECT *' in q or q.startswith('UPDATE ') for q in queries))
                self.assertNotIn('private-sentinel',json.dumps(value));self.assertFalse(value['credentials_selected'])
                self.assertFalse(value['native_RAM_invocation_id_observed']);self.assertEqual(value['all_Save_ids'],[SAVE])
                self.assertEqual(value['owned_snapshot_identity'],IDENTITY)
                if stage not in ('seeded','associated'):
                    self.assertEqual(value['native']['attempts'],[dict(operation_id=UPLOAD,attempts=1,outcome='failed',error='SYNC_NETWORK_ERROR')])

    def test_real_DB_WAL_SHM_network_failure_bytes_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,writers=original_fixture(root,'timed-out',wal=True)
            try:
                original=retained(raw)
                self.assertTrue(all((raw/(db+'-wal')).is_file() and (raw/(db+'-shm')).is_file() for db in snapshots.DATABASES))
                value=snapshots.project_timeout(raw,root/'working',stage='timed-out')
                self.assertEqual(len(value['raw_sha256']),7);self.assertEqual(original,retained(raw))
            finally:
                for db in writers:db.close()

    def test_NULL_Save_archive_and_distinct_initial_upload_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);seed=projection(root/'seed','seeded');associated=projection(root/'associated','associated')
            snapshots.preserve_initial_outbox(seed,associated)
            for stage in snapshots.STAGES[2:]:
                value=projection(root/stage,stage);snapshots.preserve_note(seed,value)
                self.assertIsNone(value['saves'][snapshots.NOTE][0]['file_id'])
                self.assertEqual(value['native']['outbox'][0],associated['native']['outbox'][0])
                self.assertEqual(value['original_association_upload_id'],UPLOAD)
            for field,value in (('state','pending'),('operation_id',RELEASE),('hash','0'*64)):
                bad=deepcopy(associated);bad['native']['outbox'][0][field]=value
                with self.subTest(field=field),self.assertRaises(RuntimeError):snapshots.preserve_initial_outbox(seed,bad)

    def test_network_error_is_exact_and_never_relabels_failed_attempt_after_ACK(self):
        for stage in snapshots.STAGES[2:]:
            for sql in ("UPDATE sync_attempts SET outcome='running'","UPDATE sync_attempts SET outcome='interrupted'",
                    "UPDATE sync_attempts SET outcome='succeeded'","UPDATE sync_attempts SET error=NULL",
                    "UPDATE sync_attempts SET error='OWNED_AUTH_RESPONSE_DROPPED'","UPDATE sync_attempts SET error='private body'",
                    'UPDATE sync_attempts SET attempts=2',"UPDATE sync_jobs SET error='SYNC_NETWORK_ERROR'"):
                with self.subTest(stage=stage,sql=sql),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder);raw,_=original_fixture(root,stage);mutate(raw,sql)
                    with self.assertRaises(RuntimeError):snapshots.project_timeout(raw,root/'working',stage=stage)

    def test_no_premature_application_or_commit_payload_replacement(self):
        for sql in ('UPDATE sync_jobs SET remote_revision=1','UPDATE sync_jobs SET base_revision=1',
                "UPDATE sync_jobs SET operation='delete'","UPDATE outbox SET state='acked' WHERE state='queued'",
                'UPDATE sync_bindings SET cursor=1',"INSERT INTO sync_heads VALUES('x','x',1,'release-owned-note.md','x')"):
            with self.subTest(sql=sql),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'timed-out');mutate(raw,sql)
                with self.assertRaises(RuntimeError):snapshots.project_timeout(raw,root/'working',stage='timed-out')

    def test_hidden_rows_and_foreign_bindings_and_row65_refused(self):
        for mode in ('draft','save','NULL-rewrite','third-outbox','association','foreign-job','foreign-attempt','foreign-issuer','65'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'timed-out')
                if mode=='draft':mutate(raw,'INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',(RELEASE,FILE,'hidden.md','',0,0,'private-foreign-body'),draft=True)
                if mode=='save':mutate(raw,'INSERT INTO save_intents SELECT * FROM save_intents',draft=True)
                if mode=='NULL-rewrite':mutate(raw,'UPDATE save_intents SET file_id=?',(FILE,),draft=True)
                if mode=='third-outbox':mutate(raw,'INSERT INTO outbox SELECT * FROM outbox WHERE operation_id=?',(SAVE,))
                if mode=='association':mutate(raw,'INSERT INTO mobile_sync_binding_intents SELECT * FROM mobile_sync_binding_intents')
                if mode.startswith('foreign-'):
                    table={'foreign-job':'sync_jobs','foreign-attempt':'sync_attempts','foreign-issuer':'mobile_sync_attempt_devices'}[mode]
                    mutate(raw,'UPDATE '+table+' SET binding=?',(RELEASE,))
                if mode=='65':
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                        for _ in range(64):db.execute('INSERT INTO sync_attempts SELECT * FROM sync_attempts LIMIT 1')
                        db.commit()
                    refresh(raw)
                with self.assertRaises(RuntimeError):snapshots.project_timeout(raw,root/'working',stage='timed-out')

    def test_private_manifest_bad_identity_duplicates_and_metadata_rejected_before_SQL(self):
        for mode in ('extra-file','private-file','duplicate','bytes-bool','not-stopped','auth','missing-identity','identity','extra-field'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');m=json.loads((raw/'snapshot.json').read_bytes())
                if mode=='extra-file':m['files']['private-auth.sqlite3']=None
                if mode=='private-file':(raw/'private-auth').write_bytes(b'never-read')
                if mode=='bytes-bool':m['files']['vault/'+snapshots.NOTE]['bytes']=True
                if mode=='not-stopped':m['app_stopped_before_and_after']=False
                if mode=='auth':m['credential_store_copied']=True
                if mode=='missing-identity':m.pop('identity')
                if mode=='identity':m['identity']=dict(IDENTITY,api='35')
                if mode=='extra-field':m['raw_UI']='private'
                raw_json=json.dumps(m)
                if mode=='duplicate':raw_json=raw_json[:-1]+',"package":"cc.kronecker.opennexus"}'
                (raw/'snapshot.json').write_text(raw_json,'utf-8')
                with patch.object(sqlite3,'connect') as sql,self.assertRaises(RuntimeError):snapshots.project_timeout(raw,root/'working',stage='associated')
                sql.assert_not_called();self.assertFalse((root/'working').exists())

    def test_alias_reused_working_and_linked_parent_refused_before_SQL(self):
        for mode in ('alias','reused','nested','linked-parent'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'associated');working=root/'working'
                if mode=='alias':working=root/'alias'/'..'/'working'
                if mode=='reused':working.mkdir()
                if mode=='nested':working=raw/'working'
                is_link=Path.is_symlink
                with patch.object(Path,'is_symlink',lambda p:mode=='linked-parent' and p==raw/'vault' or is_link(p)),patch.object(sqlite3,'connect') as sql,self.assertRaises(RuntimeError):
                    snapshots.project_timeout(raw,working,stage='associated')
                sql.assert_not_called()

    def test_body_and_duplicate_inbox_JSON_and_wrong_revision_refused(self):
        for mode in ('body','duplicate','wrong-original'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=original_fixture(root,'reconciled')
                if mode=='body':(raw/'vault'/snapshots.NOTE).write_bytes(b'changed');refresh(raw)
                else:
                    r={k:v for k,v in receipt().items() if k not in ('fingerprint','device_id')}
                    if mode=='wrong-original':r['operation_id']=SAVE
                    encoded=json.dumps(r)
                    if mode=='duplicate':encoded=encoded[:-1]+',"sequence":1}'
                    mutate(raw,'UPDATE sync_inbox SET revision=?',(encoded,))
                with self.assertRaises(RuntimeError):snapshots.project_timeout(raw,root/'working',stage='reconciled')

    def test_read_pairs_preserve_failed_attempt_counts_all_ids_and_owned_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','timed-out');b=projection(root/'b','timed-out-read')
            snapshots.preserve_read(a,b)
            snapshots.preserve_read(projection(root/'c','reconciled'),projection(root/'d','cold'))
            for mode in ('outcome','count','issuer','upload','archive','identity','stage'):
                bad=deepcopy(b)
                if mode=='outcome':bad['native']['attempts'][0]['outcome']='interrupted'
                if mode=='count':bad['native']['attempts'][0]['attempts']=2
                if mode=='issuer':bad['native']['issuers'][0]['device_id']='f'*32
                if mode=='upload':bad['original_job_commit'][0]['upload_id']='replacement'
                if mode=='archive':bad['native']['outbox'][0]['state']='pending'
                if mode=='identity':bad['owned_snapshot_identity']=dict(IDENTITY,boot_id=HOLD)
                if mode=='stage':bad['timeout_stage']='cold'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):snapshots.preserve_read(a,bad)

    def test_existing_Stop_and_process_contracts_reject_new_failed_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw,_=original_fixture(root,'timed-out')
            for project,stage,name in ((old_stop.project_stop,'stopped','stop'),(old_process.project_process,'killed','process')):
                with self.subTest(name=name),self.assertRaises(RuntimeError):project(raw,root/name,stage=stage)


class TimeoutValidation(unittest.TestCase):
    def test_original_provider_commit_then_read_ACK_without_relabel_or_second_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);associated=projection(root/'a','associated');failed=projection(root/'b','timed-out')
            read=projection(root/'c','timed-out-read');after=projection(root/'d','reconciled');cold=projection(root/'e','cold')
            p0,p1,hold,held,release,released=providers()
            result=validation.validate_timed_out(associated,failed,p0,p1,DEVICE,hold,held,release,released)
            self.assertEqual(result,receipt());validation.validate_read(failed,read,p1,p1)
            p2=deepcopy(p1);p2['delivery']['counts']['changes:200']+=1
            validation.validate_reconciled(read,after,p1,p2,result);validation.validate_read(after,cold,p2,p2)
            self.assertEqual(read['native']['attempts'],after['native']['attempts'])

    def test_provider_extra_revision_drop_or_forged_counter_and_premature_apply_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','associated');b=projection(root/'b','timed-out')
            p0,p1,hold,held,release,released=providers()
            for mode in ('extra-event','counter','503','object','issuer','head','attempt','old-Save-control'):
                native=deepcopy(b);p=deepcopy(p1);h=deepcopy(hold)
                if mode=='extra-event':p['delivery']['events'].append(dict(action='revision',service_status=409,delivery_status=409,fault_operation=None))
                if mode=='counter':p['delivery']['counts']['revision:200']=2
                if mode=='503':p['delivery']['events'][0]['delivery_status']=503
                if mode=='object':p['rows']['objects'].append(dict(hash='f'*64,size=1))
                if mode=='issuer':native['native']['issuers'][0]['device_id']='f'*32
                if mode=='head':native['native']['heads']=[dict(file_id=FILE,revision=1,path=snapshots.NOTE,hash=snapshots.HASH)]
                if mode=='attempt':native['native']['attempts'][0]['error']='OWNED_AUTH_RESPONSE_DROPPED'
                if mode=='old-Save-control':h['operation_id']=SAVE
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_timed_out(a,native,p0,p,DEVICE,h,held,release,released)

    def test_read_and_reconcile_reject_second_write_new_ID_or_relabelled_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=projection(root/'a','timed-out-read');b=projection(root/'b','reconciled')
            _,p1,*_=providers();p2=deepcopy(p1);p2['delivery']['counts']['changes:200']+=1
            for mode in ('no-read','second-write','upload','attempt','archive','handle'):
                native=deepcopy(b);p=deepcopy(p2)
                if mode=='no-read':p=p1
                if mode=='second-write':p['delivery']['counts']['revision:200']=2
                if mode=='upload':native['native']['outbox'][1]['operation_id']=RELEASE
                if mode=='attempt':native['native']['attempts'][0]['outcome']='succeeded'
                if mode=='archive':native['native']['outbox'][0]['state']='pending'
                if mode=='handle':native['original_job_commit'][0]['upload_id']='new'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_reconciled(a,native,p1,p,receipt())
            with self.assertRaises(RuntimeError):validation.validate_read(projection(root/'f','timed-out'),a,p1,p2)

    def test_actual_host_recorder_clock_and_control_journals_correlate_without_native_cause_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            args=host_records(folder);result=validation.validate_host(*args)
            self.assertTrue(result['original_clock_and_metadata_correlated'])
            self.assertFalse(result['native_timeout_cause_independently_observed']);self.assertFalse(result['native_apply_ACK'])
            self.assertFalse(result['all_live_deadlines_independently_recomputed'])

    def test_host_bad_clock_owner_ID_command_unknown_signal_or_native_claim_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            phase,records,stdout,hold,release=host_records(folder)
            for mode in ('early-host','early-device','reset','late-ui','stale-input','unknown-tap','owner','CAS','signal','readback','phase','native','private-stream'):
                p,r,raw=deepcopy(phase),deepcopy(records),stdout
                if mode=='early-host':r['failure-clock']['sample_start_monotonic_ms']=r['original-window']['preinput_monotonic_ms']+29999
                if mode=='early-device':r['failed-ready']['elapsed_ms']=r['original-window']['preinput_elapsed_ms']+29999
                if mode=='reset':r['original-window']['clock_reset_on_marker']=True
                if mode=='late-ui':r['failed-ready']['elapsed_ms']=r['ready']['elapsed_ms']+36001
                if mode=='stale-input':r['input-owner-at-command']['ready_age_ms']=2001
                if mode=='unknown-tap':r['host-touch-result']['unknown_result']=True
                if mode=='owner':r['input-owner-at-command']['original_lifetime']['start_ticks']+=1
                if mode=='CAS':r['release-control-attempt']['operation_id']=HOLD
                if mode=='signal':r['released-intent']['arguments'][2]+=' extra'
                if mode=='readback':r['released-readback']['bytes']=6
                if mode=='phase':p['phase']='cycle-stop-original'
                if mode=='native':p['native_timeout_verified']=True
                if mode=='private-stream':raw+=b'private-unapproved-stream\n'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):validation.validate_host(p,r,raw,hold,release)


if __name__ == '__main__':unittest.main()
