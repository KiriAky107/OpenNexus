"""Closed process-loss lifecycle on one owned API36 app and live self instrumentation.

The single lifecycle command has its own original journal. Product UI is never
read after the driver closes observation. Unknown commands/signals/CAS cannot
re-enter, and ordinary final cleanup never repeats the tested force-stop.
"""
from copy import copy
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
from android_release_observation import APP, admit_identity, exclusive, require_stopped_package
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from android_sync_host_touch import retain_post_input_owner
from android_sync_stop_host import (OriginalHeldRevision, canonical, closed_json, product_uid,
    record, preinstrument_owner, _clock, _device_json)

ENTRY='ReleaseSyncProcessRuntimeProbe'
LOSS,RESUME='cycle-process-loss-original','cycle-after-process-loss-original'
LIMIT=1024*1024
SUMMARY='input_finished=true expected_loss_signal_observed={loss} product_UI_observation_closed={loss} host_release_signal_observed={released} process_absence_verified=false native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false'
BOUNDARY=('OPENNEXUS_SYNC_PROCESS_EXPECTED_LOSS_BOUNDARY '+SUMMARY.format(loss='true',released='false')).encode()
RELEASED=('OPENNEXUS_SYNC_PROCESS_HOST_RELEASE_SIGNAL '+SUMMARY.format(loss='true',released='true')).encode()
READ=b'OPENNEXUS_SYNC_AFTER_PROCESS_LOSS_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false'


def validate_parent(value,phase):
    require(phase in (LOSS,RESUME) and type(value) is dict and set(value)=={
        'schema','phase','parent_intent_id','input_intent_id','native_invocation_id_observed'},'SYNC_PROCESS_PARENT_SCOPE')
    require(type(value['schema']) is int and value['schema']==1 and value['phase']==phase
        and value['native_invocation_id_observed'] is False and canonical(value['parent_intent_id'])
        and canonical(value['input_intent_id']) and value['parent_intent_id']!=value['input_intent_id'],'SYNC_PROCESS_PARENT_METADATA')
    return dict(value)


def validate_ready(value,parent,elapsed,uid):
    validate_parent(parent,parent['phase'])
    require(type(value) is dict and set(value)=={'schema','phase','action','parent_intent_id','driver_intent_id',
        'package','uid','window','original','visible','x','y','elapsed_ms'},'SYNC_PROCESS_READY_SCOPE')
    expected=dict(schema=1,phase=parent['phase'],action='cycle-start' if parent['phase']==LOSS else 'cycle-resume',
        parent_intent_id=parent['parent_intent_id'],driver_intent_id=parent['input_intent_id'],package=APP,uid=uid)
    require(all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items())
        and type(uid) is int and 10000<=uid<100000,'SYNC_PROCESS_READY_OWNER')
    require(all(type(value[k]) is int for k in ('window','x','y','elapsed_ms')) and 0<=value['window']<=65535
        and type(elapsed) is int and 0<=value['elapsed_ms']<=elapsed<=value['elapsed_ms']+2000,'SYNC_PROCESS_READY_CLOCK')
    for key in ('original','visible'):
        rect=value[key]
        require(type(rect) is list and len(rect)==4 and all(type(n) is int and -16384<=n<=16384 for n in rect)
            and rect[0]<=value['x']<rect[2] and rect[1]<=value['y']<rect[3],'SYNC_PROCESS_READY_RECT')
    a,b=value['original'],value['visible']
    require(a[0]<=b[0]<b[2]<=a[2] and a[1]<=b[1]<b[3]<=a[3]
        and value['x']==(b[0]+b[2])//2>=0 and value['y']==(b[1]+b[3])//2>=0,'SYNC_PROCESS_READY_CENTER')
    return dict(value)


def validate_loss_ready(value,parent,elapsed,uid,window):
    validate_parent(parent,LOSS)
    require(type(value) is dict and set(value)=={'schema','phase','parent_intent_id','input_intent_id','package',
        'uid','window','elapsed_ms','product_UI_observation_closed','process_absence_verified'},'SYNC_PROCESS_LOSS_READY_SCOPE')
    expected=dict(schema=1,phase=LOSS,parent_intent_id=parent['parent_intent_id'],input_intent_id=parent['input_intent_id'],
        package=APP,uid=uid,window=window,product_UI_observation_closed=True,process_absence_verified=False)
    require(all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items())
        and type(value['elapsed_ms']) is int and type(elapsed) is int
        and 0<=value['elapsed_ms']<=elapsed<=value['elapsed_ms']+2000,'SYNC_PROCESS_LOSS_READY_OWNER_OR_CLOCK')
    return dict(value)


def _query(subject,args,bound=16384):
    value=subject.shell(*args,check=False,timeout=1)
    require(type(value.returncode) is int and value.returncode==0 and type(value.stdout) is bytes
        and len(value.stdout)<=bound and value.stderr==b'','SYNC_PROCESS_READ_ONLY_QUERY_UNKNOWN')
    return value.stdout


def _reads(subject):
    # The existing observer uses its own device method. Copy only the observer
    # object to bind these read-only calls to a one-second command timeout.
    # No shared observer or owning subject configuration is mutated.
    observer=copy(subject.observer)
    def device(*args,**kwargs):
        kwargs['timeout']=1;return subject.device(*args,**kwargs)
    observer.device=device
    def shell(*args,**kwargs):
        kwargs['timeout']=1;return subject.shell(*args,**kwargs)
    return SimpleNamespace(shell=shell,device=subject.device,observer=observer,identity=subject.identity,output=subject.output)


def _identity(subject):return _reads(subject).observer.observe_identity()


def _signal(subject,label,path):
    command=('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote(
        'umask 077; test ! -e '+path+' && test ! -L '+path+' && cat > '+path))
    record(subject.output/(label+'-intent.json'),dict(arguments=list(command),bytes=7,automatic_retry=False))
    try:result=subject.device(*command,input=b'finish\n',timeout=2,check=False)
    except Exception as error:
        record(subject.output/(label+'-result.json'),dict(returncode=None,unknown_result=True,error_type=type(error).__name__,
            automatic_retry=False,unvalidated_streams_exported=False))
        raise
    record(subject.output/(label+'-result.json'),dict(returncode=result.returncode,automatic_retry=False,
        stdout_bytes=len(result.stdout),stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        stderr_bytes=len(result.stderr),stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),unvalidated_streams_exported=False))
    require(type(result.returncode) is int and result.returncode==0 and result.stdout==b'' and result.stderr==b'',
        'SYNC_PROCESS_SIGNAL_UNKNOWN_OR_UNAPPROVED_STREAM')
    readback=subject.shell('run-as',DRIVER,'sh','-c','test -f '+path+' && test ! -L '+path+' && cat '+path,check=False,timeout=1)
    require(readback.returncode==0 and readback.stdout==b'finish\n' and readback.stderr==b'','SYNC_PROCESS_SIGNAL_READBACK')
    record(subject.output/(label+'-readback.json'),dict(exact_static_bytes=True,bytes=7,private_signal_bytes_exported=False))


def process_lifetime(subject,pid,uid):
    require(type(pid) is bytes and re.fullmatch(rb'[1-9][0-9]{0,9}',pid) and int(pid)<2**31
        and type(uid) is int and 10000<=uid<100000,'SYNC_PROCESS_LIFETIME_SCOPE')
    path='/proc/'+pid.decode('ascii')
    stat=_query(subject,('cat',path+'/stat'),4096)
    cmdline=_query(subject,('cat',path+'/cmdline'))
    status=_query(subject,('cat',path+'/status'))
    prefix=APP.encode()+b'\0'
    require(cmdline.startswith(prefix) and not cmdline[len(prefix):].strip(b'\0')
        and stat.startswith(pid+b' (') and b') ' in stat,'SYNC_PROCESS_LIFETIME_PACKAGE')
    fields=stat.rsplit(b') ',1)[1].split()
    require(len(fields)>=20 and fields[19].isdigit() and 0<int(fields[19])<2**64,'SYNC_PROCESS_LIFETIME_START')
    tgids=[line.split()[1:] for line in status.splitlines() if line.startswith(b'Tgid:')]
    uids=[line.split()[1:] for line in status.splitlines() if line.startswith(b'Uid:')]
    require(tgids==[[pid]] and uids==[[str(uid).encode()]*4],'SYNC_PROCESS_LIFETIME_UID')
    return dict(package=APP,pid=int(pid),uid=uid,start_ticks=int(fields[19]))


def _boot_root(subject,process):
    require(process.poll() is None and _identity(subject)==subject.identity
        and _query(subject,('id','-u'),16)==b'0\n','SYNC_PROCESS_OWNED_BOOT_ROOT_OR_DRIVER_CHANGED')


def _owner(subject,process,pid,uid,lifetime):
    _boot_root(subject,process)
    require(_query(subject,('pidof',APP),64).strip()==pid and ReleaseAuthBackground.app_is_resumed(_reads(subject))
        and product_uid(_query(subject,('pm','list','packages','-U',APP),1024))[0]==uid
        and process_lifetime(subject,pid,uid)==lifetime,'SYNC_PROCESS_ORIGINAL_FOREGROUND_LIFETIME_CHANGED')


def _wait(deadline,guard,observe):
    last=time.monotonic();require(0<=last<=deadline,'SYNC_PROCESS_WAIT_BOUND')
    while True:
        now=time.monotonic();require(last<=now<=deadline,'SYNC_PROCESS_WAIT_CLOCK_OR_TIMEOUT');last=now
        guard();value=observe()
        observed=time.monotonic();require(last<=observed<=deadline,'SYNC_PROCESS_OBSERVATION_FINISHED_TOO_LATE');last=observed
        if value is not None:
            guard();require(last<=time.monotonic()<=deadline,'SYNC_PROCESS_FINAL_GUARD_FINISHED_TOO_LATE');return value
        require(observed<deadline,'SYNC_PROCESS_WAIT_TIMEOUT');time.sleep(.05)


def _stream(path):
    require(path.stat().st_size<=LIMIT,'SYNC_PROCESS_PRIVATE_STREAM_BOUND')
    raw=path.read_bytes()
    require(b'_PHASE_FAILED' not in raw and b'INSTRUMENTATION_FAILED' not in raw,'SYNC_PROCESS_GUI_FAILED')
    return raw


def _marker(path,token):
    raw=_stream(path);require(raw.count(token)<=1,'SYNC_PROCESS_DUPLICATE_GUI_MARKER')
    return True if token in raw else None


def _approved_stdout(raw,phase):
    expected=list((BOUNDARY,RELEASED) if phase==LOSS else (READ,))
    expected.extend((('OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY '+SUMMARY.format(loss='true' if phase==LOSS else 'false',
        released='true' if phase==LOSS else 'false')).encode(),('OPENNEXUS_RELEASE_SYNC_PROCESS_PHASE_OK '+phase).encode()))
    lines=[]
    for line in raw.splitlines():
        if not line:continue
        for prefix in (b'INSTRUMENTATION_STATUS: stream=',b'INSTRUMENTATION_RESULT: stream='):
            if line.startswith(prefix):line=line[len(prefix):];break
        if line in (b'INSTRUMENTATION_STATUS_CODE: 1',b'INSTRUMENTATION_CODE: -1',b'INSTRUMENTATION_CODE: 0'):continue
        lines.append(line)
    require(lines==expected,'SYNC_PROCESS_STREAM_NOT_CLOSED_STATIC_RESULT')


def _absent(subject,process,pid,uid,*,completed_driver=False):
    if completed_driver:
        require(process.poll()==0 and _identity(subject)==subject.identity
            and _query(subject,('id','-u'),16)==b'0\n','SYNC_PROCESS_COMPLETED_DRIVER_OR_BOOT_ROOT_CHANGED')
    else:_boot_root(subject,process)
    result=subject.shell('pidof',APP,check=False,timeout=1)
    require(type(result.returncode) is int and result.stderr==b'' and len(result.stdout)<=64,'SYNC_PROCESS_ABSENCE_PID_UNKNOWN')
    if result.returncode==0:
        require(result.stdout.strip()==pid,'SYNC_PROCESS_REPLACEMENT_PID_REFUSED');return None
    require(result.returncode==1 and result.stdout==b'','SYNC_PROCESS_ABSENCE_NOT_CONFIRMED')
    raw=_query(subject,('ps','-A','-o','PID,NAME'),LIMIT)
    rows=raw.decode('utf-8','strict').splitlines()
    require(1<=len(rows)<=4097 and rows[0].split()==['PID','NAME'],'SYNC_PROCESS_ABSENCE_PS_SHAPE')
    for row in rows[1:]:
        fields=row.split()
        require(len(fields)==2 and re.fullmatch(r'[1-9][0-9]{0,9}',fields[0]) and len(fields[1])<=256,'SYNC_PROCESS_ABSENCE_PS_ROW')
        require(int(fields[0])!=int(pid) and fields[1]!=APP and not fields[1].startswith(APP+':'),
            'SYNC_PROCESS_ORIGINAL_PID_OR_PACKAGE_STILL_PRESENT')
    require(product_uid(_query(subject,('pm','list','packages','-U',APP),1024))[0]==uid,'SYNC_PROCESS_STOPPED_UID_CHANGED')
    require_stopped_package(_query(subject,('dumpsys','package',APP),LIMIT).decode('utf-8','strict'))
    return dict(package=APP,original_pid=int(pid),product_uid=uid,original_pid_absent=True,all_owned_package_processes_absent=True,
        user0_force_stopped=True,same_owned_root_boot=True,original_driver_alive=not completed_driver,
        original_driver_completed=completed_driver,raw_process_list_exported=False,native_cycle_acknowledged=False)


def _terminate_original(subject,process,pid,uid,lifetime,parent,loss_ready,held,private,label):
    command=('shell','-T',shlex.join(('am','force-stop','--user','0',APP)))
    _owner(subject,process,pid,uid,lifetime)
    validate_loss_ready(loss_ready,parent,_clock(_reads(subject)),uid,loss_ready['window'])
    intent=dict(operation_id=str(uuid.uuid4()),arguments=list(command),parent_intent_id=parent['parent_intent_id'],
        input_intent_id=parent['input_intent_id'],original_lifetime=lifetime,owned_boot=subject.identity,
        held_operation_id=held.control['operation_id'],product_UI_observation_closed=True,automatic_retry=False,
        ordinary_cleanup_is_not_tested_process_loss=True,native_invocation_id_observed=False)
    record(subject.output/(label+'-lifecycle-intent.json'),intent)
    _owner(subject,process,pid,uid,lifetime)
    final_elapsed=_clock(_reads(subject))
    validate_loss_ready(loss_ready,parent,final_elapsed,uid,loss_ready['window'])
    record(subject.output/(label+'-lifecycle-owner.json'),dict(original_lifetime=lifetime,same_original_foreground_PID_UID_boot=True,
        original_instrumentation_alive=True,ready_age_ms=final_elapsed-loss_ready['elapsed_ms'],ready_age_bound_ms=2000))
    journal=private/'lifecycle';journal.mkdir(exist_ok=False)
    recorder=SimpleNamespace(output=journal,device=subject.device)
    result=None
    try:
        # Keep the real recorder's unknown stdout/stderr private. Only an exact
        # successful, empty command result can be exported as command evidence.
        result=ReleaseUpgrade.original_command(recorder,'original-force-stop',command,timeout=2)
        require(result.stdout==b'' and result.stderr==b'','SYNC_PROCESS_LIFECYCLE_UNAPPROVED_STREAM')
    finally:
        original_result=journal/'original-force-stop-result.json'
        original=closed_json(original_result.read_bytes()) if original_result.exists() else None
        facts=dict(returncode=None if original is None else original['returncode'],
            unknown_result=original is None or original.get('unknown_result',False),automatic_retry=False,
            unvalidated_streams_exported=False,original_operation_id=intent['operation_id'])
        for name in ('stdout','stderr'):
            path=journal/('original-force-stop.'+name)
            if path.exists():raw=path.read_bytes();facts[name+'_bytes']=len(raw);facts[name+'_sha256']=hashlib.sha256(raw).hexdigest()
        record(subject.output/(label+'-lifecycle-result.json'),facts)


def run_hosted_process_cycle(subject,phase,label,*,held=None):
    require(subject.driver_entry==ENTRY and phase in (LOSS,RESUME) and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',label)
        and (type(held) is OriginalHeldRevision and held.owner==subject.owner if phase==LOSS else held is None),
        'SYNC_PROCESS_HOST_PHASE_OR_CONTROL')
    require_hosted_emulator(subject.args.serial)
    admit_identity(subject.identity,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT'])
    require(all(isinstance(v,Path) and v.is_absolute() and v.is_dir() and not v.is_symlink() and v.resolve()==v
        for v in (subject.output,subject.tools)) and not subject.tools.is_relative_to(subject.output),'SYNC_PROCESS_PRIVATE_OUTPUT_ALIAS')
    if phase==LOSS:require(held.marker is None and not held.release_attempted and held.read_held() is None,'SYNC_PROCESS_HOLD_ALREADY_USED')
    record(subject.output/(label+'-host-phase-intent.json'),dict(phase=phase,entry=ENTRY,automatic_retry=False,native_invocation_id_observed=False))
    streams=subject.tools/'process-streams';require(not streams.is_symlink(),'SYNC_PROCESS_STREAM_ALIAS');streams.mkdir(exist_ok=True)
    private=streams/label;private.mkdir(exist_ok=False)
    out_path,err_path=private/'stdout',private/'stderr'
    # Initial preparation is distinct from the later once-only lifecycle event.
    stage='initial-owned-preparation'
    try:
        subject.stop()
        stage='original-activity-launch'
        subject.original_command(label+'-launch',('shell','-T',shlex.join(('am','start','-W','-n',subject.activity))))
        stage='original-PID-and-package-UID'
        pid,uid=preinstrument_owner(subject,label)
        stage='original-process-lifetime'
        lifetime=process_lifetime(subject,pid,uid)
        record(subject.output/(label+'-original-lifetime.json'),lifetime)
    except Exception as error:
        record(subject.output/(label+'-preparation-failure.json'),dict(stage=stage,error_type=type(error).__name__,
            instrumentation_not_started=True,automatic_retry=False,whole_process_component_passed=False))
        raise
    arguments=('shell','-T',shlex.join(('am','instrument','-w','-e','phase',phase,DRIVER+'/.'+ENTRY)))
    record(subject.output/(label+'-instrument-intent.json'),dict(arguments=list(arguments),automatic_retry=False))
    complete=False;process=None;stage='launch-instrumentation';absence=None
    with out_path.open('xb') as out,err_path.open('xb') as err:
        try:
            process=subprocess.Popen([str(subject.adb),'-s',subject.args.serial,*arguments],stdout=out,stderr=err)
            owner=lambda:_owner(subject,process,pid,uid,lifetime)
            stage='original-parent'
            parent=validate_parent(_wait(time.monotonic()+25,owner,lambda:_device_json(_reads(subject),'no_backup/owned-sync-process-'+phase+'-intent.json')),phase)
            record(subject.output/(label+'-original-parent.json'),parent)
            action='cycle-start' if phase==LOSS else 'cycle-resume';stage=action+'-ready'
            value=validate_ready(_wait(time.monotonic()+10,owner,lambda:_device_json(_reads(subject),
                'no_backup/owned-sync-process-'+phase+'-'+action+'-ready.json')),parent,_clock(_reads(subject)),uid)
            record(subject.output/(label+'-ready.json'),value);owner();validate_ready(value,parent,_clock(_reads(subject)),uid)
            stage='original-input';hold_deadline=time.monotonic()+30
            subject.original_command(label+'-host-touch',('shell','-T',shlex.join(('input','touchscreen','tap',str(value['x']),str(value['y'])))),timeout=5)
            retain_post_input_owner(_reads(subject),process,pid,label)
            if phase==LOSS:
                stage='held-original-service-marker';marker=_wait(min(time.monotonic()+20,hold_deadline),owner,held.read_held)
                record(subject.output/(label+'-held-service-marker.json'),marker)
            stage='original-input-finish'
            _signal(_reads(subject),label+'-input-finish','no_backup/owned-sync-process-touch-'+parent['input_intent_id']+'.finish')
            if phase==LOSS:
                stage='expected-process-loss-signal';owner()
                _signal(_reads(subject),label+'-expect-loss','no_backup/owned-sync-process-expect-loss-'+parent['parent_intent_id']+'.finish')
                stage='original-loss-ready'
                loss_ready=validate_loss_ready(_wait(min(time.monotonic()+8,hold_deadline),owner,lambda:_device_json(_reads(subject),
                    'no_backup/owned-sync-process-'+phase+'-loss-ready.json')),parent,_clock(_reads(subject)),uid,value['window'])
                record(subject.output/(label+'-loss-ready.json'),loss_ready)
                _wait(min(time.monotonic()+.5,hold_deadline),owner,lambda:_marker(out_path,BOUNDARY))
                stage='once-original-force-stop';require(time.monotonic()<hold_deadline,'SYNC_PROCESS_HOLD_RELEASE_TOO_LATE')
                _terminate_original(subject,process,pid,uid,lifetime,parent,loss_ready,held,private,label)
                stage='original-process-absence'
                absence=_wait(min(time.monotonic()+2,hold_deadline),lambda:_boot_root(subject,process),lambda:_absent(subject,process,pid,uid))
                record(subject.output/(label+'-original-process-absence.json'),absence)
                stage='release-original-held-response';require(time.monotonic()<hold_deadline,'SYNC_PROCESS_HOLD_RELEASE_TOO_LATE')
                require(_absent(subject,process,pid,uid) is not None,'SYNC_PROCESS_ABSENCE_CHANGED_BEFORE_RELEASE')
                held.begin_release(subject.output,label)
                released=_wait(min(time.monotonic()+2,hold_deadline),lambda:_boot_root(subject,process),held.read_released)
                record(subject.output/(label+'-original-response-release.json'),released)
                require(_absent(subject,process,pid,uid) is not None,'SYNC_PROCESS_ABSENCE_CHANGED_AFTER_RELEASE')
                stage='same-parent-released-signal'
                _signal(_reads(subject),label+'-released','no_backup/owned-sync-process-released-'+parent['parent_intent_id']+'.finish')
            stage='final-original-driver-result';deadline=time.monotonic()+5;last=time.monotonic()
            while process.poll() is None:
                now=time.monotonic();require(last<=now<=deadline,'SYNC_PROCESS_FINAL_CLOCK_OR_TIMEOUT');last=now
                if phase==LOSS:require(_absent(subject,process,pid,uid) is not None,'SYNC_PROCESS_ABSENCE_CHANGED_AT_FINAL')
                else:owner()
                _stream(out_path);time.sleep(.05)
            require(last<=time.monotonic()<=deadline,'SYNC_PROCESS_DRIVER_RESULT_OBSERVED_TOO_LATE')
            stdout=_stream(out_path);require(process.returncode==0,'SYNC_PROCESS_DRIVER_RESULT_NOT_SUCCESS')
            _approved_stdout(stdout,phase)
            require(err_path.stat().st_size==0,'SYNC_PROCESS_DRIVER_STDERR_NOT_APPROVED')
            if phase==LOSS:
                final_absence=_absent(subject,process,pid,uid,completed_driver=True)
                require(final_absence is not None,'SYNC_PROCESS_FINAL_APP_NOT_ABSENT')
                record(subject.output/(label+'-final-original-process-absence.json'),final_absence)
            else:
                require(_identity(subject)==subject.identity and _query(subject,('pidof',APP),64).strip()==pid
                    and ReleaseAuthBackground.app_is_resumed(_reads(subject)) and process_lifetime(subject,pid,uid)==lifetime,
                    'SYNC_PROCESS_READ_FINAL_OWNER_CHANGED')
            exclusive(subject.output/(label+'-instrument.stdout'),stdout)
            complete=True
            subject.phases.append(dict(phase=phase,label=label,pid=int(pid),passed=True,original_parent_intent_id=parent['parent_intent_id'],
                original_input_intent_id=parent['input_intent_id'],actual_process_absence_observed=phase==LOSS,
                same_live_instrumentation_through_original_release=phase==LOSS,Native_RAM_invocation_id_observed=False,
                native_original_state_verified=False,client_receipt_known=False))
        except Exception as error:
            record(subject.output/(label+'-host-failure.json'),dict(stage=stage,error_type=type(error).__name__,automatic_retry=False,
                whole_process_component_passed=False,native_original_state_verified=False))
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for path,name in ((out_path,'stdout'),(err_path,'stderr')):
                size=path.stat().st_size
                record(subject.output/(label+'-private-instrument-'+name+'-metadata.json'),dict(bytes=size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest() if size<=LIMIT else None,
                    unvalidated_stream_exported=False))
            record(subject.output/(label+'-instrument-result.json'),dict(returncode=None if process is None else process.returncode,
                unknown_result=not complete,automatic_retry=False,early_cleanup_not_success=not complete,
                no_final_product_force_stop_or_reopen=True))
