"""Read the fixed lifecycle candidate in both languages without editing content."""
from contextlib import closing
from pathlib import Path
import hashlib,json,os,re,shlex,sqlite3,tempfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,NODE,require,require_hosted_emulator

PATH='runtime.canvas'
STEPS=('candidate-language-en','candidate-language-zh','candidate-language-return','candidate-language-cold')
SEED_STEPS=('candidate-locale-seed','candidate-locale-open')
LOCALE_FIXTURE=(json.dumps(dict(nodes=[dict(id='d85e6507-7b5f-47e4-ae78-dfdd4e32d001',type='text',
    x=0,y=0,width=300,height=140,text=NODE,vendor={'original':['中文',True,None]})],
    edges=[],**{'vendor-root':{'original':'readonly locale 😀'}}),ensure_ascii=False,indent=2)+'\n').replace('\n','\r\n').encode()

def snapshot(probe,label):
    allowed=('candidate-before',*STEPS,*(SEED_STEPS if probe.args.scope=='candidate-languages' else ()))
    require(probe.args.scope in ('lifecycle','lifecycle-remaining','lifecycle-editor-remaining','candidate-languages') and label.removesuffix('-failure') in allowed,
        'CANDIDATE_LOCALE_SNAPSHOT_SCOPE')
    value={}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content'),
        ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        value[key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows(
            'SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(PATH,))]
    folder=Path(tempfile.mkdtemp(prefix='owned-candidate-locale-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        raw=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if raw.returncode==0:Path(str(database)+suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'CANDIDATE_NATIVE_INTEGRITY')
        columns='id,path,hash,revision,deleted'
        value['files']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM files WHERE path=?',(PATH,))]
        require(len(value['files'])==1 and value['files'][0]['deleted']==0,'CANDIDATE_SINGLE_OWN_FILE')
        columns='operation_id,file_id,revision,path,hash,operation,state'
        value['outbox']=[dict(zip(columns.split(','),row)) for row in db.execute(
            'SELECT '+columns+' FROM outbox WHERE file_id=? ORDER BY operation_id',(value['files'][0]['id'],))]
        ids=sorted({x['operation_id'] for x in value['outbox']+value['saves']})
        require(1<=len(ids)<=64,'CANDIDATE_OPERATION_BOUND')
        columns='operation_id,fingerprint,state,result'
        value['operations']=[dict(zip(columns.split(','),row)) for row in db.execute(
            'SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)]
    atomic_json(probe.output/(label+'-native.json'),value,exclusive=True);return value

def verify_candidate(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope in ('lifecycle','lifecycle-remaining','lifecycle-editor-remaining','candidate-languages'),'CANDIDATE_LIFECYCLE_ONLY')
    remaining=probe.args.scope in ('lifecycle-remaining','lifecycle-editor-remaining','candidate-languages');insets=None
    if remaining:
        require(probe.keystore_test_result is None and not (probe.output/'window-insets-device-proof.json').exists(),
            'CANDIDATE_REMAINING_MUST_NOT_REPLAY_NATIVE_SUITES')
    else:
        require(probe.keystore_test_result and probe.keystore_test_result.get('tests')==5
            and probe.keystore_test_result.get('Rust_worker_verified') is True,'CANDIDATE_FIVE_NATIVE_TESTS')
        insets=json.loads((probe.output/'window-insets-device-proof.json').read_bytes())
        require(insets.get('passed') is True and insets.get('tests')==3
            and insets.get('actual_keyboard_visibility_verified') is False,'CANDIDATE_THREE_NATIVE_INSET_TESTS')
    description='Readonly bilingual fixed-candidate properties' if probe.args.scope=='candidate-languages' else 'Remaining fixed-candidate lifecycle and readonly bilingual properties' if remaining else 'Necessary fixed-candidate lifecycle Native suite and readonly bilingual properties'
    report=dict(passed=False,scope=description,
        tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
        executed_steps=[],content_input=0,Save_actions=0,physical_phone_verified=False,
        native_suites_executed_here=not remaining,prior_native_suites_not_impersonated=remaining)
    current='candidate-before'
    try:
        raw=probe.original(PATH);document=json.loads(raw)
        require(len(document['nodes'])==1 and document['nodes'][0].get('text')==NODE,'CANDIDATE_ORIGINAL_CANVAS_TEXT')
        before=snapshot(probe,current);report['original_file_id']=before['files'][0]['id']
        report['original_raw_sha256']=hashlib.sha256(raw).hexdigest()
        require(before['files'][0]['hash']==report['original_raw_sha256'],'CANDIDATE_NATIVE_HASH_NOT_ORIGINAL_BYTES')
        for step in STEPS:
            current=step
            before_pid=probe.shell('pidof',APP).stdout.strip() if step==STEPS[-1] else None
            probe.phase(step,continue_process=step not in (STEPS[0],STEPS[-1]),keep_running=True)
            after=snapshot(probe,step);require(after==before,'CANDIDATE_LANGUAGE_CHANGED_NATIVE_CONTENT_OR_RECEIPT')
            require(probe.original(PATH)==raw,'CANDIDATE_LANGUAGE_CHANGED_ORIGINAL_BYTES')
            log=(probe.output/(step+'.log')).read_text('utf-8')
            require(log.count('CANDIDATE_LOCALE_OK '+step+' content_input=0 save=0')==1,'CANDIDATE_NATIVE_LOCALE_NOT_CONFIRMED')
            require(log.count('CANDIDATE_LOCALE_READ '+step+' ')==7,'CANDIDATE_SEVEN_LOCALIZED_CONTROLS')
            if step==STEPS[-1]:
                after_pid=probe.shell('pidof',APP).stdout.strip()
                require(re.fullmatch(rb'[1-9][0-9]{0,9}',before_pid) and re.fullmatch(rb'[1-9][0-9]{0,9}',after_pid)
                    and before_pid!=after_pid,'CANDIDATE_COLD_PROCESS_NOT_NEW')
                report['cold_PIDs']=[int(before_pid),int(after_pid)]
            report['executed_steps'].append(step)
        report.update(passed=True,actual_Native_controls_per_language=7,actual_new_process_cold=True,
            Native_tests=probe.keystore_test_result,native_insets=insets,original_native_state_and_bytes_unchanged=True)
    except Exception as error:
        report['failure_class']=type(error).__name__
        try:snapshot(probe,current+'-failure');probe.original(PATH);report['failure_original_observed']=True
        except Exception as observation:report['failure_observation_class']=type(observation).__name__
        raise
    finally:atomic_json(probe.output/'candidate-readiness-proof.json',report,exclusive=True);probe.force_stop()

def run_languages(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='candidate-languages','CANDIDATE_LANGUAGE_EXPLICIT_SCOPE')
    require(probe.keystore_test_result is None,'CANDIDATE_LANGUAGE_NATIVE_SUITE_REPLAY')
    from android_canvas_graph_probe import saved_original
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],setup_steps=[],
        declared_synthetic_one_node_fixture_not_GUI_node_creation=True,
        prior19_content_and_auth_and_Native8_and_themes_not_replayed=True,physical_or_release_complete=False)
    try:
        probe.phase(SEED_STEPS[0],keep_running=True);seed=snapshot(probe,SEED_STEPS[0]);raw=probe.original(PATH)
        require(json.loads(raw)==dict(nodes=[],edges=[]),'CANDIDATE_LANGUAGE_EMPTY_GUI_SEED')
        report['original_setup_Save']=saved_original(None,seed,raw,path=PATH);report['setup_steps'].append(SEED_STEPS[0])
        probe.force_stop()
        atomic_json(probe.output/'candidate-locale-fixture-intent.json',dict(path=PATH,
            before_sha256=hashlib.sha256(raw).hexdigest(),after_sha256=hashlib.sha256(LOCALE_FIXTURE).hexdigest(),
            synthetic_setup_only=True,GUI_content_input=False),exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+PATH))),input=LOCALE_FIXTURE)
        require(probe.original(PATH)==LOCALE_FIXTURE,'CANDIDATE_LANGUAGE_FIXTURE_BYTES')
        probe.phase(SEED_STEPS[1]);opened=snapshot(probe,SEED_STEPS[1]);report['setup_steps'].append(SEED_STEPS[1])
        require(opened['files'][0]['id']==seed['files'][0]['id'] and probe.original(PATH)==LOCALE_FIXTURE,
            'CANDIDATE_LANGUAGE_ORIGINAL_FILE_ID_OR_BYTES')
        verify_candidate(probe)
        report.update(passed=True,original_file_id=opened['files'][0]['id'],original_raw_sha256=hashlib.sha256(LOCALE_FIXTURE).hexdigest())
    finally:atomic_json(probe.output/'candidate-languages-seed-proof.json',report,exclusive=True);probe.force_stop()
