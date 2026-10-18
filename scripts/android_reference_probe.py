"""Actual owned Native local reference navigation, with immutable saved originals."""
from contextlib import closing
from pathlib import Path
import hashlib,json,os,re,shlex,sqlite3,tempfile,time
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,DRIVER,require,require_hosted_emulator
from android_canvas_graph_probe import saved_original
from android_reference_contract import (SOURCE_MD,SOURCE_CANVAS,TARGET_MD,TARGET_CANVAS,
    TARGET_BODY,TARGET_CANVAS_TEXT,SOURCE_BODY,DRAFT_BODY,MISSING,UNSAFE,PATHS,STEPS,
    originals,same_originals,stable_state)

def driver_form():
    return dict(schema=1,scope='local-references',source_md=SOURCE_MD,source_canvas=SOURCE_CANVAS,
        target_md=TARGET_MD,target_canvas=TARGET_CANVAS,target_body=TARGET_BODY,
        target_canvas_text=TARGET_CANVAS_TEXT,source_body=SOURCE_BODY,draft_body=DRAFT_BODY,
        missing=MISSING,unsafe=UNSAFE)

def snapshot(probe,label):
    require(probe.args.scope=='local-references' and label.removesuffix('-failure') in STEPS,
        'REFERENCE_SNAPSHOT_SCOPE')
    result={path:{} for path in PATHS}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
        ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        for path in PATHS:
            result[path][key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows(
                'SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(path,))]
    folder=Path(tempfile.mkdtemp(prefix='owned-local-reference-native-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        raw=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if raw.returncode==0:Path(str(database)+suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'REFERENCE_NATIVE_INTEGRITY')
        for path,item in result.items():
            columns='id,path,hash,revision,deleted'
            item['files']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM files WHERE path=?',(path,))]
            require(len(item['files'])<=1,'REFERENCE_SINGLE_OWN_FILE')
            columns='operation_id,file_id,revision,path,hash,operation,state'
            item['outbox']=[dict(zip(columns.split(','),row)) for row in db.execute(
                'SELECT '+columns+' FROM outbox WHERE file_id=? ORDER BY operation_id',
                (item['files'][0]['id'] if item['files'] else '',))]
            ids=sorted({x['operation_id'] for x in item['outbox']+item['saves']})
            require(len(ids)<=64,'REFERENCE_OPERATION_BOUND')
            columns='operation_id,fingerprint,state,result'
            item['operations']=[dict(zip(columns.split(','),row)) for row in db.execute(
                'SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)] if ids else []
    atomic_json(probe.output/(label+'-native.json'),result,exclusive=True);return result

def saved_projection(value):
    return {path:{k:item[k] for k in ('files','saves','outbox','operations')} for path,item in value.items()}

def await_source_draft(probe):
    # The generic dirty status can predate this input. Observe its exact durable
    # content/generation, without any UI action, body retry or Save approval.
    until=time.monotonic()+10
    while True:
        rows=probe.database_rows('SELECT content,generation,saved_generation FROM drafts WHERE path=? ORDER BY rowid LIMIT 2',(SOURCE_MD,))
        require(len(rows)<=1,'REFERENCE_AMBIGUOUS_SOURCE_DRAFT')
        if len(rows)==1 and rows[0][0]==DRAFT_BODY and rows[0][1]>rows[0][2]:return
        require(time.monotonic()<until,'REFERENCE_ORIGINAL_DRAFT_NOT_DURABLE');time.sleep(.2)

def validate_draft(before,after):
    require(saved_projection(before)==saved_projection(after),'REFERENCE_DRAFT_CHANGED_SAVED_STATE')
    other=[p for p in PATHS if p!=SOURCE_MD]
    require(all(stable_state(before)[p]==stable_state(after)[p] for p in other),'REFERENCE_DRAFT_CHANGED_OTHER_TARGET')
    require(len(after[SOURCE_MD]['drafts'])==1,'REFERENCE_SINGLE_ORIGINAL_DRAFT')
    value=after[SOURCE_MD]['drafts'][0]
    require(value['content']==DRAFT_BODY and value['generation']>value['saved_generation'],
        'REFERENCE_ORIGINAL_UNCONFIRMED_DRAFT')

def validate_frozen(expected,baseline,current,raws):
    same_originals(expected,raws)
    require(stable_state(baseline)==stable_state(current),'REFERENCE_NAVIGATION_CHANGED_NATIVE_DRAFT_OR_RECEIPT')
    for path in PATHS:
        require(len(current[path]['files'])==1 and current[path]['files'][0]['deleted']==0
            and current[path]['files'][0]['hash']==hashlib.sha256(raws[path]).hexdigest(),
            'REFERENCE_NATIVE_HASH_NOT_ORIGINAL')

def run_references(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='local-references','REFERENCE_EXPLICIT_SCOPE')
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],scope=probe.args.scope,
        executed_steps=[],setup_saves=[],synthetic_Canvas_setup_not_GUI_node_creation=True,
        old_Native_suites_and_other_accepted_scopes_not_replayed=True,
        external_OS_dispatch_verified=False,physical_phone_or_release_verified=False)
    current=None;expected=originals()
    def phase(name,*,cold=False):
        nonlocal current
        require(name==STEPS[len(report['executed_steps'])],'REFERENCE_ORIGINAL_STEP_ORDER');current=name
        atomic_json(probe.output/(name+'-intent.json'),dict(step=name,run=report['run_id'],scope=probe.args.scope,
            exact_owned_paths=list(PATHS),automatic_retry=False,Save_expected=name.endswith('-seed')),exclusive=True)
        probe.phase(name,continue_process=not cold,keep_running=True)
        if name=='reference-md-draft':await_source_draft(probe)
        state=snapshot(probe,name);report['executed_steps'].append(name)
        log=(probe.output/(name+'.log')).read_text('utf-8')
        require(log.count('ORIGINAL_LOCAL_REFERENCE '+name+' save='+str(int(name.endswith('-seed'))))==1,
            'REFERENCE_ORIGINAL_PHASE_NOT_CONFIRMED')
        require(log.count('ORIGINAL_REFERENCE_FORM_SHA256 '+hashlib.sha256(payload).hexdigest())==1,
            'REFERENCE_NATIVE_FORM_NOT_ORIGINAL_BYTES')
        return state
    try:
        require(probe.keystore_test_result is None,'REFERENCE_MUST_NOT_REPLAY_NATIVE_SUITES')
        probe.enable_test_ime()
        payload=json.dumps(driver_form(),ensure_ascii=False).encode();require(len(payload)<=8192,'REFERENCE_BOUNDED_FORM')
        atomic_json(probe.output/'reference-reviewed-form.json',driver_form(),exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',DRIVER,'sh','-c',
            'umask 077; mkdir -p files && test ! -e files/owned-reference-form.json && cat > files/owned-reference-form.json')),input=payload)
        for step,path in zip(STEPS[:4],(TARGET_MD,TARGET_CANVAS,SOURCE_MD,SOURCE_CANVAS)):
            state=phase(step,cold=True);raw=probe.original(path)
            require(raw==expected[path] if path.endswith('.md') else json.loads(raw)==dict(nodes=[],edges=[]),
                'REFERENCE_GUI_SEED_BYTES')
            report['setup_saves'].append(saved_original(None,state[path],raw,path=path))
        require(len({v['operation_id'] for v in report['setup_saves']})==4,'REFERENCE_FOUR_DISTINCT_SETUP_SAVES')
        probe.force_stop()  # No live editor can race the two declared fixture writes.
        for path in (SOURCE_CANVAS,TARGET_CANVAS):
            before=probe.original(path)
            atomic_json(probe.output/('reference-synthetic-'+path+'.json'),dict(path=path,
                before_sha256=hashlib.sha256(before).hexdigest(),after_sha256=hashlib.sha256(expected[path]).hexdigest(),
                declared_fixture_only_not_GUI_creation=True),exclusive=True)
            probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+path))),input=expected[path])
            require(probe.original(path)==expected[path],'REFERENCE_SYNTHETIC_ORIGINAL_BYTES')
        before=phase('reference-baseline',cold=True)
        same_originals(expected,{p:probe.original(p) for p in PATHS})
        frozen=phase('reference-md-draft');validate_draft(before,frozen)
        for step in STEPS[6:]:
            pid=probe.shell('pidof',APP).stdout.strip() if step==STEPS[-1] else None
            state=phase(step,cold=step==STEPS[-1])
            validate_frozen(expected,frozen,state,{p:probe.original(p) for p in PATHS})
            if pid is not None:
                after=probe.shell('pidof',APP).stdout.strip()
                require(re.fullmatch(rb'[1-9][0-9]{0,9}',pid) and re.fullmatch(rb'[1-9][0-9]{0,9}',after)
                    and pid!=after,'REFERENCE_COLD_NEW_PID')
                report['cold_PIDs']=[int(pid),int(after)]
        for path,raw in expected.items():(probe.output/path).write_bytes(raw)
        report.update(passed=True,originals_sha256={p:hashlib.sha256(raw).hexdigest() for p,raw in expected.items()},
            original_file_ids={p:frozen[p]['files'][0]['id'] for p in PATHS},
            actual_Markdown_to_Markdown_and_Canvas=True,actual_Canvas_to_Markdown_and_Canvas=True,
            explicit_review_cancel_and_missing_and_unsupported_observed=True,
            unconfirmed_source_draft_preserved_without_Save=True,original_state_and_receipts_unchanged=True)
    except Exception as error:
        report['failure_class']=type(error).__name__
        if current and probe.root is not None:
            try:snapshot(probe,current+'-failure');report['original_failure_observations_preserved']=True
            except Exception as observation:report['observation_failure_class']=type(observation).__name__
        raise
    finally:atomic_json(probe.output/'local-reference-proof.json',report,exclusive=True);probe.force_stop()
