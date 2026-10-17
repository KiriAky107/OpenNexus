"""Synthetic SQLite/privacy boundaries; no packaged Desktop is launched."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from android_desktop_handoff import HandoffError
from android_desktop_pair_metadata import owned_native_metadata,read_transfer_metadata


class PairMetadata(unittest.TestCase):
    binding='11111111-1111-4111-8111-111111111111'

    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE sync_bindings(id,remote_vault,state,cursor);
            CREATE TABLE sync_jobs(binding,operation_id,file_id,path,hash,size,operation,state,base_revision,remote_revision,error);
            CREATE TABLE sync_attempts(binding,operation_id,attempts,outcome,error);
            CREATE TABLE sync_heads(binding,file_id,revision,path,hash);
            CREATE TABLE sync_inbox(binding,sequence,operation_id,rename_id,state);
            CREATE TABLE sync_conflicts(binding,sequence,file_id,local_path,local_hash,state);
            CREATE TABLE auth_secrets(password,access_token);
            INSERT INTO auth_secrets VALUES ('DO_NOT_EXPORT_PASSWORD','DO_NOT_EXPORT_TOKEN');
        ''')
        self.db.execute('INSERT INTO sync_bindings VALUES (?,?,?,?)',(self.binding,'f'*32,'active',1))
        self.db.execute('INSERT INTO sync_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (self.binding,'original-operation','original-file','runtime-note.md','a'*64,99,'put','acked',0,1,None))
        self.db.execute('INSERT INTO sync_heads VALUES (?,?,?,?,?)',(self.binding,'original-file',1,'runtime-note.md','a'*64))
        self.db.commit()

    def test_readonly_snapshot_exports_original_transfer_columns_without_secret_tables_or_writes(self):
        before=self.db.total_changes
        value=read_transfer_metadata(self.db,self.binding)
        self.assertEqual(self.db.total_changes,before)
        self.assertEqual(value['jobs'][0]['operation_id'],'original-operation')
        self.assertEqual(value['heads'][0]['file_id'],'original-file')
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(value));self.assertFalse(value['credentials_or_session_selected'])
        self.assertEqual(self.db.execute('SELECT * FROM auth_secrets').fetchone(),('DO_NOT_EXPORT_PASSWORD','DO_NOT_EXPORT_TOKEN'))
        with self.assertRaises(sqlite3.OperationalError):self.db.execute('DELETE FROM sync_jobs')

    def test_foreign_binding_and_unbounded_rows_are_refused(self):
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_METADATA_BINDING_INVALID'):
            read_transfer_metadata(self.db,'22222222-2222-4222-8222-222222222222')
        self.db.execute('PRAGMA query_only=OFF')
        self.db.executemany('INSERT INTO sync_heads VALUES (?,?,?,?,?)',[(self.binding,'extra-'+str(i),1,'fixture.md','a'*64) for i in range(513)])
        self.db.commit()
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_METADATA_ROWS_EXCEEDED'):read_transfer_metadata(self.db,self.binding)

    def test_nonpublic_error_payload_is_refused_instead_of_becoming_an_artifact(self):
        self.db.execute('UPDATE sync_jobs SET error=?',('raw password=DO_NOT_EXPORT_PASSWORD',));self.db.commit()
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_METADATA_ERROR_NOT_PUBLIC'):read_transfer_metadata(self.db,self.binding)

    def test_local_host_refuses_before_native_database_or_any_user_profile_is_opened(self):
        context={'run_id':1234,'tools_commit':'a'*40,'android_commit':'b'*40,
                 'desktop_commit':'931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True),patch('sqlite3.connect') as connect:
            with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_HOST_REFUSED'):owned_native_metadata(context,self.binding)
            connect.assert_not_called();self.assertEqual(list(Path(folder).iterdir()),[])


if __name__=='__main__':unittest.main(verbosity=2)
