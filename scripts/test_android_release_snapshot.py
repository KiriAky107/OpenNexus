from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from android_release_observation import APP, DATABASES
from android_release_snapshot import fingerprint, inspect_stopped_snapshot, same_originals

NAME='release-owned-note.md'


class SnapshotBoundaries(unittest.TestCase):
    def fixture(self, root):
        snapshot=root/'raw';snapshot.mkdir();(snapshot/'vault/.ainote').mkdir(parents=True)
        raw=b'# original\r\n\xe4\xb8\xad\xe6\x96\x87';(snapshot/'vault'/NAME).write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
        with closing(sqlite3.connect(snapshot/DATABASES[0])) as db:
            db.execute('CREATE TABLE drafts(id,file_id,path,base_hash,generation,saved_generation,content,edit_session)')
            db.execute('CREATE TABLE save_intents(operation_id,draft_id,generation,file_id,path,expected,content,state,error)')
            db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?)',('draft','file',NAME,sha,2,1,'unconfirmed draft','before'))
            db.execute('INSERT INTO save_intents VALUES(?,?,?,?,?,?,?,?,?)',('save','draft',1,'file',NAME,sha,raw.decode(),'committed',None));db.commit()
        with closing(sqlite3.connect(snapshot/DATABASES[1])) as db:
            db.execute('CREATE TABLE files(id,path,hash,revision,deleted)')
            db.execute('CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state)')
            db.execute('CREATE TABLE operations(operation_id,fingerprint,state,result)')
            db.execute('INSERT INTO files VALUES(?,?,?,?,?)',('file',NAME,sha,1,0))
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',('save','file',1,NAME,sha,'write','pending'))
            db.execute('INSERT INTO operations VALUES(?,?,?,?)',('save','a'*64,'committed','original own result'));db.commit()
        items={}
        for path in [*(snapshot/v for v in DATABASES),snapshot/'vault'/NAME]:
            items[path.relative_to(snapshot).as_posix()]=dict(bytes=path.stat().st_size,sha256=fingerprint(path))
        for database in DATABASES:
            for suffix in ('-wal','-shm'):items[database+suffix]=None
        record=dict(package=APP,files=items,app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False)
        (snapshot/'snapshot.json').write_text(json.dumps(record),'utf-8')
        return snapshot,record

    def test_actual_sqlite_own_projection_inspects_working_copy_and_retains_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot,record=self.fixture(root)
            value=inspect_stopped_snapshot(snapshot,root/'work',[NAME])
            self.assertEqual(value['owned_files'][NAME]['drafts'][0]['content'],'unconfirmed draft')
            self.assertEqual(value['owned_files'][NAME]['operations'][0]['result'],'original own result')
            self.assertTrue(value['retained_DB_WAL_SHM_never_opened_by_SQLite'])
            self.assertFalse(value['actual_GUI_upgrade_or_physical_result'])
            for relative,item in record['files'].items():
                if item is not None:self.assertEqual(fingerprint(snapshot/relative),item['sha256'])
            with self.assertRaisesRegex(RuntimeError,'NEW_WORKING_COPY_REQUIRED'):inspect_stopped_snapshot(snapshot,snapshot/'work',[NAME])
            with self.assertRaisesRegex(RuntimeError,'NEW_WORKING_COPY_REQUIRED'):inspect_stopped_snapshot(snapshot,root/'work',[NAME])

    def test_tampered_original_extra_credentials_and_absence_cannot_enter_projection(self):
        for mode in ('raw','credential','wal'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);snapshot,record=self.fixture(root)
                if mode=='raw':(snapshot/'vault'/NAME).write_bytes(b'changed')
                elif mode=='credential':
                    record['files']['mobile-sync-auth.sqlite3']=dict(bytes=1,sha256='a'*64)
                    (snapshot/'snapshot.json').write_text(json.dumps(record),'utf-8')
                else:(snapshot/(DATABASES[0]+'-wal')).write_bytes(b'new WAL')
                with self.assertRaises(RuntimeError):inspect_stopped_snapshot(snapshot,root/'work',[NAME])

    def test_only_transient_edit_session_is_excluded_from_upgrade_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot,_=self.fixture(root);before=inspect_stopped_snapshot(snapshot,root/'work',[NAME])
            after=deepcopy(before);after['owned_files'][NAME]['drafts'][0]['edit_session']='after'
            self.assertTrue(same_originals(before,after)['only_transient_edit_session_excluded'])
            for section in ('files','drafts','saves','outbox','operations'):
                bad=deepcopy(after);bad['owned_files'][NAME][section][0]['unexpected']='changed'
                with self.subTest(section=section),self.assertRaisesRegex(RuntimeError,'ORIGINAL_ROWS_CHANGED'):same_originals(before,bad)
            after['raw_sha256']['vault/'+NAME]='b'*64
            with self.assertRaisesRegex(RuntimeError,'ORIGINAL_BYTES_CHANGED'):same_originals(before,after)


if __name__=='__main__':unittest.main()
