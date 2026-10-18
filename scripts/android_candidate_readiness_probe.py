"""Read the fixed lifecycle candidate in both languages without editing content."""
from contextlib import closing
from pathlib import Path
import hashlib,json,re,sqlite3,tempfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,NODE,require,require_hosted_emulator

PATH='runtime.canvas'
STEPS=('candidate-language-en','candidate-language-zh','candidate-language-return','candidate-language-cold')

def snapshot(probe,label):
    require(probe.args.scope=='lifecycle' and label.removesuffix('-failure') in ('candidate-before',*STEPS),
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
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='lifecycle','CANDIDATE_LIFECYCLE_ONLY')
    require(probe.keystore_test_result and probe.keystore_test_result.get('tests')==5
        and probe.keystore_test_result.get('Rust_worker_verified') is True,'CANDIDATE_FIVE_NATIVE_TESTS')
    insets=json.loads((probe.output/'window-insets-device-proof.json').read_bytes())
    require(insets.get('passed') is True and insets.get('tests')==3
        and insets.get('actual_keyboard_visibility_verified') is False,'CANDIDATE_THREE_NATIVE_INSET_TESTS')
    report=dict(passed=False,scope='Necessary fixed-candidate lifecycle Native suite and readonly bilingual properties',
        tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
        executed_steps=[],content_input=0,Save_actions=0,physical_phone_verified=False)
    current='candidate-before'
    try:
        raw=probe.original(PATH);document=json.loads(raw)
        require(len(document['nodes'])==1 and document['nodes'][0].get('text')==NODE,'CANDIDATE_ORIGINAL_CANVAS_TEXT')
        before=snapshot(probe,current);report['original_file_id']=before['files'][0]['id']
        report['original_raw_sha256']=hashlib.sha256(raw).hexdigest()
        for step in STEPS:
            current=step;before_pid=probe.shell('pidof',APP).stdout.strip()
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
