"""Bounded auth index and ciphertext observations; synthetic local inputs only."""
from contextlib import closing
from copy import deepcopy
import io
import hashlib
import json
from pathlib import Path
import sqlite3
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import Mock,patch

import android_release_auth as runtime
from android_release_auth_observation import APP_ROOT,APP,INDEX,INDEX_FILES,CIPHER_DIRECTORY,StoppedAuthObserver,file_hash,project_metadata


class ReleaseAuthBoundaries(unittest.TestCase):
    def index(self, root):
        snapshot=root/'raw';snapshot.mkdir();dbfile=snapshot/INDEX
        with closing(sqlite3.connect(dbfile)) as db:
            db.executescript('CREATE TABLE auth_intents(operation_id,endpoint,account,device_name,kind,phase,device_id,generation,server_logout_confirmed,previous_operation);CREATE TABLE auth_selection(revision,endpoint,account,operation_id,generation);CREATE TABLE private_credentials(password,access_token);')
            db.execute('INSERT INTO auth_intents VALUES(?,?,?,?,?,?,?,?,?,?)',('original-login','https://owned.trycloudflare.com','owned-user','own','login','active','own-device',1,0,None))
            db.execute('INSERT INTO auth_selection VALUES(?,?,?,?,?)',(1,'https://owned.trycloudflare.com','owned-user','original-login',1))
            db.execute('INSERT INTO private_credentials VALUES(?,?)',('never-export-password','never-export-token'));db.commit()
        manifest=dict(package=APP,app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False,
            files={INDEX:dict(bytes=dbfile.stat().st_size,sha256=file_hash(dbfile)),INDEX+'-wal':None,INDEX+'-shm':None})
        (snapshot/'snapshot.json').write_text(json.dumps(manifest),'utf-8')
        return snapshot

    def test_real_SQLite_queries_only_allowed_columns_from_new_working_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot=self.index(root);before={p.name:file_hash(p) for p in snapshot.iterdir()}
            result=project_metadata(snapshot,root/'working')
            self.assertEqual(result['intents'][0]['operation_id'],'original-login')
            self.assertEqual(result['selection']['operation_id'],'original-login')
            self.assertFalse(result['secret_payload_selected']);self.assertNotIn('never-export',json.dumps(result))
            self.assertEqual(before,{p.name:file_hash(p) for p in snapshot.iterdir()})

    def test_tampered_or_expanded_index_snapshot_cannot_be_opened(self):
        for mode in ('extra-path','changed-original','nested-working','stopped-false'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);snapshot=self.index(root);path=snapshot/'snapshot.json';value=json.loads(path.read_bytes());working=root/'working'
                if mode=='extra-path':value['files']['no_backup/sync-credentials/a.bin']=None
                if mode=='changed-original':(snapshot/INDEX).write_bytes(b'changed')
                if mode=='nested-working':working=snapshot/'working'
                if mode=='stopped-false':value['app_stopped_before_and_after']=False
                path.write_text(json.dumps(value),'utf-8')
                with self.assertRaises(RuntimeError):project_metadata(snapshot,working)

    def cipher_observer(self, *, symlink=False, foreign=False, absent=False):
        observer=object.__new__(StoppedAuthObserver);observer.allowed=set(INDEX_FILES);observer.require_stopped=Mock()
        name=APP_ROOT+CIPHER_DIRECTORY+'/'+'a'*64+'/'+'b'*64+'.bin';calls=[]
        def shell(*args,**kwargs):
            calls.append(args);raw=b'';code=0
            if args[:2]==('test','-e'):code=1 if absent else 0
            elif args[0]=='find':
                raw=(name+'\n').encode() if args[-1]=='f' or symlink else b''
                if foreign and args[-1]=='f':raw=(APP_ROOT+'mobile-sync-auth.sqlite3\n').encode()
            elif args[0]=='realpath':raw=(args[1]+'\n').encode()
            elif args[0]=='stat':raw=b'256\n'
            elif args[0]=='sha256sum':raw=('c'*64+'  '+args[1]+'\n').encode()
            elif args[0]=='cat':raise AssertionError('Ciphertext must never be read')
            return CompletedProcess(args,code,raw,b'')
        observer.shell=shell
        return observer,calls

    def test_ciphertext_only_stat_and_hash_never_cat_or_copy_and_scope_restored(self):
        observer,calls=self.cipher_observer();result=observer.ciphertext_fingerprints()
        self.assertEqual(len(result),1);self.assertEqual(next(iter(result.values())),dict(bytes=256,sha256='c'*64))
        self.assertEqual(observer.allowed,INDEX_FILES);self.assertFalse(any(v[0]=='cat' for v in calls))
        self.assertEqual(observer.require_stopped.call_count,2)

    def test_ciphertext_symlink_or_foreign_path_refused_without_byte_read(self):
        for mode in ('symlink','foreign'):
            with self.subTest(mode=mode):
                observer,calls=self.cipher_observer(**{mode:True})
                with self.assertRaises(RuntimeError):observer.ciphertext_fingerprints()
                self.assertFalse(any(v[0]=='cat' for v in calls));self.assertEqual(observer.allowed,INDEX_FILES)
        observer,calls=self.cipher_observer(absent=True);self.assertEqual(observer.ciphertext_fingerprints(),{})

    def sequence(self):
        provider=[];native=[]
        login=dict(operation_id='original-login',kind='login',phase='active',device_id='device',previous_operation=None,server_logout_confirmed=0)
        refresh=dict(login,operation_id='original-refresh',kind='refresh',previous_operation='original-login')
        logout=dict(login,operation_id='original-logout',kind='logout',phase='disconnected',previous_operation='original-refresh',server_logout_confirmed=1)
        for i,counts in enumerate(((1,0,0),(1,0,0),(1,1,0),(1,1,1))):
            provider.append(dict(users=[dict(devices=[dict(id='device',name='owned',revoked=False)],session_counts=[dict(device_id='device',sessions=0 if i==3 else 1)]),dict(devices=[],session_counts=[])],requests=dict(counts={action+':'+str(status):v for action,status,v in zip(('login','refresh','logout'),(200,200,204),counts) if v})))
            rows=[login] if i<2 else [login,refresh] if i==2 else [login,refresh,logout]
            native.append(dict(intents=deepcopy(rows),selection=dict(operation_id=rows[-1]['operation_id']),integrity='ok',secret_payload_selected=False))
        sealed=[{'owned.bin':dict(sha256='a'*64,bytes=256)},{'owned.bin':dict(sha256='a'*64,bytes=256)},{'owned.bin':dict(sha256='b'*64,bytes=256)},{}]
        return provider,native,sealed

    def test_real_request_counts_original_ID_chain_and_cold_cipher_binding_required(self):
        provider,native,sealed=self.sequence();result=runtime.validate_normal_observations(provider,native,sealed)
        self.assertEqual(result['original_login_refresh_logout_ids'],['original-login','original-refresh','original-logout'])
        for mode in ('extra-login','new-device','cold-native','no-rotation','not-erased','logout-unknown','rewritten-old'):
            p,n,s=deepcopy(provider),deepcopy(native),deepcopy(sealed)
            if mode=='extra-login':p[1]['requests']['counts']['login:200']=2
            if mode=='new-device':p[2]['users'][0]['devices'][0]['id']='new'
            if mode=='cold-native':n[1]['selection']['operation_id']='new'
            if mode=='no-rotation':s[2]=s[0]
            if mode=='not-erased':s[3]=s[2]
            if mode=='logout-unknown':n[3]['intents'][2]['server_logout_confirmed']=0
            if mode=='rewritten-old':n[3]['intents'][0]['phase']='changed'
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_normal_observations(p,n,s)

    def test_unapproved_phase_or_local_run_refused_before_product_or_private_input(self):
        subject=object.__new__(runtime.ReleaseAuth)
        subject.stop=Mock()
        with self.assertRaises(RuntimeError):subject.phase('refresh-unknown','replay')
        subject.stop.assert_not_called()
        with patch.dict('os.environ',GITHUB_ACTIONS='false'),patch('android_release_auth.bind_pair') as bind,tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'evidence'
            with self.assertRaises(RuntimeError):runtime.ReleaseAuth(root,root,root,out)
            bind.assert_not_called();self.assertFalse(out.exists())

    def test_failure_capture_never_observes_screen_AX_or_private_store(self):
        with tempfile.TemporaryDirectory() as folder:
            subject=object.__new__(runtime.ReleaseAuth);subject.output=Path(folder);subject.device=Mock();subject.shell=Mock()
            subject.capture('original-failure');subject.device.assert_not_called();subject.shell.assert_not_called()
            result=json.loads((subject.output/'original-failure-capture-omitted.json').read_bytes())
            self.assertEqual(result,dict(raw_UI_capture=False,credentials_captured=False))

    def test_provider_exception_values_never_reach_output_or_failure_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);probe=Mock();probe.output=root;probe.identity={'owned':True}
            probe.run.side_effect=ValueError('never-export-password-or-DSN')
            probe.failure_observations.side_effect=RuntimeError('never-export-provider-payload')
            with patch('sys.argv',['auth','--inputs',folder,'--sdk',folder,'--java',folder,'--output',folder]),patch('android_release_auth.ReleaseAuth',return_value=probe),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(runtime.main(),1);self.assertEqual(out.getvalue(),'RELEASE_AUTH_FAILED_ValueError\n')
            self.assertNotIn('never-export',''.join(p.read_text('utf-8') for p in root.iterdir()))
            probe.run.assert_called_once();probe.failure_observations.assert_called_once();probe.stop.assert_called_once()


if __name__=='__main__':unittest.main()
