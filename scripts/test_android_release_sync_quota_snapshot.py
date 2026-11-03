"""New synthetic files/SQLite copies only; no real retained run or device."""
from contextlib import contextmanager, closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import android_release_sync_quota_snapshot as snapshots
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_validation import fixture as metadata_fixture


@contextmanager
def task():
    with tempfile.TemporaryDirectory(prefix='opennexus-quota-projection-', dir=os.getenv('OPENNEXUS_QUOTA_TEST_ROOT')) as folder:
        root = Path(folder)
        assert root.is_absolute() and root == root.resolve() and root.name.startswith('opennexus-quota-projection-')
        yield root


def retained(raw):
    return {str(p.relative_to(raw)): hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()}


def refresh(raw):
    manifest = json.loads((raw/'snapshot.json').read_bytes())
    for name in snapshots.FILES:
        path = raw/name
        manifest['files'][name] = dict(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()) if path.exists() else None
    (raw/'snapshot.json').write_text(json.dumps(manifest), encoding='utf-8')


def original_fixture(root, stage='quota-failed', wal=False):
    data = metadata_fixture(); value = deepcopy(data['before' if stage == 'associated' else 'after']); scope = data['scope']
    selected = dict(endpoint='https://sync.fixture.invalid', account='release-owned-a',
                    local_vault='00000000-0000-0000-0000-000000000014', revision=1, generation=2,
                    preview_fingerprint=value['association']['preview_fingerprint'])
    request = dict(operation_id=scope['association_operation_id'], local_vault=selected['local_vault'],
        remote_vault=scope['remote_vault'], selection=dict(revision=1, endpoint=selected['endpoint'], account=selected['account'],
        operation_id=scope['login_operation_id'], generation=2), preview_fingerprint=selected['preview_fingerprint'])
    binding = dict(id=scope['binding_id'], endpoint=selected['endpoint'], remote_vault=scope['remote_vault'], account=selected['account'], cursor=0)
    request_raw, binding_raw = json.dumps(request), json.dumps(binding)
    value['association']['original_request_bytes_sha256'] = hashlib.sha256(request_raw.encode()).hexdigest()
    value['association']['original_binding_bytes_sha256'] = hashlib.sha256(binding_raw.encode()).hexdigest()
    raw = root/'original'; (raw/'vault/.ainote').mkdir(parents=True)
    (raw/snapshots.NOTE).write_bytes(quota.BODY); (raw/snapshots.SPOOL).write_bytes(quota.BODY)
    writers = []
    for index, dbname in enumerate(snapshots.DATABASES):
        db = sqlite3.connect(raw/dbname)
        if wal:
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0')
        tables = snapshots.DRAFT_COLUMNS if index == 0 else snapshots.HOST_COLUMNS
        for table, columns in tables.items():
            if table == 'sync_jobs': columns += ',binding,operation,base_revision,upload_id'
            elif table in ('sync_attempts', 'mobile_sync_attempt_devices'): columns += ',binding'
            numbers = {'generation','saved_generation','revision','deleted','cursor','size','remote_revision',
                       'attempts','attempt_revision','base_revision','sequence'}
            db.execute('CREATE TABLE '+table+' ('+','.join(c+(' INTEGER' if c in numbers else ' TEXT') for c in columns.split(','))+')')
        if index == 0:
            inserts = {'drafts':[value['draft']], 'save_intents':[value['save']]}
        else:
            inserts = {'files':[value['file']], 'outbox':value['outbox'],
                'sync_bindings':[dict(value['binding'], endpoint=selected['endpoint'], account=selected['account'])],
                'mobile_sync_binding_intents':[dict(operation_id=scope['association_operation_id'], request_hash=value['association']['request_hash'],
                    request=request_raw, state='bound', attempt_revision=1, binding=binding_raw)],
                'sync_jobs':[dict(row, **{k:value['commits'][i][k] for k in ('binding','operation','base_revision','upload_id')}) for i,row in enumerate(value['jobs'])],
                'sync_attempts':[dict(row,binding=scope['binding_id']) for row in value['attempts']],
                'mobile_sync_attempt_devices':[dict(row,binding=scope['binding_id']) for row in value['issuers']]}
            db.execute('CREATE TABLE credentials (secret TEXT)'); db.execute("INSERT INTO credentials VALUES ('synthetic-private-never-select')")
        for table, rows in inserts.items():
            for row in rows:
                db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))
        db.commit()
        if wal: writers.append(db)
        else: db.close()
    identity = dict(avd=scope['avd'], qemu='1', api='36', build_type='userdebug', debuggable='1', boot_id=scope['boot_id'])
    manifest = dict(schema=1, package=snapshots.APP, identity=identity, quota_scope=scope, quota_stage=stage,
        spool_source=snapshots.SPOOL_SOURCE, files={}, app_stopped_before_and_after=True,
        credential_store_copied=False, SQL_executed_on_device=False, normal_phone_permissions_verified=False)
    (raw/'snapshot.json').write_text(json.dumps(manifest), encoding='utf-8'); refresh(raw)
    return raw,scope,selected,value,writers


def mutate(root, table, sql, args=(), *, draft=False):
    with closing(sqlite3.connect(root/snapshots.DATABASES[0 if draft else 1])) as db:
        db.execute(sql, args); db.commit()
    refresh(root)


class QuotaSnapshot(unittest.TestCase):
    def project(self, root, raw, scope, selected, stage='quota-failed'):
        return snapshots.project_quota(raw, root/'working', scope=scope, selected=selected, stage=stage)

    def test_exact_associated_and_quota_failed_real_SQL_copies_preserve_originals(self):
        for stage in ('associated','quota-failed'):
            with self.subTest(stage=stage), task() as root:
                raw,scope,selected,expected,_ = original_fixture(root,stage)
                original = retained(raw)
                actual = self.project(root,raw,scope,selected,stage)
                self.assertEqual(actual.projection,expected)
                self.assertEqual(actual.note,quota.BODY); self.assertEqual(actual.spool,quota.BODY)
                self.assertEqual(retained(raw),original)
                self.assertFalse(actual.evidence['credentials_selected'])
                self.assertFalse(actual.evidence['completed_driver_and_stop_gate_verified'])
                self.assertFalse(actual.evidence['runtime_verified'])
                self.assertNotIn('synthetic-private',json.dumps(actual.projection))
                self.assertNotIn(selected['endpoint'],json.dumps(actual.projection))

    def test_real_WAL_SHM_inputs_remain_unchanged_and_are_read_only_after_copy(self):
        with task() as root:
            raw,scope,selected,expected,writers = original_fixture(root,wal=True)
            try:
                original = retained(raw)
                self.assertTrue(all((raw/(name+'-wal')).exists() and (raw/(name+'-shm')).exists() for name in snapshots.DATABASES))
                actual = self.project(root,raw,scope,selected)
                self.assertEqual(actual.projection,expected)
                self.assertEqual(retained(raw),original)
                self.assertEqual(len(actual.evidence['original_files']),8)
            finally:
                for db in writers: db.close()

    def test_SQL_handles_only_new_copy_paths_and_authorizer_denies_credentials_and_writes(self):
        with task() as root:
            raw,scope,selected,_,_ = original_fixture(root)
            connect = sqlite3.connect; paths = []
            def tracked(path, *args, **kwargs):
                self.assertTrue(type(path) is str and path.endswith('?mode=ro'))
                self.assertTrue(path.startswith((root/'working').as_uri()+'/'))
                self.assertNotIn(raw.as_uri(),path); paths.append(path)
                return connect(path,*args,**kwargs)
            with patch.object(snapshots.sqlite3,'connect',side_effect=tracked):
                self.project(root,raw,scope,selected)
            self.assertEqual(len(paths),2)
            with closing(snapshots.open_copy(root/'working'/snapshots.DATABASES[1],snapshots.HOST_COLUMNS)) as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone(),(1,))
                for sql in ('SELECT secret FROM credentials','DELETE FROM outbox',"ATTACH DATABASE ':memory:' AS foreign_db"):
                    with self.assertRaises(sqlite3.DatabaseError): db.execute(sql)

    def test_alias_overlap_existing_output_and_linked_original_reject_without_overwrite(self):
        with task() as root:
            raw,scope,selected,_,_ = original_fixture(root)
            (root/'working').mkdir(); (root/'working/keep').write_bytes(b'untouched')
            with self.assertRaises(RuntimeError): self.project(root,raw,scope,selected)
            self.assertEqual((root/'working/keep').read_bytes(),b'untouched')
            for source, output in ((raw,raw/'working'),(raw/'..'/'original',root/'fresh')):
                with self.assertRaises(RuntimeError): snapshots.project_quota(source,output,scope=scope,selected=selected,stage='quota-failed')
        with task() as root:
            raw,scope,selected,_,_ = original_fixture(root)
            os.link(raw/snapshots.NOTE,root/'same-content-hardlink.md')
            with self.assertRaises(RuntimeError): self.project(root,raw,scope,selected)
            self.assertFalse((root/'working').exists())

    def test_manifest_types_scope_duplicates_partial_and_spool_mapping_are_closed(self):
        changes = [('identity','boot_id','00000000-0000-0000-0000-ffffffffffff'),('identity','api',36),
                   ('quota_scope','uid',True),('files',snapshots.SPOOL,None)]
        for block,key,change in changes:
            with self.subTest(block=block,key=key),task() as root:
                raw,scope,selected,_,_ = original_fixture(root)
                manifest=json.loads((raw/'snapshot.json').read_bytes());manifest[block][key]=change
                (raw/'snapshot.json').write_text(json.dumps(manifest),encoding='utf-8')
                with self.assertRaises(RuntimeError): self.project(root,raw,scope,selected)
        for kind in ('duplicate','partial','extra','spool','credential','size','foreign-file'):
            with self.subTest(kind=kind),task() as root:
                raw,scope,selected,_,_ = original_fixture(root)
                manifest=json.loads((raw/'snapshot.json').read_bytes())
                if kind=='extra': manifest['private_UI']='synthetic-private'
                if kind=='spool': manifest['spool_source']='vault/.ainote/sync-spool/unknown'
                if kind=='credential': manifest['credential_store_copied']=True
                if kind=='size': manifest['files'][snapshots.DATABASES[0]]['bytes']=True
                if kind=='foreign-file': (raw/'auth.sqlite3').write_bytes(b'synthetic-private')
                encoded=json.dumps(manifest)
                if kind=='duplicate': encoded=encoded.replace('"schema": 1','"schema": 1, "schema": 1',1)
                if kind=='partial': encoded=encoded[:-1]
                (raw/'snapshot.json').write_text(encoded,encoding='utf-8')
                with self.assertRaises(RuntimeError): self.project(root,raw,scope,selected)
                self.assertFalse((root/'working').exists())

    def test_hidden_rows_foreign_bindings_and_views_never_become_partial_success(self):
        for kind in ('draft','save','job','binding','view'):
            with self.subTest(kind=kind),task() as root:
                raw,scope,selected,_,_ = original_fixture(root)
                if kind in ('draft','save'):
                    table='drafts' if kind=='draft' else 'save_intents'
                    mutate(raw,table,'INSERT INTO '+table+' SELECT * FROM '+table,draft=True)
                elif kind=='job': mutate(raw,'sync_jobs','INSERT INTO sync_jobs SELECT * FROM sync_jobs')
                elif kind=='binding': mutate(raw,'sync_jobs',"UPDATE sync_jobs SET binding='foreign'")
                else:
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                        db.execute('DROP TABLE sync_jobs'); db.execute('CREATE VIEW sync_jobs AS SELECT secret AS operation_id FROM credentials');db.commit()
                    refresh(raw)
                original=retained(raw)
                with self.assertRaises(RuntimeError):self.project(root,raw,scope,selected)
                self.assertEqual(original,retained(raw))

    def test_closed_bound_association_uses_no_unknown_JSON_hash_or_private_export(self):
        for kind in ('duplicate','private','wrong-login','wrong-selection','oversize'):
            with self.subTest(kind=kind),task() as root:
                raw,scope,selected,_,_ = original_fixture(root)
                with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                    text=db.execute('SELECT request FROM mobile_sync_binding_intents').fetchone()[0]
                    if kind=='duplicate':text=text.replace('"operation_id":','"operation_id":"duplicate", "operation_id":',1)
                    if kind=='private':text=text[:-1]+',"password":"synthetic-private"}'
                    if kind=='wrong-login':text=text.replace(scope['login_operation_id'],scope['setup_operation_id'])
                    if kind=='wrong-selection':selected['generation']=3
                    if kind=='oversize':text=' '*8193
                    db.execute('UPDATE mobile_sync_binding_intents SET request=?',(text,));db.commit()
                refresh(raw)
                with self.assertRaisesRegex(RuntimeError,'^RELEASE_QUOTA_PROJECTION_UNAVAILABLE$'):self.project(root,raw,scope,selected)

    def test_pending_null_commit_single_failed_attempt_and_exact_note_spool_are_required(self):
        cases=[('sync_jobs',"UPDATE sync_jobs SET base_revision=0"),('sync_jobs',"UPDATE sync_jobs SET upload_id='new'"),
               ('sync_jobs',"UPDATE sync_jobs SET state='committing'"),('sync_attempts',"UPDATE sync_attempts SET attempts=2"),
               ('sync_attempts',"UPDATE sync_attempts SET error='SYNC_NETWORK_ERROR'"),
               ('outbox',"UPDATE outbox SET state='acked' WHERE state='archived'")]
        for table,sql in cases:
            with self.subTest(sql=sql),task() as root:
                raw,scope,selected,_,_=original_fixture(root);mutate(raw,table,sql)
                with self.assertRaises(RuntimeError):self.project(root,raw,scope,selected)
        for name in (snapshots.NOTE,snapshots.SPOOL):
            with task() as root:
                raw,scope,selected,_,_=original_fixture(root)
                (raw/name).write_bytes(quota.BODY+b' ');refresh(raw)
                with self.assertRaises(RuntimeError):self.project(root,raw,scope,selected)
                self.assertFalse((root/'working').exists())

    def test_final_original_mutation_is_rejected_after_copy_SQL_reads(self):
        with task() as root:
            raw,scope,selected,_,_=original_fixture(root)
            original_rows=snapshots.rows; changed=[]
            def reading(db,table,columns,count):
                result=original_rows(db,table,columns,count)
                if table=='sync_jobs' and columns.startswith('binding,'):
                    (raw/snapshots.NOTE).write_bytes(b'changed during SQL');changed.append(True)
                return result
            with patch.object(snapshots,'rows',side_effect=reading),self.assertRaises(RuntimeError):
                self.project(root,raw,scope,selected)
            self.assertEqual(changed,[True])

    def test_partial_copy_failure_preserves_original_and_existing_partial_output_is_not_retried(self):
        with task() as root:
            raw,scope,selected,_,_=original_fixture(root);original=retained(raw);opening=Path.open; attempts=[]
            class ShortWriter:
                def __init__(self,stream):self.stream=stream
                def __enter__(self):self.stream.__enter__();return self
                def __exit__(self,*args):return self.stream.__exit__(*args)
                def write(self,data):attempts.append(True);return self.stream.write(data[:3])
            def partial(path,mode='r',*args,**kwargs):
                stream=opening(path,mode,*args,**kwargs)
                return ShortWriter(stream) if mode=='xb' else stream
            with patch.object(Path,'open',partial),self.assertRaises(RuntimeError):self.project(root,raw,scope,selected)
            self.assertEqual(attempts,[True]);self.assertEqual(retained(raw),original)
            partial_files=retained(root/'working')
            with self.assertRaises(RuntimeError):self.project(root,raw,scope,selected)
            self.assertEqual(retained(root/'working'),partial_files)

    def test_read_boundaries_recheck_new_links_and_refuse_late_manifest_growth(self):
        with task() as root:
            raw,scope,selected,_,_=original_fixture(root)
            foreign=root/'foreign-synthetic.md';foreign.write_bytes(b'never-read-this-content')
            copying=snapshots.copy_originals;opening=Path.open;reads=[]
            def changed(source,working,manifest):
                (raw/snapshots.SPOOL).unlink();os.link(foreign,raw/snapshots.SPOOL)
                return copying(source,working,manifest)
            def reading(path,mode='r',*args,**kwargs):
                if path==raw/snapshots.SPOOL and mode=='rb':reads.append(True)
                return opening(path,mode,*args,**kwargs)
            with patch.object(snapshots,'copy_originals',side_effect=changed),patch.object(Path,'open',reading),self.assertRaises(RuntimeError):
                self.project(root,raw,scope,selected)
            # Initial admission read the original fixed Note spool; after the
            # link swap no copy read is allowed to follow the changed source.
            self.assertEqual(len(reads),2)  # bounded hash + fixed56B admission
        with task() as root:
            raw,scope,selected,_,_=original_fixture(root);copying=snapshots.copy_originals
            def grown(source,working,manifest):
                copying(source,working,manifest)
                with (raw/'snapshot.json').open('ab') as stream:stream.write(b' '*(snapshots.MAX_MANIFEST+1))
            with patch.object(snapshots,'copy_originals',side_effect=grown),self.assertRaises(RuntimeError):
                self.project(root,raw,scope,selected)


if __name__=='__main__':
    unittest.main()
