"""Two original reviewed links through Android's OS resolver, never a URL fetch."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import hashlib,json,os,re,shlex,sqlite3,tempfile,time,uuid
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,DRIVER,require,require_hosted_emulator
from android_canvas_graph_probe import saved_original
from android_external_contract import MD,CANVAS,MD_PATH,CANVAS_PATH,BODY,DRAFT,STEPS,armed,canvas_fixture,validate_record
from android_external_routing import setup as setup_routing,cleanup as cleanup_routing

PATHS=(MD_PATH,CANVAS_PATH)


def originals():return {MD_PATH:BODY.encode(),CANVAS_PATH:canvas_fixture()}


def driver_form():
    return dict(schema=1,scope='external-links',source_md=MD_PATH,source_canvas=CANVAS_PATH,
        source_body=BODY,draft_body=DRAFT,markdown_uri=MD,canvas_uri=CANVAS)


def stable(value):
    require(set(value)==set(PATHS),'EXTERNAL_EXACT_STATE_PATHS');result=deepcopy(value)
    for item in result.values():
        require(set(item)=={'files','drafts','saves','outbox','operations'},'EXTERNAL_EXACT_NATIVE_SCHEMA')
        for draft in item['drafts']:draft.pop('edit_session',None)
    return result


def snapshot(probe,label):
    require(probe.args.scope=='external-links' and label.removesuffix('-failure') in STEPS,'EXTERNAL_SNAPSHOT_SCOPE')
    result={path:{} for path in PATHS}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
        ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        for path in PATHS:
            result[path][key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows(
                'SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(path,))]
    folder=Path(tempfile.mkdtemp(prefix='owned-external-native-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        raw=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if raw.returncode==0:Path(str(database)+suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'EXTERNAL_NATIVE_INTEGRITY')
        for path,item in result.items():
            columns='id,path,hash,revision,deleted'
            item['files']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM files WHERE path=?',(path,))]
            require(len(item['files'])<=1,'EXTERNAL_SINGLE_OWN_FILE');columns='operation_id,file_id,revision,path,hash,operation,state'
            item['outbox']=[dict(zip(columns.split(','),row)) for row in db.execute(
                'SELECT '+columns+' FROM outbox WHERE file_id=? ORDER BY operation_id',
                (item['files'][0]['id'] if item['files'] else '',))]
            ids=sorted({x['operation_id'] for x in item['outbox']+item['saves']});require(len(ids)<=16,'EXTERNAL_OPERATION_BOUND')
            columns='operation_id,fingerprint,state,result'
            item['operations']=[dict(zip(columns.split(','),row)) for row in db.execute(
                'SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)] if ids else []
    atomic_json(probe.output/(label+'-native.json'),result,exclusive=True);return result


def saved(value):return {path:{key:item[key] for key in ('files','saves','outbox','operations')} for path,item in value.items()}


def validate_draft(before,after):
    require(saved(before)==saved(after),'EXTERNAL_DRAFT_CHANGED_SAVED_STATE')
    require(stable(before)[CANVAS_PATH]==stable(after)[CANVAS_PATH],'EXTERNAL_DRAFT_CHANGED_CANVAS')
    rows=after[MD_PATH]['drafts'];require(len(rows)==1 and rows[0]['content']==DRAFT
        and rows[0]['generation']>rows[0]['saved_generation'],'EXTERNAL_ORIGINAL_UNCONFIRMED_DRAFT')


def validate_frozen(expected,baseline,current,raws):
    require(set(expected)==set(raws)==set(PATHS) and all(type(expected[p]) is bytes and raws[p]==expected[p] for p in PATHS),'EXTERNAL_ORIGINAL_BYTES_CHANGED')
    require(stable(baseline)==stable(current),'EXTERNAL_HANDOFF_CHANGED_NATIVE_STATE')
    for path in PATHS:
        rows=current[path]['files'];require(len(rows)==1 and rows[0]['deleted']==0
            and rows[0]['hash']==hashlib.sha256(raws[path]).hexdigest(),'EXTERNAL_NATIVE_HASH_NOT_ORIGINAL')


def transfer(probe,name,payload):
    require(probe.args.scope=='external-links','EXTERNAL_TRANSFER_SCOPE')
    require(name in ('owned-external-form.json','owned-external-markdown.armed.json','owned-external-canvas.armed.json')
        and type(payload) is bytes and 0<len(payload)<=8192,'EXTERNAL_EXACT_DRIVER_TRANSFER')
    probe.device('shell','-T',shlex.join(('run-as',DRIVER,'sh','-c',
        'umask 077; mkdir -p files && test ! -e files/'+name+' && cat > files/'+name)),input=payload)


def read_record(probe,original):
    require(probe.args.scope=='external-links','EXTERNAL_RECORD_READ_SCOPE')
    key=original['key'];require(original==armed(key,original['operation_id']),'EXTERNAL_ORIGINAL_INTENT_CHANGED')
    result=probe.shell('run-as',DRIVER,'cat','files/owned-external-'+key+'.received.json',check=False)
    if result.returncode!=0:return None
    value=validate_record(original,result.stdout)
    target=probe.output/('external-'+key+'-received.json')
    if target.exists():require(target.read_bytes()==result.stdout,'EXTERNAL_ORIGINAL_RECORD_CHANGED')
    else:
        with target.open('xb') as stream:stream.write(result.stdout);stream.flush();os.fsync(stream.fileno())
    return value


def no_records(probe):
    require(probe.args.scope=='external-links','EXTERNAL_RECORD_READ_SCOPE')
    for key in ('markdown','canvas'):
        result=probe.shell('run-as',DRIVER,'test','-e','files/owned-external-'+key+'.received.json',check=False)
        require(result.returncode==1,'EXTERNAL_INTENT_BEFORE_EXPLICIT_OPEN')


def await_draft(probe):
    require(probe.args.scope=='external-links','EXTERNAL_DRAFT_READ_SCOPE')
    until=time.monotonic()+10
    while True:
        rows=probe.database_rows('SELECT content,generation,saved_generation FROM drafts WHERE path=? ORDER BY rowid LIMIT 2',(MD_PATH,))
        require(len(rows)<=1,'EXTERNAL_AMBIGUOUS_DRAFT')
        if len(rows)==1 and rows[0][0]==DRAFT and rows[0][1]>rows[0][2]:return
        require(time.monotonic()<until,'EXTERNAL_DRAFT_NOT_DURABLE');time.sleep(.2)


def run_external(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links','EXTERNAL_EXPLICIT_SCOPE')
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],scope=probe.args.scope,
        executed_steps=[],setup_saves=[],original_external_actions={},original_external_receipts={},
        synthetic_Canvas_fixture_not_GUI_node_creation=True,old_components_not_replayed=True,
        browser_or_network_or_physical_verified=False,intended_signature_upgrade_or_release_complete=False)
    current=None;expected=originals();intents={};baseline=None
    payload=json.dumps(driver_form(),ensure_ascii=False).encode()
    def phase(name,*,cold=False):
        nonlocal current
        require(name==STEPS[len(report['executed_steps'])],'EXTERNAL_ORIGINAL_STEP_ORDER');current=name
        atomic_json(probe.output/(name+'-intent.json'),dict(step=name,run=report['run_id'],scope=probe.args.scope,
            exact_owned_paths=list(PATHS),automatic_retry=False,Save_expected=name.endswith('-seed')),exclusive=True)
        probe.phase(name,continue_process=not cold,keep_running=True)
        if name=='external-draft':await_draft(probe)
        value=snapshot(probe,name);report['executed_steps'].append(name)
        log=(probe.output/(name+'.log')).read_text('utf-8')
        require(log.count('ORIGINAL_EXTERNAL_REFERENCE '+name+' save='+str(int(name.endswith('-seed'))))==1,'EXTERNAL_PHASE_NOT_CONFIRMED')
        require(log.count('ORIGINAL_EXTERNAL_FORM_SHA256 '+hashlib.sha256(payload).hexdigest())==1,'EXTERNAL_FORM_NOT_ORIGINAL_BYTES')
        return value
    try:
        require(probe.keystore_test_result is None,'EXTERNAL_MUST_NOT_REPLAY_NATIVE_SUITES');probe.enable_test_ime()
        atomic_json(probe.output/'external-reviewed-form.json',driver_form(),exclusive=True)
        transfer(probe,'owned-external-form.json',payload)
        setup_routing(probe)  # Validate before any new product GUI seed or Save.
        for step,path in zip(STEPS[:2],PATHS):
            state=phase(step,cold=True);raw=probe.original(path)
            require(raw==expected[path] if path==MD_PATH else json.loads(raw)==dict(nodes=[],edges=[]),'EXTERNAL_GUI_SEED_BYTES')
            report['setup_saves'].append(saved_original(None,state[path],raw,path=path))
        require(len({v['operation_id'] for v in report['setup_saves']})==2,'EXTERNAL_TWO_DISTINCT_SETUP_SAVES')
        probe.force_stop();before=probe.original(CANVAS_PATH)
        atomic_json(probe.output/'external-Canvas-fixture-intent.json',dict(path=CANVAS_PATH,
            before_sha256=hashlib.sha256(before).hexdigest(),after_sha256=hashlib.sha256(expected[CANVAS_PATH]).hexdigest(),
            declared_fixture_only_not_GUI_creation=True),exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+CANVAS_PATH))),input=expected[CANVAS_PATH])
        require(probe.original(CANVAS_PATH)==expected[CANVAS_PATH],'EXTERNAL_SYNTHETIC_ORIGINAL_BYTES')
        before=phase('external-baseline',cold=True);no_records(probe)
        baseline=phase('external-draft');validate_draft(before,baseline)
        state=phase('external-cancel');no_records(probe)
        validate_frozen(expected,baseline,state,{p:probe.original(p) for p in PATHS})
        for step,key in (('external-md-open','markdown'),('external-canvas-open','canvas')):
            original=armed(key,str(uuid.uuid4()));intents[key]=original
            atomic_json(probe.output/('external-'+key+'-armed.json'),original,exclusive=True)
            report['original_external_actions'][key]=original
            transfer(probe,'owned-external-'+key+'.armed.json',json.dumps(original,ensure_ascii=False).encode())
            state=phase(step);record=read_record(probe,original);require(record is not None,'EXTERNAL_ORIGINAL_RECEIPT_MISSING')
            report['original_external_receipts'][key]=record
            validate_frozen(expected,baseline,state,{p:probe.original(p) for p in PATHS})
        pid=probe.shell('pidof',APP).stdout.strip();state=phase('external-cold',cold=True);after=probe.shell('pidof',APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}',pid) and re.fullmatch(rb'[1-9][0-9]{0,9}',after) and pid!=after,'EXTERNAL_COLD_NEW_PID')
        validate_frozen(expected,baseline,state,{p:probe.original(p) for p in PATHS})
        for key,original in intents.items():require(read_record(probe,original)==report['original_external_receipts'][key],'EXTERNAL_COLD_RECEIPT_CHANGED')
        for path,raw in expected.items():(probe.output/path).write_bytes(raw)
        report.update(passed=True,cold_PIDs=[int(pid),int(after)],originals_sha256={p:hashlib.sha256(raw).hexdigest() for p,raw in expected.items()},
            original_file_ids={p:baseline[p]['files'][0]['id'] for p in PATHS},actual_OS_VIEW_BROWSABLE_to_owned_receiver=True,
            explicit_cancel_had_no_OS_receipt=True,unsaved_draft_and_originals_and_native_receipts_unchanged=True,
            uppercase_original_reviewed_and_only_scheme_normalized_for_OS=True)
    except Exception as error:
        report['failure_class']=type(error).__name__
        # Read original receipt first even after timeout/failure; never replay Open.
        for key,original in intents.items():
            try:report['original_external_receipts'][key]=read_record(probe,original)
            except Exception as observation:report['receipt_observation_error_'+key]=type(observation).__name__
        if current and probe.root is not None:
            try:
                state=snapshot(probe,current+'-failure');report['original_failure_observations_preserved']=True
                if baseline:validate_frozen(expected,baseline,state,{p:probe.original(p) for p in PATHS});report['failure_original_state_unchanged']=True
            except Exception as observation:report['observation_failure_class']=type(observation).__name__
        raise
    finally:
        try:report['routing_cleanup']=cleanup_routing(probe)
        except Exception as error:report['routing_cleanup_error']=type(error).__name__;report['passed']=False
        atomic_json(probe.output/'external-link-proof.json',report,exclusive=True);probe.force_stop()
        require('routing_cleanup_error' not in report,'EXTERNAL_OWN_ROUTING_CLEANUP_UNCONFIRMED')
