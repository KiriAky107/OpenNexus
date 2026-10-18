"""Remaining Canvas view gestures in a new owned scene, with exact raw guards.

The synthetic graph starts at the geometry already accepted in the prior move
and resize case. Those original saves and completed graph actions are not run.
"""
import hashlib,json,os,re,shlex
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,DRIVER,require,require_hosted_emulator
from android_canvas_graph_contract import graph,encoded
from android_canvas_graph_probe import saved_original
from android_canvas_motion_probe import FIXTURE as SOURCE_FIXTURE,readonly,snapshot,actual_input_result
from android_canvas_view_geometry import PHASES,effect

PATH='view-original.canvas'
STEPS=tuple('view-'+name for name in ('seed','open','pan','pinch','cancel','takeover','background','cold','inspector'))
value=graph(SOURCE_FIXTURE);value['nodes'][0].update(x=80,y=75,width=200,height=175)
FIXTURE=encoded(value).replace(b'\n',b'\r\n');del value
def digest(raw):return hashlib.sha256(raw).hexdigest()


def original_touch_records(probe,phase,check=True):
    require(phase in PHASES,'VIEW_TOUCH_RECORD_SCOPE');result=[]
    for kind in ('intent','result'):
        name=phase+'-touch-'+kind+'.json'
        raw=probe.device('exec-out','run-as',DRIVER,'cat','files/'+name,check=False)
        if raw.returncode or not raw.stdout:
            require(not check,'VIEW_ORIGINAL_TOUCH_RECORD_MISSING');return None
        require(len(raw.stdout)<=16384,'VIEW_ORIGINAL_TOUCH_RECORD_BOUND')
        target=probe.output/name
        if target.exists():require(target.read_bytes()==raw.stdout,'VIEW_ORIGINAL_TOUCH_RECORD_CHANGED')
        else:
            with target.open('xb') as stream:stream.write(raw.stdout);stream.flush();os.fsync(stream.fileno())
        result.append(json.loads(raw.stdout))
    intent,observed=result
    dispatch=json.loads((probe.output/(phase+'-observation-intent.json')).read_bytes())['dispatch_id']
    require(intent['dispatch_id']==observed['dispatch_id']==dispatch,'VIEW_TOUCH_OBSERVATION_IDENTITY')
    native_input=actual_input_result(intent,observed,phase)
    def read(stage,kind):return json.loads((probe.output/(phase+'-'+stage+'-'+kind+'-view.json')).read_bytes())
    measured=effect(read('before','renderer'),read('after','renderer'),read('before','native'),read('after','native'),
        phase,intent['plan'],intent.get('secondary'))
    return dict(actual_native_input=native_input,original_readonly_geometry=measured)


def run_canvas_view(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='canvas-view','VIEW_EXPLICIT_SCOPE')
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],executed_steps=[],touches=[],saves=[],
        synthetic_original_fixture_not_GUI_additions=True,completed_move_resize_or_other_graph_Saves_replayed=False,
        geometry_alone_does_not_prove_input_or_raw_preservation=True,
        physical_properties_upgrade_or_whole_A3C_complete=False)
    current=None
    def phase(name):
        nonlocal current
        require(name==STEPS[len(report['executed_steps'])],'VIEW_ORIGINAL_PHASE_ORDER');current=name
        atomic_json(probe.output/(name+'-original-intent.json'),dict(scope='canvas-view',phase=name,path=PATH,
            tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,
            previous_observed_sha256=report.get('last_observed_sha256'),automatic_retry=False),exclusive=True)
        old=probe.shell('pidof',APP,check=False).stdout.strip()
        probe.phase(name,continue_process=name not in ('view-seed','view-open','view-cold'),keep_running=True)
        new=probe.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',new),'VIEW_ORIGINAL_PID')
        if name not in ('view-seed','view-open','view-cold'):require(new==old,'VIEW_ORIGINAL_PROCESS_CHANGED')
        elif name!='view-seed':require(old!=new,'VIEW_NOT_NEW_COLD_PROCESS')
        if name in PHASES:report['touches'].append(original_touch_records(probe,name))
        native=snapshot(probe,name);raw=probe.original(PATH);report['last_observed_sha256']=digest(raw)
        report['executed_steps'].append(name)
        return native,raw,new
    try:
        probe.enable_test_ime()
        seed,raw,_=phase('view-seed');require(graph(raw)=={'nodes':[],'edges':[]},'VIEW_EMPTY_GUI_SEED')
        report['saves'].append(saved_original(None,seed,raw,path=PATH))
        atomic_json(probe.output/'view-fixture-original-intent.json',dict(path=PATH,before_sha256=digest(raw),
            declared_synthetic_sha256=digest(FIXTURE),new_owned_file_only=True,
            not_GUI_additions_or_accepted_move_resize_Save_replay=True,automatic_retry=False),exclusive=True)
        target=probe.root+'vault/'+PATH;require(probe.private(target).stdout==raw,'VIEW_SEED_CHANGED_BEFORE_DECLARED_SETUP')
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(target))),input=FIXTURE)
        require(probe.private(target).stdout==FIXTURE,'VIEW_DECLARED_ORIGINAL_BYTES')
        report['last_observed_sha256']=digest(FIXTURE)
        before,raw,opened_pid=phase('view-open');require(raw==FIXTURE,'VIEW_OPEN_CHANGED_ORIGINAL')
        with (probe.output/'view-original.canvas').open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        cold_pid=None
        for name in STEPS[2:]:
            after,new,pid=phase(name);readonly(before,after,raw,new,cold=name=='view-cold');before=after
            if name=='view-cold':cold_pid=pid
        report.update(passed=True,original_file_id=before['files'][0]['id'],fixture_open_PID=int(opened_pid),cold_PID=int(cold_pid),
            final_sha256=digest(raw),only_one_necessary_seed_Save=len(report['saves'])==1,
            actual_original_native_pointer_streams_and_readonly_renderer_effect=True,
            exact_original_raw_drafts_CAS_receipts_and_outbox_unchanged=True)
    except Exception as error:
        report.update(failure_class=type(error).__name__,phase=current)
        if current in PHASES:
            try:original_touch_records(probe,current,check=False)
            except Exception as observation:report['failure_touch_observation_class']=type(observation).__name__
        if probe.root is not None and current in STEPS:
            try:snapshot(probe,current+'-failure')
            except Exception as observation:report['failure_native_observation_class']=type(observation).__name__
        raise
    finally:
        atomic_json(probe.output/'canvas-view-proof.json',report,exclusive=True);probe.force_stop()
