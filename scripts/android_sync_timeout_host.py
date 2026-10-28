"""Closed timeout-only GUI handshake; no Native timeout or apply ACK inference.

One fixed input, original service-held response, failed UI and same-parent
release stay on one instrumentation connection. Existing hosts are unchanged.
"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from types import SimpleNamespace
import uuid

from android_release_auth_background import ReleaseAuthBackground
from android_release_observation import APP,admit_identity,exclusive
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import DRIVER,require,require_hosted_emulator
from android_sync_fixture import arm_control
from android_sync_host_touch import retain_post_input_owner
from android_sync_process_host import _owner,_identity,_query,_reads,_signal,_wait,_stream,process_lifetime
from android_sync_stop_host import OriginalHeldRevision,canonical,closed_json,record,same_fields,preinstrument_owner,_device_json

ENTRY='ReleaseSyncTimeoutRuntimeProbe'
TIMEOUT,RESUME='cycle-timeout-original','cycle-after-timeout-original'
LIMIT=1024*1024
CLIENT_MS,DRIVER_MS,HOLD_MS,RELEASE_MS=30000,36000,42000,5000
MARKER_ADMISSION_MS=5000
SUMMARY='input_finished=true failed_cycle_UI_observed={failed} network_error_UI_observed={failed} host_release_signal_observed={released} native_timeout_verified=false native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false'
FAILED=('OPENNEXUS_SYNC_TIMEOUT_FAILED_UI '+SUMMARY.format(failed='true',released='false')).encode()
RELEASED=('OPENNEXUS_SYNC_TIMEOUT_RELEASE_GUARD '+SUMMARY.format(failed='true',released='true')).encode()
READ=b'OPENNEXUS_SYNC_AFTER_TIMEOUT_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false'


def validate_parent(value,phase):
    require(phase in (TIMEOUT,RESUME) and type(value) is dict and set(value)=={
        'schema','phase','parent_intent_id','input_intent_id','native_invocation_id_observed'},'SYNC_TIMEOUT_PARENT_SCOPE')
    require(type(value['schema']) is int and value['schema']==1 and value['phase']==phase
        and value['native_invocation_id_observed'] is False and canonical(value['parent_intent_id'])
        and canonical(value['input_intent_id']) and value['parent_intent_id']!=value['input_intent_id'],'SYNC_TIMEOUT_PARENT_OWNER')
    return dict(value)


def validate_ready(value,parent,elapsed,uid):
    validate_parent(parent,parent['phase'])
    require(type(value) is dict and set(value)=={'schema','phase','action','parent_intent_id','driver_intent_id',
        'package','uid','window','original','visible','x','y','elapsed_ms'},'SYNC_TIMEOUT_READY_SCOPE')
    expected=dict(schema=1,phase=parent['phase'],action='cycle-start' if parent['phase']==TIMEOUT else 'cycle-resume',
        parent_intent_id=parent['parent_intent_id'],driver_intent_id=parent['input_intent_id'],package=APP,uid=uid)
    require(type(uid) is int and 10000<=uid<100000 and all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items()),
        'SYNC_TIMEOUT_READY_OWNER')
    require(all(type(value[k]) is int for k in ('window','x','y','elapsed_ms')) and 0<=value['window']<=65535
        and type(elapsed) is int and 0<=value['elapsed_ms']<=elapsed<=value['elapsed_ms']+2000,'SYNC_TIMEOUT_READY_CLOCK')
    for key in ('original','visible'):
        rect=value[key]
        require(type(rect) is list and len(rect)==4 and all(type(n) is int and -16384<=n<=16384 for n in rect)
            and rect[0]<=value['x']<rect[2] and rect[1]<=value['y']<rect[3],'SYNC_TIMEOUT_READY_RECT')
    a,b=value['original'],value['visible']
    require(a[0]<=b[0]<b[2]<=a[2] and a[1]<=b[1]<b[3]<=a[3]
        and value['x']==(b[0]+b[2])//2>=0 and value['y']==(b[1]+b[3])//2>=0,'SYNC_TIMEOUT_READY_CENTER')
    return dict(value)


def validate_failed(value,parent,ready,elapsed):
    validate_parent(parent,TIMEOUT)
    expected=dict(schema=1,phase=TIMEOUT,parent_intent_id=parent['parent_intent_id'],input_intent_id=parent['input_intent_id'],
        package=APP,uid=ready['uid'],window=ready['window'],elapsed_ms=value.get('elapsed_ms') if type(value) is dict else None,
        failed_cycle_UI_observed=True,network_error_UI_observed=True,native_timeout_verified=False)
    require(same_fields(value,expected) and type(value['elapsed_ms']) is int and type(elapsed) is int
        and ready['elapsed_ms']<=value['elapsed_ms']<=ready['elapsed_ms']+DRIVER_MS
        and 0<=value['elapsed_ms']<=elapsed<=value['elapsed_ms']+2000,'SYNC_TIMEOUT_FAILED_READY_OWNER_OR_CLOCK')
    return dict(value)


def _local_marker(process,path,token,deadline):
    def alive():require(process.poll() is None,'SYNC_TIMEOUT_DRIVER_EXITED_BEFORE_RELEASE')
    def marker():
        raw=_stream(path);require(raw.count(token)<=1,'SYNC_TIMEOUT_DUPLICATE_STATIC_MARKER')
        return True if token in raw else None
    return _wait(deadline,alive,marker)


def _approved_stdout(raw,phase):
    require(phase in (TIMEOUT,RESUME),'SYNC_TIMEOUT_STREAM_PHASE')
    expected=list((FAILED,RELEASED) if phase==TIMEOUT else (READ,))
    expected.extend((('OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY '+SUMMARY.format(failed='true' if phase==TIMEOUT else 'false',
        released='true' if phase==TIMEOUT else 'false')).encode(),('OPENNEXUS_RELEASE_SYNC_TIMEOUT_PHASE_OK '+phase).encode()))
    lines=[]
    for line in raw.splitlines():
        if not line:continue
        for prefix in (b'INSTRUMENTATION_STATUS: stream=',b'INSTRUMENTATION_RESULT: stream='):
            if line.startswith(prefix):line=line[len(prefix):];break
        if line in (b'INSTRUMENTATION_STATUS_CODE: 1',b'INSTRUMENTATION_CODE: -1',b'INSTRUMENTATION_CODE: 0'):continue
        lines.append(line)
    require(lines==expected,'SYNC_TIMEOUT_STREAM_NOT_CLOSED_STATIC_RESULT')


def _command(subject,private,label,args,*,timeout,empty):
    record(subject.output/(label+'-intent.json'),dict(arguments=list(args),package=APP,automatic_retry=False))
    journal=private/label;journal.mkdir(exist_ok=False)
    try:
        result=ReleaseUpgrade.original_command(SimpleNamespace(output=journal,device=subject.device),'once',args,timeout=timeout)
        require(type(result.returncode) is int and result.returncode==0 and type(result.stdout) is bytes
            and type(result.stderr) is bytes and result.stderr==b'' and len(result.stdout)<=16384
            and (result.stdout==b'' if empty else b'Error:' not in result.stdout and b'Exception' not in result.stdout),
            'SYNC_TIMEOUT_COMMAND_UNAPPROVED_RESULT')
        return result
    finally:
        path=journal/'once-result.json';value=closed_json(path.read_bytes()) if path.exists() else None
        facts=dict(returncode=None if value is None else value['returncode'],unknown_result=value is None or value.get('unknown_result',False),
            automatic_retry=False,unvalidated_streams_exported=False)
        for name in ('stdout','stderr'):
            path=journal/('once.'+name)
            if path.exists():facts[name+'_bytes']=path.stat().st_size;facts[name+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest() if path.stat().st_size<=LIMIT else None
        record(subject.output/(label+'-result.json'),facts)


def _clock_sample(subject):
    start=time.monotonic();raw=_query(subject,('cat','/proc/uptime'),128);end=time.monotonic()
    require(0<=start<=end<=start+1,'SYNC_TIMEOUT_CLOCK_QUERY_BOUND')
    match=re.fullmatch(rb'([0-9]+)\.([0-9]{1,3}) [0-9]+\.[0-9]{1,3}\n?',raw)
    require(match is not None,'SYNC_TIMEOUT_DEVICE_CLOCK_SHAPE')
    elapsed=int(match[1])*1000+int(match[2].ljust(3,b'0'))
    require(0<=elapsed<2**63,'SYNC_TIMEOUT_DEVICE_CLOCK_BOUND')
    return elapsed,start,end


def _signal_bounded(subject,process,pid,uid,lifetime,label,path,deadline):
    _owner(subject,process,pid,uid,lifetime)
    require(time.monotonic()+3<=deadline,'SYNC_TIMEOUT_SIGNAL_MARGIN_EXHAUSTED')
    _signal(_reads(subject),label,path)
    require(time.monotonic()<=deadline,'SYNC_TIMEOUT_SIGNAL_FINISHED_TOO_LATE')


def _release(subject,process,pid,uid,lifetime,held,label,deadline):
    _owner(subject,process,pid,uid,lifetime);require(time.monotonic()+3.5<=deadline,'SYNC_TIMEOUT_RELEASE_MARGIN_EXHAUSTED')
    require(held.marker is not None and not held.release_attempted and held.release_control is None,'SYNC_TIMEOUT_RELEASE_NOT_ORIGINAL')
    held.current(held.control)
    operation=str(uuid.uuid4())
    record(subject.output/(label+'-release-control-intent.json'),dict(operation_id=operation,mode='release',expected_revision=1,
        held_operation_id=held.control['operation_id'],automatic_retry=False,client_receipt_known=False))
    _owner(subject,process,pid,uid,lifetime);held.current(held.control)
    require(time.monotonic()+3.5<=deadline,'SYNC_TIMEOUT_RELEASE_INTENT_FINISHED_TOO_LATE')
    held.release_attempted=True
    record(subject.output/(label+'-release-control-attempt.json'),dict(operation_id=operation,mode='release',expected_revision=1,
        held_operation_id=held.control['operation_id'],automatic_retry=False,receipt_repair_allowed=False))
    require(time.monotonic()+3.5<=deadline,'SYNC_TIMEOUT_RELEASE_ATTEMPT_FINISHED_TOO_LATE')
    receipt=arm_control(held.owner,operation,'release',1)
    require(same_fields(receipt,dict(operation_id=operation,mode='release',expected_revision=1,result_revision=2)),
        'SYNC_TIMEOUT_RELEASE_RECEIPT_CHANGED')
    record(subject.output/(label+'-release-control-receipt.json'),receipt);held.release_control=receipt
    require(time.monotonic()+3<=deadline,'SYNC_TIMEOUT_RELEASE_CAS_FINISHED_TOO_LATE')


def run_hosted_timeout_cycle(subject,phase,label,*,held=None):
    require(subject.driver_entry==ENTRY and phase in (TIMEOUT,RESUME) and type(label) is str
        and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',label)
        and (type(held) is OriginalHeldRevision and held.owner==subject.owner if phase==TIMEOUT else held is None),
        'SYNC_TIMEOUT_HOST_PHASE_OR_CONTROL')
    require_hosted_emulator(subject.args.serial);admit_identity(subject.identity,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT'])
    require(all(isinstance(v,Path) and v.is_absolute() and v.is_dir() and v.resolve()==v and all(not x.is_symlink() for x in (v,*v.parents))
        for v in (subject.output,subject.tools)) and not subject.tools.is_relative_to(subject.output)
        and not subject.output.is_relative_to(subject.tools),'SYNC_TIMEOUT_PRIVATE_OUTPUT_ALIAS')
    require(type(subject.activity) is str and re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.$]+',subject.activity),'SYNC_TIMEOUT_ACTIVITY_SCOPE')
    if phase==TIMEOUT:require(held.marker is None and not held.release_attempted and held.read_held() is None,'SYNC_TIMEOUT_HOLD_ALREADY_USED')
    record(subject.output/(label+'-host-phase-intent.json'),dict(phase=phase,entry=ENTRY,automatic_retry=False,native_timeout_verified=False))
    streams=subject.tools/'timeout-streams';require(not streams.is_symlink(),'SYNC_TIMEOUT_PRIVATE_STREAM_ALIAS');streams.mkdir(exist_ok=True)
    private=streams/label;private.mkdir(exist_ok=False);out_path,err_path=private/'stdout',private/'stderr'
    stage='initial-owned-preparation'
    try:
        subject.stop();stage='original-activity-launch'
        _command(subject,private,label+'-launch',('shell','-T',shlex.join(('am','start','-W','-n',subject.activity))),timeout=30,empty=False)
        stage='original-PID-and-package-UID';pid,uid=preinstrument_owner(subject,label)
        stage='original-process-lifetime';lifetime=process_lifetime(subject,pid,uid)
        record(subject.output/(label+'-original-lifetime.json'),lifetime)
    except Exception as error:
        record(subject.output/(label+'-preparation-failure.json'),dict(stage=stage,error_type=type(error).__name__,instrumentation_not_started=True,automatic_retry=False))
        raise
    arguments=('shell','-T',shlex.join(('am','instrument','-w','-e','phase',phase,DRIVER+'/.'+ENTRY)))
    record(subject.output/(label+'-instrument-intent.json'),dict(arguments=list(arguments),automatic_retry=False))
    complete=False;process=None;stage='launch-instrumentation'
    with out_path.open('xb') as out,err_path.open('xb') as err:
        try:
            process=subprocess.Popen([str(subject.adb),'-s',subject.args.serial,*arguments],stdout=out,stderr=err)
            owner=lambda:_owner(subject,process,pid,uid,lifetime)
            stage='original-parent'
            parent=validate_parent(_wait(time.monotonic()+25,owner,lambda:_device_json(_reads(subject),'no_backup/owned-sync-timeout-'+phase+'-intent.json')),phase)
            record(subject.output/(label+'-original-parent.json'),parent)
            stage='original-ready';action='cycle-start' if phase==TIMEOUT else 'cycle-resume'
            ready=_wait(time.monotonic()+10,owner,lambda:_device_json(_reads(subject),'no_backup/owned-sync-timeout-'+phase+'-'+action+'-ready.json'))
            elapsed,_,_= _clock_sample(subject);ready=validate_ready(ready,parent,elapsed,uid)
            record(subject.output/(label+'-ready.json'),ready);owner();elapsed,_,_=_clock_sample(subject);validate_ready(ready,parent,elapsed,uid)
            record(subject.output/(label+'-input-owner.json'),dict(original_lifetime=lifetime,same_original_foreground_PID_UID_boot=True,
                original_instrumentation_alive=True,ready_age_ms=elapsed-ready['elapsed_ms'],ready_age_bound_ms=2000,native_cycle_acknowledged=False))
            preinput_elapsed=elapsed;start=time.monotonic();deadline=start+HOLD_MS/1000
            record(subject.output/(label+'-original-window.json'),dict(preinput_monotonic_ms=int(start*1000),hold_release_deadline_monotonic_ms=int(deadline*1000),
                preinput_elapsed_ms=elapsed,original_ready_elapsed_ms=ready['elapsed_ms'],host_release_bound_ms=HOLD_MS,driver_failure_bound_ms=DRIVER_MS,
                product_client_bound_ms=CLIENT_MS,proxy_hold_bound_ms=45000,clock_reset_on_marker=False,native_timeout_verified=False))
            stage='original-input'
            _command(subject,private,label+'-host-touch',('shell','-T',shlex.join(('input','touchscreen','tap',str(ready['x']),str(ready['y'])))),timeout=5,empty=True)
            retain_post_input_owner(_reads(subject),process,pid,label)
            if phase==TIMEOUT:
                stage='original-held-service-marker';marker=_wait(min(time.monotonic()+5,deadline),owner,held.read_held)
                elapsed,_,_=_clock_sample(subject)
                require(elapsed-ready['elapsed_ms']<=MARKER_ADMISSION_MS,'SYNC_TIMEOUT_NATIVE_WAIT_MARGIN_EXHAUSTED')
                record(subject.output/(label+'-held-service-marker.json'),marker)
            stage='original-input-finish'
            _signal_bounded(subject,process,pid,uid,lifetime,label+'-input-finish','no_backup/owned-sync-timeout-touch-'+parent['input_intent_id']+'.finish',deadline)
            if phase==TIMEOUT:
                stage='original-failed-ready'
                failed=_wait(min(start+38,deadline),owner,lambda:_device_json(_reads(subject),'no_backup/owned-sync-timeout-'+phase+'-failed-ready.json'))
                elapsed,sample_start,sample_end=_clock_sample(subject);failed=validate_failed(failed,parent,ready,elapsed)
                require(failed['elapsed_ms']>=preinput_elapsed+CLIENT_MS and sample_start-start>=CLIENT_MS/1000,
                    'SYNC_TIMEOUT_FAILURE_BEFORE_CLIENT_BOUND')
                record(subject.output/(label+'-failed-ready.json'),failed)
                signal_deadline=min(deadline,sample_start+(failed['elapsed_ms']+RELEASE_MS-elapsed)/1000)
                record(subject.output/(label+'-failure-clock.json'),dict(sample_start_monotonic_ms=int(sample_start*1000),sample_end_monotonic_ms=int(sample_end*1000),
                    sampled_elapsed_ms=elapsed,failed_elapsed_ms=failed['elapsed_ms'],signal_deadline_monotonic_ms=int(signal_deadline*1000),
                    original_preinput_monotonic_ms=int(start*1000),native_timeout_verified=False))
                stage='original-failed-static-marker';_local_marker(process,out_path,FAILED,min(time.monotonic()+.5,signal_deadline))
                stage='release-original-response';require(held.read_held()==marker,'SYNC_TIMEOUT_HELD_MARKER_CHANGED')
                _release(subject,process,pid,uid,lifetime,held,label,signal_deadline)
                def alive():require(process.poll() is None,'SYNC_TIMEOUT_DRIVER_EXITED_BEFORE_RELEASE_SIGNAL')
                # This is a local read of the original namespace/receipt. Full
                # owner guards surround CAS and precede the next actual signal.
                released=_wait(min(time.monotonic()+.5,signal_deadline-3),alive,held.read_released)
                record(subject.output/(label+'-original-response-release.json'),released)
                stage='same-parent-release-signal'
                _signal_bounded(subject,process,pid,uid,lifetime,label+'-released','no_backup/owned-sync-timeout-released-'+parent['parent_intent_id']+'.finish',signal_deadline)
            stage='final-original-driver-result';final_deadline=time.monotonic()+(5 if phase==TIMEOUT else 30);last=time.monotonic()
            while process.poll() is None:
                now=time.monotonic();require(last<=now<=final_deadline,'SYNC_TIMEOUT_FINAL_CLOCK_OR_TIMEOUT');last=now
                owner();_stream(out_path);require(last<=time.monotonic()<=final_deadline,'SYNC_TIMEOUT_FINAL_OBSERVATION_TOO_LATE');time.sleep(.05)
            require(last<=time.monotonic()<=final_deadline and process.returncode==0,'SYNC_TIMEOUT_DRIVER_NOT_SUCCESS')
            stdout=_stream(out_path);_approved_stdout(stdout,phase);require(err_path.stat().st_size==0,'SYNC_TIMEOUT_PRIVATE_STDERR_UNAPPROVED')
            require(_identity(subject)==subject.identity and _query(subject,('id','-u'),16)==b'0\n'
                and _query(subject,('pidof',APP),64).strip()==pid and ReleaseAuthBackground.app_is_resumed(_reads(subject))
                and process_lifetime(subject,pid,uid)==lifetime,'SYNC_TIMEOUT_FINAL_ORIGINAL_OWNER_CHANGED')
            require(time.monotonic()<=final_deadline,'SYNC_TIMEOUT_FINAL_OWNER_TOO_LATE')
            exclusive(subject.output/(label+'-instrument.stdout'),stdout);complete=True
            subject.phases.append(dict(phase=phase,label=label,pid=int(pid),passed=True,original_parent_intent_id=parent['parent_intent_id'],
                original_input_intent_id=parent['input_intent_id'],same_live_instrumentation_through_original_release=phase==TIMEOUT,
                failed_UI_observed_while_original_response_held=phase==TIMEOUT,native_timeout_verified=False,native_cycle_acknowledged=False,client_receipt_known=False))
        except Exception as error:
            record(subject.output/(label+'-host-failure.json'),dict(stage=stage,error_type=type(error).__name__,automatic_retry=False,
                whole_timeout_component_passed=False,native_timeout_verified=False));raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for path,name in ((out_path,'stdout'),(err_path,'stderr')):
                size=path.stat().st_size
                record(subject.output/(label+'-private-instrument-'+name+'-metadata.json'),dict(bytes=size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest() if size<=LIMIT else None,unvalidated_stream_exported=False))
            record(subject.output/(label+'-instrument-result.json'),dict(returncode=None if process is None else process.returncode,
                unknown_result=not complete,automatic_retry=False,early_cleanup_not_success=not complete,no_final_product_force_stop_or_reopen=True))
