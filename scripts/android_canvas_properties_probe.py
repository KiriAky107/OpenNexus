"""Remaining five actual Native property forms; original raw graphs and receipts."""
import hashlib,json,os,re,shlex
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,DRIVER,require,require_hosted_emulator
from android_canvas_graph_contract import graph,identical_original_bytes
from android_canvas_graph_probe import snapshot,projection,saved_original
from android_canvas_properties import PATH,driver_form,validate_transaction,selected_transactions,property_steps,initial_property_fixture

def driver_form_write_script():
    return 'umask 077; mkdir -p files && test ! -e files/owned-properties-form.json && cat > files/owned-properties-form.json'

def run_properties(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope in ('canvas-properties','canvas-properties-remaining'),'PROPERTY_EXPLICIT_SCOPE')
    remaining=probe.args.scope=='canvas-properties-remaining';transactions=selected_transactions(remaining)
    steps=property_steps(remaining);fixture=initial_property_fixture(remaining)
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],scope=probe.args.scope,
        executed_steps=[],saves=[],declared_synthetic_four_node_one_edge_fixture=True,
        accepted_old_body_geometry_and_gestures_not_replayed=True,prior_four_node_forms_excluded=remaining,
        declared_prefix_is_synthetic_setup_not_new_GUI_acceptance=remaining,physical_or_whole_A3C_or_release_complete=False)
    current=None
    def phase(name):
        nonlocal current
        require(name==steps[len(report['executed_steps'])],'PROPERTY_ORIGINAL_STEP_ORDER');current=name
        atomic_json(probe.output/(name+'-original-intent.json'),dict(run_id=os.environ['GITHUB_RUN_ID'],
            tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,scope=probe.args.scope,step=name,
            owned_path=PATH,automatic_retry=False,native_operation_ID_assigned_by_driver=False),exclusive=True)
        probe.phase(name,continue_process=name not in ('properties-seed','properties-open','properties-cold'),keep_running=True)
        value=snapshot(probe,name,path=PATH);raw=probe.original(PATH);graph(raw)
        report['executed_steps'].append(name);return value,raw
    try:
        probe.enable_test_ime()
        before,raw=phase('properties-seed');require(graph(raw)==dict(nodes=[],edges=[]),'PROPERTY_EMPTY_GUI_SEED')
        report['saves'].append(saved_original(None,before,raw,path=PATH));identity=before['files'][0]['id']
        first_pid=probe.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',first_pid),'PROPERTY_ORIGINAL_PID')
        atomic_json(probe.output/'properties-declared-fixture-intent.json',dict(before_sha256=hashlib.sha256(raw).hexdigest(),
            after_sha256=hashlib.sha256(fixture).hexdigest(),owned_path=PATH,
            synthetic_setup_only_not_GUI_creation_or_unknown_user_file=True),exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+PATH))),input=fixture)
        require(probe.original(PATH)==fixture,'PROPERTY_ORIGINAL_FIXTURE_BYTES')
        forms={item['phase']:driver_form(item['phase']) for item in transactions}
        payload=json.dumps(forms,ensure_ascii=False).encode();require(len(payload)<=8192,'PROPERTY_BOUNDED_FORMS')
        atomic_json(probe.output/'properties-reviewed-forms.json',forms,exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',DRIVER,'sh','-c',
            driver_form_write_script())),input=payload)
        before,raw=phase('properties-open');identical_original_bytes(fixture,raw)
        opened_pid=probe.shell('pidof',APP).stdout.strip()
        require(opened_pid!=first_pid and before['files'][0]['id']==identity,'PROPERTY_NEW_PROCESS_OR_ORIGINAL_FILE_CHANGED')
        for item in transactions:
            after,updated=phase(item['phase']);validate_transaction(raw,updated,item['phase'])
            report['saves'].append(saved_original(before,after,updated,path=PATH))
            log=(probe.output/(item['phase']+'.log')).read_text('utf-8')
            require(log.count('ORIGINAL_PROPERTY_FORM '+item['phase']+' fields='+str(len(item['fields']))+' save=1')==1,'PROPERTY_ONE_ORIGINAL_FORM_SAVE')
            for key in item['fields']:
                require(log.count('ORIGINAL_PROPERTY_FIELD '+item['phase']+' '+key+' input=1')==1,'PROPERTY_ONE_ORIGINAL_FIELD_INPUT')
            before,raw=after,updated
        before_cold_pid=probe.shell('pidof',APP).stdout.strip();cold,cold_raw=phase('properties-cold')
        require(probe.shell('pidof',APP).stdout.strip()!=before_cold_pid,'PROPERTY_COLD_NEW_PID_NOT_OBSERVED')
        identical_original_bytes(raw,cold_raw);require(projection(cold)==projection(before),'PROPERTY_COLD_REPLAYED_OR_CHANGED_RECEIPT')
        require(len(report['saves'])==len(transactions)+1 and len({v['operation_id'] for v in report['saves']})==len(transactions)+1,'PROPERTY_ORIGINAL_SAVE_IDS_NOT_DISTINCT')
        require(all(v['file_id']==identity for v in report['saves']),'PROPERTY_ORIGINAL_FILE_ID_CHANGED')
        report.update(passed=True,actual_remaining_forms=len(transactions),actual_remaining_fields=sum(len(v['fields']) for v in transactions),original_file_id=identity,
            final_raw_sha256=hashlib.sha256(raw).hexdigest(),immutable_reviewed_forms_sha256=hashlib.sha256(payload).hexdigest(),
            original_cold_PID=int(before_cold_pid),new_cold_PID=int(probe.shell('pidof',APP).stdout.strip()))
    except Exception as error:
        report['failure_class']=type(error).__name__
        if current and probe.root is not None:
            try:snapshot(probe,current+'-failure',path=PATH);probe.original(PATH);report['original_failure_observations_preserved']=True
            except Exception as observation:report['observation_failure_class']=type(observation).__name__
        raise
    finally:
        atomic_json(probe.output/'canvas-properties-proof.json',report,exclusive=True);probe.force_stop()
