"""Actual owned GUI nested-note literal search; no native IPC or search-result injection."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP, require, require_hosted_emulator

DIRECTORY = '检索'
NAME, CONTROL_NAME = '资料😀.md', '对照.md'
PATH, CONTROL_PATH = DIRECTORY + '/' + NAME, DIRECTORY + '/' + CONTROL_NAME
PATHS = (PATH, CONTROL_PATH)
SEED = '# 离线检索原文 😀\n专属查找文本 %_.*\nOriginal source remains literal.\n'.encode()
ORIGINAL = SEED.replace(b'\n', b'\r\n')
CONTROL = '# 对照笔记\nControl note without the query tokens.\n'.encode()
QUERIES = ('专属查找文本', '%_', '.*', '资料😀', '😀', '对照')
STEPS = ('search-seed-original', 'search-seed-control', 'search-browse',
         *(f'search-query-{i}' for i in range(6)), 'search-open')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def digest(value): return hashlib.sha256(value).hexdigest()


def seed_bindings(value, originals):
    """Bind immutable NULL creation intents to their actual original native result."""
    require(type(originals) is dict and set(originals) <= set(PATHS) and originals, 'SEARCH_OWN_PATHS')
    require(len(value['saves']) == len(value['files']) == len(value['drafts']) == len(originals), 'SEARCH_ORIGINAL_COUNTS')
    require(len(value['operations']) == len(value['outbox']) == len(originals), 'SEARCH_ORIGINAL_RECEIPT_COUNTS')
    bindings = {}
    for path, seed in originals.items():
        saves = [x for x in value['saves'] if x['path'] == path]
        files = [x for x in value['files'] if x['path'] == path]
        drafts = [x for x in value['drafts'] if x['path'] == path]
        require(len(saves) == len(files) == len(drafts) == 1, 'SEARCH_ORIGINAL_NOT_UNIQUE')
        intent, entry, draft = saves[0], files[0], drafts[0]
        operation = intent['operation_id']
        require(re.fullmatch(UUID, operation) and re.fullmatch(UUID, entry['id']) and entry['deleted'] == 0
                and intent['file_id'] in (None, entry['id']) and intent['expected'] == ''
                and intent['content'].encode() == seed and intent['state'] == 'committed' and intent['error'] is None,
                'SEARCH_ORIGINAL_CREATION_INTENT_CHANGED')
        receipts = [x for x in value['operations'] if x['operation_id'] == operation]
        outbox = [x for x in value['outbox'] if x['operation_id'] == operation]
        require(len(receipts) == len(outbox) == 1 and receipts[0]['state'] == 'committed' and re.fullmatch(r'[0-9a-f]{64}',receipts[0]['fingerprint']), 'SEARCH_ORIGINAL_NOT_COMMITTED')
        result = json.loads(receipts[0]['result'])
        require(result['file_id'] == entry['id'] == draft['file_id'] == outbox[0]['file_id']
                and result['path'] == outbox[0]['path'] == path and result['hash'] == outbox[0]['hash'] == digest(seed)
                and result['deleted'] is False and outbox[0]['operation'] == 'put'
                and outbox[0]['revision'] == result['revision'] == 1
                and draft['generation'] == draft['saved_generation'], 'SEARCH_ORIGINAL_RESULT_OR_OUTBOX_CHANGED')
        bindings[path] = {'file_id': entry['id'], 'operation_id': operation, 'fingerprint': receipts[0]['fingerprint'], 'seed_sha256': digest(seed)}
    require(len({x['file_id'] for x in bindings.values()}) == len(bindings)
            and len({x['operation_id'] for x in bindings.values()}) == len(bindings), 'SEARCH_ORIGINAL_IDENTITIES_REUSED')
    return bindings


def readonly_projection(value):
    return {key: value[key] for key in ('saves', 'files', 'outbox', 'operations')}


def snapshot(probe, label):
    require(probe.args.scope == 'note-search' and (label in STEPS or label.endswith('-failure') and label.removesuffix('-failure') in STEPS), 'SEARCH_SNAPSHOT_SCOPE')
    fields = {'drafts': 'id,file_id,path,base_hash,generation,saved_generation,content,edit_session',
              'saves': 'operation_id,draft_id,generation,file_id,path,expected,content,state,error'}
    value = {}
    for name, columns in fields.items():
        table = 'save_intents' if name == 'saves' else name
        rows = probe.database_rows('SELECT ' + columns + ' FROM ' + table + ' WHERE path IN (?,?) ORDER BY path,rowid', PATHS)
        value[name] = [dict(zip(columns.split(','), row)) for row in rows]
    folder = Path(tempfile.mkdtemp(prefix='owned-note-search-native-', dir=probe.tools)); database = folder/'host.sqlite3'
    for suffix in ('', '-wal', '-shm'):
        raw = probe.private(probe.root + 'vault/.ainote/host.sqlite3' + suffix, check=False)
        if raw.returncode == 0: Path(str(database)+suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'SEARCH_NATIVE_DATABASE_INTEGRITY')
        columns = 'id,path,hash,revision,deleted'
        value['files'] = [dict(zip(columns.split(','), row)) for row in db.execute('SELECT '+columns+' FROM files WHERE path IN (?,?) ORDER BY path',PATHS)]
        ids = [x['id'] for x in value['files']]
        require(1 <= len(ids) <= 2, 'SEARCH_NATIVE_FILE_COUNT')
        columns = 'operation_id,file_id,revision,path,hash,operation,state'
        value['outbox'] = [dict(zip(columns.split(','), row)) for row in db.execute('SELECT '+columns+' FROM outbox WHERE file_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)]
        operations = sorted({x['operation_id'] for x in value['saves']+value['outbox']})
        require(1 <= len(operations) <= 4, 'SEARCH_NATIVE_OPERATION_BOUND')
        columns = 'operation_id,fingerprint,state,result'
        value['operations'] = [dict(zip(columns.split(','), row)) for row in db.execute('SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in operations)+') ORDER BY operation_id',operations)]
    atomic_json(probe.output/(label+'-native.json'),value,exclusive=True)
    return value


def intent(probe, step, **details):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope == 'note-search' and step in (*STEPS,'external-owned-CRLF'), 'SEARCH_INTENT_SCOPE')
    atomic_json(probe.output/(step+'-original-intent.json'),{'run_id':os.environ['GITHUB_RUN_ID'],
        'tools_source':probe.args.tools_commit,'compiled_source':probe.args.commit,'scope':'note-search','step':step,
        'owned_paths':PATHS,'automatic_retry':False,'native_operation_ID_assigned_by_driver':False,**details},exclusive=True)


def run_note_search(probe):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope == 'note-search','SEARCH_EXPLICIT_SCOPE')
    proof = {'passed':False,'scope':'note-search','tools_source':probe.args.tools_commit,'compiled_source':probe.args.commit,
        'APK_sha256':probe.binding['sha256'],'run_id':os.environ['GITHUB_RUN_ID'],
        'physical_phone_or_all_A2_A6_complete':False,'queries':[]}
    current = None
    def phase(name):
        nonlocal current
        current=name;proof['phase']=name;intent(probe,name)
        probe.phase(name,continue_process=name.startswith('search-query-') or name=='search-open',keep_running=True)
        return snapshot(probe,name)
    try:
        probe.enable_test_ime()
        first=phase('search-seed-original');seed_bindings(first,{PATH:SEED})
        second=phase('search-seed-control');bindings=seed_bindings(second,{PATH:SEED,CONTROL_PATH:CONTROL})
        require(probe.original(PATH)==SEED and probe.original(CONTROL_PATH)==CONTROL,'SEARCH_ACTUAL_GUI_SEED_BYTES')
        current='external-owned-CRLF';proof['phase']=current
        intent(probe,current,before_sha256=digest(SEED),after_sha256=digest(ORIGINAL),
            actual_GUI_typed_CRLF_preservation_not_claimed=True)
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+PATH))),input=ORIGINAL)
        require(probe.original(PATH)==ORIGINAL,'SEARCH_DECLARED_OWN_CRLF_BYTES')
        old=probe.shell('pidof',APP).stdout.strip();before=phase('search-browse');new=probe.shell('pidof',APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}',old) and re.fullmatch(rb'[1-9][0-9]{0,9}',new) and old!=new,'SEARCH_COLD_NOT_NEW_APP_PID')
        require(seed_bindings(before,{PATH:SEED,CONTROL_PATH:CONTROL})==bindings,'SEARCH_COLD_ORIGINAL_IDS_CHANGED')
        expected=readonly_projection(before)
        for ordinal,query in enumerate(QUERIES):
            after=phase('search-query-'+str(ordinal))
            require(readonly_projection(after)==expected,'SEARCH_READ_REPLACED_ORIGINAL_NATIVE_DATA')
            require(probe.original(PATH)==ORIGINAL and probe.original(CONTROL_PATH)==CONTROL,'SEARCH_READ_CHANGED_ORIGINAL_BYTES')
            log=(probe.output/('search-query-'+str(ordinal)+'.log')).read_text('utf-8')
            require(log.count('ORIGINAL_NOTE_SEARCH_RESULT '+str(ordinal)+' negative=0 positive=1')==1,'SEARCH_ACTUAL_RESULT_MARKER_MISSING')
            proof['queries'].append({'ordinal':ordinal,'literal_query':query,'expected':CONTROL_NAME if ordinal==5 else NAME,
                'preceded_by_actual_empty_results':True,'nonmatching_control_excluded':True})
        final=phase('search-open')
        require(readonly_projection(final)==expected and seed_bindings(final,{PATH:SEED,CONTROL_PATH:CONTROL})==bindings,'SEARCH_FINAL_READ_REPLACED_ORIGINALS')
        require(next(x for x in final['drafts'] if x['path']==PATH)['content'].encode()==ORIGINAL,'SEARCH_OPEN_LOST_NATIVE_CRLF_CONTENT')
        require(probe.original(PATH)==ORIGINAL and probe.original(CONTROL_PATH)==CONTROL,'SEARCH_FINAL_BYTES_CHANGED')
        proof.update(passed=True,original_bindings=bindings,cold_original_PID=int(old),cold_new_PID=int(new),
            original_CRLF_sha256=digest(ORIGINAL),original_CRLF_bytes=len(ORIGINAL),control_sha256=digest(CONTROL),
            original_save_intents_receipts_and_outbox_retained=True,actual_UI_browse_search_and_open=True,
            fresh_GUI_seed_then_one_explicit_owned_CRLF_fixture_change=True,exact_AX_note_body_uses_normalized_linebreaks=True)
    except Exception as error:
        proof['failure_class']=type(error).__name__
        if probe.root is not None and current in STEPS:
            try: snapshot(probe,current+'-failure')
            except Exception as failure:proof['failure_observation_class']=type(failure).__name__
        raise
    finally:
        atomic_json(probe.output/'note-search-proof.json',proof,exclusive=True)
        probe.force_stop()
