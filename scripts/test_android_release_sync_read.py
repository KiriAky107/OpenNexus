"""Empty/download SQLite and closed GUI orchestration; no device/network starts."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import android_release_sync_read as runtime
import android_release_sync_read_snapshot as snapshots
import test_android_release_sync as original_fixture


def metadata(raw):
    names={db+s for db in snapshots.DATABASES for s in ('','-wal','-shm')}
    names.update(p.relative_to(raw).as_posix() for p in (raw/'vault').rglob('*') if p.is_file())
    files={name:dict(bytes=(raw/name).stat().st_size,sha256=hashlib.sha256((raw/name).read_bytes()).hexdigest())
        if (raw/name).exists() else None for name in names}
    (raw/'snapshot.json').write_text(json.dumps(dict(package=runtime.APP,app_stopped_before_and_after=True,
        credential_store_copied=False,SQL_executed_on_device=False,files=files)), 'utf-8')


def fixture(root, downloaded=False, bound=True):
    raw=original_fixture.ReleaseSyncChecks().fixture(root,bound=bound)
    for path in original_fixture.runtime.LOCAL: (raw/'vault'/path).unlink()
    bodies={runtime.PEER[0]:runtime.PEER_NOTE,runtime.PEER[1]:runtime.PEER_CANVAS,runtime.ASSET:runtime.IMAGE_BYTES} if downloaded else {}
    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
        db.execute('DELETE FROM files')
        for index,(path,body) in enumerate(bodies.items()):
            out=raw/'vault'/path;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(body)
            db.execute('INSERT INTO files VALUES(?,?,?,?,?)',('peer-'+str(index),path,hashlib.sha256(body).hexdigest(),1,0))
        db.commit()
    metadata(raw);return raw


def add_draft(raw, path=runtime.PEER[0], *, generation=0, body=None):
    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as host:
        file=host.execute('SELECT id,hash FROM files WHERE path=?',(path,)).fetchone()
    body=(raw/'vault'/path).read_bytes().decode() if body is None else body
    with closing(sqlite3.connect(raw/snapshots.DATABASES[0])) as db:
        db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',('draft-'+path,file[0],path,file[1],generation,0,body));db.commit()
    metadata(raw)


class SyncReadChecks(unittest.TestCase):
    def test_actual_empty_and_download_SQLite_closed_projection(self):
        for downloaded,bound in ((False,False),(False,True),(True,True)):
            with self.subTest(downloaded=downloaded,bound=bound),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=fixture(root,downloaded,bound)
                before={p.relative_to(raw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()}
                value=snapshots.project_download(raw,root/'working',downloaded=downloaded)
                self.assertEqual(len(value['files']),3 if downloaded else 0)
                self.assertEqual(value['all_clean_drafts'],[]);self.assertEqual(value['all_save_count'],0)
                self.assertNotIn('never-read-this',json.dumps(value));self.assertFalse(value['credentials_selected'])
                self.assertEqual(before,{p.relative_to(raw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()})

    def test_open_initializes_clean_draft_and_cold_reuses_original_id_without_DB_byte_equality(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=fixture(root,True)
            before=snapshots.project_download(raw,root/'before',downloaded=True)
            add_draft(raw)
            after=snapshots.project_download(raw,root/'after',downloaded=True)
            self.assertNotEqual(before['raw_sha256'][snapshots.DATABASES[0]],after['raw_sha256'][snapshots.DATABASES[0]])
            snapshots.preserve_editor_read(before,after,runtime.PEER[0])
            cold=snapshots.project_download(raw,root/'cold',downloaded=True)
            snapshots.preserve_editor_read(after,cold,runtime.PEER[0])
            add_draft(raw,runtime.PEER[1]);canvas=snapshots.project_download(raw,root/'canvas',downloaded=True)
            snapshots.preserve_editor_read(cold,canvas,runtime.PEER[1])

    def test_hidden_Save_outbox_dirty_foreign_or_changed_body_rejected(self):
        for mode in ('save','outbox','dirty','changed-body','foreign-draft','download-not-declared','not-bool'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw=fixture(root,True)
                if mode in ('dirty','changed-body'): add_draft(raw,generation=1 if mode=='dirty' else 0,body='changed' if mode=='changed-body' else None)
                if mode in ('save','foreign-draft'):
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[0])) as db:
                        if mode=='save':db.execute('INSERT INTO save_intents(operation_id,path,state) VALUES(?,?,?)',('save','foreign.md','prepared'))
                        else:db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',('foreign','id','foreign.md','a'*64,0,0,'foreign'))
                        db.commit()
                if mode=='outbox':
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                        db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',('id','peer-0',1,runtime.PEER[0],'a'*64,'put','pending'));db.commit()
                metadata(raw)
                with self.assertRaises(RuntimeError):snapshots.project_download(raw,root/'working',downloaded=False if mode=='download-not-declared' else 1 if mode=='not-bool' else True)

    def test_editor_read_refuses_changed_transfer_and_unrelated_or_missing_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=fixture(root,True);before=snapshots.project_download(raw,root/'before',downloaded=True)
            add_draft(raw);after=snapshots.project_download(raw,root/'after',downloaded=True)
            for mode in ('head','bytes','wrong-open','missing-open','changed-existing'):
                value=deepcopy(after);start=before
                if mode=='head':value['native']['heads'].append(dict(path=runtime.PEER[0],hash='x'))
                elif mode=='bytes':value['raw_sha256']['vault/'+runtime.PEER[0]]='changed'
                elif mode=='missing-open':value['all_clean_drafts']=[]
                elif mode=='changed-existing':start=after;value['all_clean_drafts'][0]['id']='replacement'
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):
                    snapshots.preserve_editor_read(start,value,runtime.PEER[1] if mode=='wrong-open' else runtime.PEER[0])

    def test_original_read_driver_command_is_closed_and_unknown_not_retried(self):
        subject=runtime.ReleaseSyncRead.__new__(runtime.ReleaseSyncRead)
        subject.stop=Mock();subject.activity=runtime.APP+'/.MainActivity';subject.phases=[]
        subject.shell=Mock(return_value=SimpleNamespace(stdout=b'1234'))
        subject.original_command=Mock(side_effect=[SimpleNamespace(stdout=b''),RuntimeError('unknown original input')])
        with self.assertRaises(RuntimeError):subject.phase(runtime.READ_PHASES[0],'markdown-open')
        self.assertEqual(subject.original_command.call_count,2);self.assertEqual(subject.stop.call_count,2)
        command=subject.original_command.call_args.args[1][-1]
        self.assertIn('/.ReleaseSyncReadRuntimeProbe',command);self.assertIn(runtime.READ_PHASES[0],command)
        self.assertNotIn('Save',command);self.assertNotIn('-e path',command);self.assertFalse(subject.phases)


if __name__=='__main__':unittest.main()
