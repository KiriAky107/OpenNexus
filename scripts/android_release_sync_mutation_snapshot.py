"""Closed peer rename/delete index and live-content projections on new readonly copies."""
from contextlib import closing
from pathlib import Path
import hashlib
import sqlite3

from android_release_sync import PEER_NOTE, PEER_CANVAS
from android_release_sync_snapshot import ASSET, DATABASES, PEER, RENAMED, _project_owned, rows
from android_runtime_probe import require

STAGES = ('empty', 'associated', 'downloaded', 'renamed', 'deleted', 'cold')


def live_paths(stage):
    require(type(stage) is str and stage in STAGES, 'RELEASE_SYNC_MUTATION_EXACT_STAGE')
    if stage in ('empty', 'associated'): return ()
    if stage == 'downloaded': return PEER + (ASSET,)
    if stage == 'renamed': return (RENAMED, PEER[1], ASSET)
    return (PEER[1], ASSET)


def absent_paths(stage):
    live_paths(stage)
    return (PEER[0], RENAMED) if stage in ('deleted', 'cold') else (PEER[0],) if stage == 'renamed' else ()


def project_mutations(snapshot, working, *, stage):
    paths = live_paths(stage)
    value = _project_owned(snapshot, working, paths, deleted_peer_note=stage in ('deleted', 'cold'))
    with closing(sqlite3.connect((Path(working).resolve()/DATABASES[0]).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        require(not rows(db, 'SELECT id,file_id,path,generation,saved_generation FROM drafts')
                and not rows(db, 'SELECT operation_id,path,state FROM save_intents'), 'RELEASE_SYNC_MUTATION_HIDDEN_DRAFT_OR_SAVE')
    require(all(not value['native'][name] for name in ('outbox', 'jobs', 'attempts', 'issuers', 'conflicts')),
            'RELEASE_SYNC_MUTATION_UNEXPECTED_LOCAL_WRITE')
    hashes={PEER[0]:hashlib.sha256(PEER_NOTE).hexdigest(),RENAMED:hashlib.sha256(PEER_NOTE).hexdigest(),
            PEER[1]:hashlib.sha256(PEER_CANVAS).hexdigest(),ASSET:ASSET.rsplit('/',1)[1][:-4]}
    require(all(row['hash']==hashes[row['path']] for row in value['files']), 'RELEASE_SYNC_MUTATION_FIXED_BODY_HASH')
    value.update(mutation_stage=stage, absent_paths=list(absent_paths(stage)), all_clean_drafts=[], all_save_count=0)
    return value


def preserve_cold_mutation(before, after):
    require(before['mutation_stage'] == 'deleted' and after['mutation_stage'] == 'cold', 'RELEASE_SYNC_MUTATION_EXACT_COLD_STAGE')
    require({k:v for k,v in before.items() if k not in ('raw_sha256', 'mutation_stage')} ==
            {k:v for k,v in after.items() if k not in ('raw_sha256', 'mutation_stage')}, 'RELEASE_SYNC_MUTATION_COLD_RECORDS_CHANGED')
    for path in live_paths('cold'):
        require(before['raw_sha256']['vault/'+path] == after['raw_sha256']['vault/'+path], 'RELEASE_SYNC_MUTATION_COLD_BYTES_CHANGED')
