"""Bounded, read-only native transfer evidence from the owned Desktop vault.

Only public transfer columns are selected. Credentials, sessions, journal
content, and raw databases never become proof artifacts. A running client may
continue its own writes; a consistent SQLite read does not freeze the client.
"""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import stat

from android_desktop_handoff import require
from android_desktop_product import directory_identity, original_marker, owned_root


PROJECTIONS={
    'jobs':('sync_jobs','operation_id,file_id,path,hash,size,operation,state,base_revision,remote_revision,error'),
    'attempts':('sync_attempts','operation_id,attempts,outcome,error'),
    'heads':('sync_heads','file_id,revision,path,hash'),
    'inbox':('sync_inbox','sequence,operation_id,rename_id,state'),
    'conflicts':('sync_conflicts','sequence,file_id,local_path,local_hash,state'),
}


def read_transfer_metadata(db, binding):
    require(type(binding) is str and re.fullmatch(r'[0-9a-f-]{36}',binding), 'DESKTOP_PAIR_METADATA_SCOPE_INVALID')
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    try:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',), 'DESKTOP_PAIR_METADATA_INTEGRITY_INVALID')
        bindings=db.execute('SELECT id,remote_vault,state,cursor FROM sync_bindings').fetchall()
        require(len(bindings)==1 and bindings[0][0]==binding and bindings[0][2]=='active',
                'DESKTOP_PAIR_METADATA_BINDING_INVALID')
        result={'binding':dict(zip(('id','remote_vault','state','cursor'),bindings[0]))}
        for name,(table,columns) in PROJECTIONS.items():
            count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE binding=?',(binding,)).fetchone()[0]
            require(0<=count<=512, 'DESKTOP_PAIR_METADATA_ROWS_EXCEEDED')
            rows=[dict(zip(columns.split(','),r)) for r in db.execute(
                'SELECT '+columns+' FROM '+table+' WHERE binding=? ORDER BY rowid LIMIT 512',(binding,))]
            require(len(rows)==count, 'DESKTOP_PAIR_METADATA_SNAPSHOT_CHANGED')
            for row in rows:
                for key,value in row.items():
                    require(value is None or type(value) is int or type(value) is str and len(value)<=1024,
                            'DESKTOP_PAIR_METADATA_VALUE_INVALID')
                    if key=='error':
                        require(value is None or type(value) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}',value),
                                'DESKTOP_PAIR_METADATA_ERROR_NOT_PUBLIC')
            result[name]=rows
        return {**result,'integrity':'ok','snapshot_transaction':'SQLite query_only BEGIN',
                'credentials_or_session_selected':False,'raw_database_uploaded':False}
    finally:
        db.rollback()


def owned_native_metadata(context, binding):
    root=owned_root(os.environ,context);original_marker(root,context)
    vault=root/'vault';managed=vault/'.ainote'
    directory_identity(vault);directory_identity(managed)
    path=managed/'host.sqlite3';identity=path.stat(follow_symlinks=False)
    require(stat.S_ISREG(identity.st_mode) and identity.st_nlink==1
            and not getattr(identity,'st_file_attributes',0)&0x400 and not path.is_symlink(),
            'DESKTOP_PAIR_METADATA_PATH_INVALID')
    require(path.resolve().is_relative_to(vault.resolve()), 'DESKTOP_PAIR_METADATA_PATH_INVALID')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        return read_transfer_metadata(db,binding)
