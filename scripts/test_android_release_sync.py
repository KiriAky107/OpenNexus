"""Owned local SQLite and protocol projections only; no device/network starts."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_release_sync as runtime
import android_release_sync_snapshot as snapshots
import test_android_sync_peer as original_peer

ID = '11111111-2222-4333-8444-555555555555'
VAULT = 'a'*32


class ReleaseSyncChecks(unittest.TestCase):
    def fixture(self, root, *, bound=True, incoming=False):
        raw = root/'raw'; raw.mkdir()
        bodies = {runtime.LOCAL[0]: runtime.BODY.encode(), runtime.LOCAL[1]: b'{"nodes":[],"edges":[]}'}
        if incoming:bodies.update({runtime.PEER[0]:runtime.PEER_NOTE,runtime.PEER[1]:runtime.PEER_CANVAS,runtime.ASSET:runtime.IMAGE_BYTES})
        for path, body in bodies.items():
            target=raw/'vault'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
        with closing(sqlite3.connect(raw/snapshots.DATABASES[0])) as db:
            db.executescript('CREATE TABLE drafts(id,file_id,path,base_hash,generation,saved_generation,content);'
                'CREATE TABLE save_intents(operation_id,draft_id,generation,file_id,path,expected,content,state,error);'
                'CREATE TABLE forbidden_credentials(secret);INSERT INTO forbidden_credentials VALUES("never-read-this");')
        (raw/snapshots.DATABASES[1]).parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
            db.executescript('CREATE TABLE files(id,path,hash,revision,deleted);CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state);'
                'CREATE TABLE sync_bindings(id,remote_vault,state,cursor,endpoint,account);'
                'CREATE TABLE mobile_sync_binding_intents(operation_id,request_hash,request,state,attempt_revision,binding);'
                'CREATE TABLE forbidden_credentials(secret);INSERT INTO forbidden_credentials VALUES("never-read-this");')
            for _,(table,columns) in snapshots.TABLES.items():db.execute('CREATE TABLE '+table+'(binding,'+columns+')')
            for index,(path,body) in enumerate(bodies.items()):
                db.execute('INSERT INTO files VALUES(?,?,?,?,?)',('file-'+str(index),path,hashlib.sha256(body).hexdigest(),1,0))
            if bound:
                endpoint='https://owned.trycloudflare.com/';db.execute('INSERT INTO sync_bindings VALUES(?,?,?,?,?,?)',(ID,VAULT,'active',0,endpoint,'owned-a'))
                request=dict(operation_id=ID,local_vault=ID,remote_vault=VAULT,preview_fingerprint='b'*64,
                    selection=dict(operation_id=ID,endpoint=endpoint,account='owned-a',revision=1,generation=1))
                receipt=dict(id=ID,endpoint=endpoint,remote_vault=VAULT,account='owned-a',cursor=0)
                db.execute('INSERT INTO mobile_sync_binding_intents VALUES(?,?,?,?,?,?)',(ID,'c'*64,json.dumps(request),'bound',1,json.dumps(receipt)))
            db.commit()
        names={'vault/'+p for p in bodies}|{db+s for db in snapshots.DATABASES for s in ('','-wal','-shm')}
        metadata={}
        for name in names:
            f=raw/name;metadata[name]=dict(bytes=f.stat().st_size,sha256=snapshots.sha(f)) if f.exists() else None
        manifest=dict(package=runtime.APP,app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False,files=metadata)
        (raw/'snapshot.json').write_text(json.dumps(manifest),'utf-8')
        return raw

    def test_actual_SQLite_projection_has_closed_columns_and_keeps_raw_originals(self):
        for bound,incoming in ((False,False),(True,False),(True,True)):
            with self.subTest(bound=bound,incoming=incoming),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=self.fixture(root,bound=bound,incoming=incoming);before={p.relative_to(raw).as_posix():snapshots.sha(p) for p in raw.rglob('*') if p.is_file()}
                value=snapshots.project_sync(raw,root/'working',incoming=incoming)
                self.assertEqual(value['native']['binding'] is not None,bound)
                self.assertNotIn('never-read-this',json.dumps(value));self.assertFalse(value['credentials_selected'])
                self.assertEqual(before,{p.relative_to(raw).as_posix():snapshots.sha(p) for p in raw.rglob('*') if p.is_file()})
                self.assertFalse(value['native_RAM_invocation_id_observed'])

    def test_projection_rejects_missing_issuer_for_attempts_secret_request_keys_and_row_overflow(self):
        for mode in ('no-attempt-yet','missing-issuer','extra-request-field','too-many-heads'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=self.fixture(root);path=raw/snapshots.DATABASES[1]
                with closing(sqlite3.connect(path)) as db:
                    if mode in ('no-attempt-yet','missing-issuer'):db.execute('DROP TABLE mobile_sync_attempt_devices')
                    if mode=='missing-issuer':db.execute('INSERT INTO sync_attempts VALUES(?,?,?,?,?)',(ID,ID,1,'succeeded',None))
                    if mode=='extra-request-field':
                        request=json.loads(db.execute('SELECT request FROM mobile_sync_binding_intents').fetchone()[0]);request['password']='never-export'
                        db.execute('UPDATE mobile_sync_binding_intents SET request=?',(json.dumps(request),))
                    if mode=='too-many-heads':
                        db.executemany('INSERT INTO sync_heads VALUES(?,?,?,?,?)',[(ID,'file-'+str(i),i,runtime.LOCAL[0],'b'*64) for i in range(65)])
                    db.commit()
                m=json.loads((raw/'snapshot.json').read_bytes());m['files'][snapshots.DATABASES[1]]=dict(bytes=path.stat().st_size,sha256=snapshots.sha(path));(raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
                if mode=='no-attempt-yet':self.assertEqual(snapshots.project_sync(raw,root/'working')['native']['issuers'],[])
                else:
                    with self.assertRaises(RuntimeError):snapshots.project_sync(raw,root/'working')

    def test_tampered_extra_private_or_nested_original_snapshot_refused(self):
        for mode in ('extra-auth-path','changed-original','nested-working','not-stopped','copied-credential','incoming-file-missing'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=self.fixture(root);m=json.loads((raw/'snapshot.json').read_bytes());working=root/'working'
                if mode=='extra-auth-path':m['files']['mobile-sync-auth.sqlite3']=None
                if mode=='changed-original':(raw/'vault'/runtime.LOCAL[0]).write_bytes(b'changed')
                if mode=='nested-working':working=raw/'working'
                if mode=='not-stopped':m['app_stopped_before_and_after']=False
                if mode=='copied-credential':m['credential_store_copied']=True
                (raw/'snapshot.json').write_text(json.dumps(m),'utf-8')
                with self.assertRaises(RuntimeError):snapshots.project_sync(raw,working,incoming=mode=='incoming-file-missing')

    def test_cold_logical_comparison_allows_only_SQLite_layout_change(self):
        before=dict(files=[dict(path=runtime.LOCAL[0])],native=dict(jobs=[]),raw_sha256={'vault/'+runtime.LOCAL[0]:'a'*64,snapshots.DATABASES[1]:'b'*64,snapshots.DATABASES[1]+'-wal':'c'*64})
        after=deepcopy(before);after['raw_sha256'][snapshots.DATABASES[1]]='d'*64;after['raw_sha256'][snapshots.DATABASES[1]+'-wal']='e'*64
        runtime.same_projection(before,after)
        after['raw_sha256']['vault/'+runtime.LOCAL[0]]='f'*64
        with self.assertRaises(RuntimeError):runtime.same_projection(before,after)
        after=deepcopy(before);after['native']['jobs']=[dict(operation_id='other')]
        with self.assertRaises(RuntimeError):runtime.same_projection(before,after)

    def test_actual_normal_and_sync_private_stdin_routes_have_separate_closed_inputs(self):
        subject=object.__new__(runtime.ReleaseSync);subject.args=SimpleNamespace(serial='emulator-5562');subject.shell=Mock();subject.device=Mock()
        auth=dict(endpoint='https://owned.trycloudflare.com',accounts=[dict(username='android-owned-'+k+'-'+'b'*12,password='A'*48,device_name='Owned Android '+k) for k in ('a','b')])
        vault=dict(operation_id=ID,vault_id=VAULT,name='Owned Android Sync '+VAULT,delivery='original_response_received',server_operation_id_available=False)
        with patch('android_auth_runtime.require_hosted_emulator'),patch.object(runtime,'require_hosted_emulator'):
            subject.stage_owned_input(auth);subject.stage_sync_input(vault)
        self.assertEqual(subject.device.call_count,2)
        first,second=subject.device.call_args_list
        self.assertEqual(json.loads(first.kwargs['input']),auth)
        self.assertEqual(json.loads(second.kwargs['input']),dict(vault_id=VAULT,vault_name='Owned Android Sync '+VAULT))
        self.assertIn('test ! -e no_backup/owned-auth.json',first.args[2]);self.assertIn('test ! -e no_backup/owned-sync.json',second.args[2])
        for call in subject.device.call_args_list:self.assertNotIn('A'*48,str(call.args))
        subject.device.reset_mock()
        for value in ({**vault,'password':'forbidden'},{**vault,'vault_id':'../foreign'},{**vault,'server_operation_id_available':True}):
            with patch.object(runtime,'require_hosted_emulator'),self.assertRaises(RuntimeError):subject.stage_sync_input(value)
        subject.device.assert_not_called()
        subject.device.side_effect=TimeoutError()
        with patch.object(runtime,'require_hosted_emulator'),self.assertRaises(TimeoutError):subject.stage_sync_input(vault)
        subject.device.assert_called_once()

    def upload(self):
        native=dict(binding=dict(id=ID,remote_vault=VAULT),jobs=[],attempts=[],issuers=[],heads=[],conflicts=[],outbox=[],inbox=[],association=dict(operation_id=ID))
        remote=[];hashes={}
        for i,path in enumerate(runtime.LOCAL):
            digest=hashlib.sha256(('original-'+path).encode()).hexdigest();operation=str(i+1)+'1'*7+'-2222-4333-8444-555555555555'
            value=dict(operation_id=operation,file_id='file-'+str(i),path=path,hash=digest,size=12,sequence=i+1,device_id='android',operation='put')
            remote.append(value);native['jobs'].append({k:value[k] for k in ('operation_id','file_id','path','hash','size') }|dict(remote_revision=i+1,state='acked',error=None))
            native['attempts'].append(dict(operation_id=operation,attempts=1,outcome='succeeded',error=None));native['issuers'].append(dict(operation_id=operation,device_id='android'))
            native['outbox'].append(dict(operation_id=operation,file_id=value['file_id'],path=path,hash=digest,state='acked'))
            native['heads'].append(dict(file_id=value['file_id'],revision=i+1,path=path,hash=digest));hashes['vault/'+path]=digest
        return dict(vault=dict(id=VAULT,sequence=2),rows=dict(revisions=remote)),dict(native=native,raw_sha256=hashes)

    def test_upload_provider_count_cannot_replace_original_ID_device_attempt_or_ack(self):
        provider,projection=self.upload();self.assertEqual(len(runtime.validate_upload(provider,projection,'android')),2)
        for mode in ('remote-ID','issuer','attempt','native-ack','outbox-ack','file-bytes'):
            p,n=deepcopy(provider),deepcopy(projection)
            if mode=='remote-ID':p['rows']['revisions'][0]['operation_id']='other'
            if mode=='issuer':n['native']['issuers'][0]['device_id']='setup'
            if mode=='attempt':n['native']['attempts'][0]['attempts']=2
            if mode=='native-ack':n['native']['jobs'][0]['state']='committing'
            if mode=='outbox-ack':n['native']['outbox'][0]['state']='pending'
            if mode=='file-bytes':n['raw_sha256']['vault/'+runtime.LOCAL[0]]='f'*64
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_upload(p,n,'android')

    def test_incoming_needs_actual_applied_inbox_and_original_heads_not_just_offered_sequence(self):
        provider,projection=self.upload();before=projection['native'];after=deepcopy(before);after['binding']['cursor']=5
        receipts=[]
        for i,path in enumerate(runtime.PEER+(runtime.ASSET,)):
            receipt=dict(operation_id='peer-'+str(i),file_id='peer-file-'+str(i),base_revision=0,path=path,operation='put',hash='c'*64,size=16,sequence=i+3,vault_id=VAULT,device_id='peer',fingerprint='b'*64)
            receipts.append(receipt);provider['rows']['revisions'].append(receipt)
            after['inbox'].append(dict(sequence=i+3,state='applied',revision=json.dumps({k:v for k,v in receipt.items() if k not in ('device_id','fingerprint')})))
            after['heads'].append(dict(file_id=receipt['file_id'],revision=i+3,path=path,hash=receipt['hash']))
        provider['vault']['sequence']=5;provider['rows']['device_progress']=[dict(device_id='android',offered_sequence=5)]
        runtime.validate_incoming(receipts,provider,before,after,'android')
        for mode in ('not-applied','wrong-ID','changed-prior','missing-head'):
            changed=deepcopy(after)
            if mode=='not-applied':changed['inbox'][0]['state']='pending'
            if mode=='wrong-ID':original=json.loads(changed['inbox'][0]['revision']);original['operation_id']='other';changed['inbox'][0]['revision']=json.dumps(original)
            if mode=='changed-prior':changed['attempts'][0]['attempts']=2
            if mode=='missing-head':changed['heads'].pop()
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_incoming(receipts,provider,before,changed,'android')

    def test_frozen_association_cannot_switch_original_auth_or_setup_identity(self):
        prepared=dict(vault=dict(vault_id=VAULT),device_id='setup')
        original=dict(operation_id=ID,remote_vault=VAULT,preview_fingerprint='b'*64)
        login=dict(operation_id=ID,endpoint='https://owned.trycloudflare.com/',account='owned-a',device_id='android')
        native=dict(binding=dict(id=ID,remote_vault=VAULT,endpoint=login['endpoint'],account=login['account']),association=dict(original,auth_operation_id=ID))
        runtime.validate_association(original,native,prepared,login)
        for mode in ('auth','digest','device'):
            changed=deepcopy(native);l=deepcopy(login)
            if mode=='auth':changed['association']['auth_operation_id']='other'
            if mode=='digest':changed['association']['preview_fingerprint']='c'*64
            if mode=='device':l['device_id']='setup'
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_association(original,changed,prepared,l)

    def test_release_peer_closed_paths_and_original_unknown_result_no_new_POST(self):
        with tempfile.TemporaryDirectory() as folder:
            base,calls,remote=original_peer.OriginalPeerChecks().peer(Path(folder),lose_commit=True)
            base.__class__=runtime.ReleasePeer
            with self.assertRaises(TimeoutError):base.publish('release-note',runtime.PEER[0],b'original')
            original_count=sum(m=='POST' and p.endswith('/revisions') for m,p,_ in calls)
            base.publish('release-note',runtime.PEER[0],b'original')
            self.assertEqual(sum(m=='POST' and p.endswith('/revisions') for m,p,_ in calls),original_count)
            with self.assertRaises(RuntimeError):base.publish('foreign','peer-note.md',b'foreign')
        self.assertNotIn(runtime.PEER[0],runtime.Peer.paths)

    def test_unknown_seed_GUI_stops_before_login_setup_or_further_writes(self):
        subject=object.__new__(runtime.ReleaseSync);subject.prepare=Mock();subject.enable_test_ime=Mock();subject.restore_test_ime=Mock();subject.phase=Mock()
        with patch.object(runtime.ReleaseUpgrade,'phase',side_effect=TimeoutError('original GUI unknown')) as seed,patch.object(runtime,'Setup') as setup, self.assertRaises(TimeoutError):subject.run()
        seed.assert_called_once_with(subject,'markdown','seed-markdown');subject.restore_test_ime.assert_called_once();subject.phase.assert_not_called();setup.assert_not_called()


if __name__=='__main__':unittest.main()
