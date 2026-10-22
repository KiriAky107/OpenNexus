"""Dedicated empty-vault/download projections; GUI opens may initialize clean drafts."""
from contextlib import closing
from pathlib import Path
import hashlib
import sqlite3

from android_release_observation import DATABASES
from android_release_sync_snapshot import ASSET, PEER, _project_owned, rows
from android_runtime_probe import require


def project_download(snapshot, working, *, downloaded):
    require(type(downloaded) is bool, 'RELEASE_SYNC_READ_EXACT_STAGE')
    paths = PEER + (ASSET,) if downloaded else ()
    value = _project_owned(snapshot, working, paths)
    # Unlike the upload projection, inspect ALL draft/save rows so unrelated
    # documents or a hidden Save cannot disappear behind per-path WHERE clauses.
    columns = 'id,file_id,path,base_hash,generation,saved_generation,content'
    with closing(sqlite3.connect((Path(working).resolve()/DATABASES[0]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        drafts = [dict(zip(columns.split(','), row)) for row in rows(db, 'SELECT '+columns+' FROM drafts ORDER BY path,id')]
        saves = rows(db, 'SELECT operation_id,path,state FROM save_intents ORDER BY rowid')
    require(not saves and not value['native']['outbox'] and not value['native']['jobs']
            and not value['native']['attempts'] and not value['native']['issuers'], 'RELEASE_SYNC_READ_UNEXPECTED_WRITE')
    files = {row['path']: row for row in value['files']}
    require(len(drafts) <= 2 and len({row['path'] for row in drafts}) == len(drafts), 'RELEASE_SYNC_READ_DRAFT_SET')
    for row in drafts:
        file = files.get(row['path'])
        require(downloaded and row['path'] in PEER and file is not None and row['file_id'] == file['id']
                and row['base_hash'] == file['hash'] and row['generation'] == row['saved_generation'] == 0
                and hashlib.sha256(row['content'].encode()).hexdigest() == file['hash'],
                'RELEASE_SYNC_READ_DIRTY_OR_FOREIGN_DRAFT')
    value['all_clean_drafts'] = drafts
    value['all_save_count'] = len(saves)
    value['empty_local_download_contract'] = True
    return value


def preserve_editor_read(before, after, opened):
    require(opened in PEER and before['native'] == after['native'] and before['files'] == after['files']
            and before['all_save_count'] == after['all_save_count'] == 0, 'RELEASE_SYNC_READ_CHANGED_TRANSFER_RECORDS')
    content = {'vault/'+path for path in PEER+(ASSET,)}
    require({k:v for k,v in before['raw_sha256'].items() if k in content} ==
            {k:v for k,v in after['raw_sha256'].items() if k in content}, 'RELEASE_SYNC_READ_CHANGED_ORIGINAL_BYTES')
    prior = {row['path']: row for row in before['all_clean_drafts']}
    final = {row['path']: row for row in after['all_clean_drafts']}
    require(set(prior) <= set(final) and set(final)-set(prior) <= {opened}
            and all(final[path] == row for path,row in prior.items()), 'RELEASE_SYNC_READ_CHANGED_OR_UNRELATED_DRAFT')
    # The actual mobile open_note inserts a clean generation0/saved0 draft or
    # checks out the existing one; the raw SQLite/WAL bytes may legitimately change.
    require(opened in final, 'RELEASE_SYNC_READ_OPENED_DRAFT_NOT_OBSERVED')
