"""Readonly timeout originals and failure policy with no implicit product stop.

This module is used only by the dedicated transport-timeout orchestrator. The normal
auth CLI/failure hooks must not run after an unknown lifecycle command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

from android_release_observation import APP, DATABASES, StoppedReleaseObserver, exclusive
from android_release_sync_timeout_snapshot import NOTE, STAGES, project_timeout
from android_runtime_probe import require
from android_sync_stop_host import closed_json, record, same_fields

LIMIT = 32 * 1024**2
STEPS = frozenset(('prepare','original-note-Save','original-login','independent-setup-and-association',
    'original-hold-control','original-timeout-GUI','timed-out-original-projection','timed-out-new-PID-read',
    'timed-out-snapshot-stop','reconciled-snapshot-stop',
    'explicit-original-changes-reconcile','final-new-PID-cold-read'))
ERRORS = frozenset(('RuntimeError','TimeoutError','FileExistsError','FileNotFoundError','PermissionError',
    'ValueError','TypeError','KeyError','OSError','AssertionError','JSONDecodeError','UnicodeDecodeError'))


def error_kind(error):
    name = type(error).__name__
    return name if name in ERRORS else 'Exception'


def exact_directory(path):
    require(isinstance(path, Path) and path.is_absolute() and path.resolve() == path
            and path.is_dir() and all(not p.is_symlink() for p in (path,*path.parents)),
            'SYNC_TIMEOUT_LIFECYCLE_CANONICAL_DIRECTORY')
    return path


def publish_original(raw, output, identity, *, include_note):
    """Export only the complete approved snapshot, never observer raw streams."""
    exact_directory(raw)
    expected = {db+s for db in DATABASES for s in ('','-wal','-shm')}
    if include_note: expected.add('vault/'+NOTE)
    manifest_path = raw/'snapshot.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink() and 0 < manifest_path.stat().st_size <= 16384,
            'SYNC_TIMEOUT_ORIGINAL_MANIFEST_BOUND')
    encoded = manifest_path.read_bytes(); manifest = closed_json(encoded)
    require(type(manifest) is dict and set(manifest) == {'package','identity','files','app_stopped_before_and_after',
        'credential_store_copied','SQL_executed_on_device','normal_phone_permissions_verified'}
        and same_fields({k:v for k,v in manifest.items() if k != 'files'},dict(package=APP,identity=identity,
            app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False,
            normal_phone_permissions_verified=False)) and type(manifest['files']) is dict and set(manifest['files']) == expected,
            'SYNC_TIMEOUT_ORIGINAL_SCOPE_OR_OWNER')
    admitted = {'snapshot.json'}
    for name,item in manifest['files'].items():
        path = raw/name
        require(not path.is_symlink(), 'SYNC_TIMEOUT_ORIGINAL_LINK')
        if item is None:
            require(name.endswith(('-wal','-shm')) and not path.exists(), 'SYNC_TIMEOUT_ORIGINAL_ABSENT_CHANGED')
            continue
        require(type(item) is dict and set(item) == {'bytes','sha256'} and type(item['bytes']) is int
            and 0 <= item['bytes'] <= LIMIT and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}',item['sha256'])
            and path.is_file() and path.stat().st_size == item['bytes'] and path.resolve() == path,
            'SYNC_TIMEOUT_ORIGINAL_FILE_BOUND')
        with path.open('rb') as stream: sha = hashlib.file_digest(stream,'sha256').hexdigest()
        require(sha == item['sha256'], 'SYNC_TIMEOUT_ORIGINAL_HASH')
        admitted.add(name)
    entries = list(raw.rglob('*'))
    require(all(not p.is_symlink() and ((p.is_file() and p.relative_to(raw).as_posix() in admitted)
                or (p.is_dir() and p.relative_to(raw).as_posix() in {'vault','vault/.ainote'}))
                for p in entries) and {p.relative_to(raw).as_posix() for p in entries if p.is_file()} == admitted,
            'SYNC_TIMEOUT_ORIGINAL_UNAPPROVED_ENTRY')
    require(output.is_absolute() and output.resolve() == output and not output.exists()
            and all(not p.is_symlink() for p in (output,*output.parents))
            and not output.is_relative_to(raw) and not raw.is_relative_to(output), 'SYNC_TIMEOUT_NEW_PUBLIC_ORIGINAL')
    output.mkdir(parents=True)
    for name in sorted(admitted):
        target = output/name; target.parent.mkdir(parents=True,exist_ok=True)
        # The retained private original is bounded and verified before export.
        data = (raw/name).read_bytes()
        if name == 'snapshot.json': require(data == encoded,'SYNC_TIMEOUT_MANIFEST_CHANGED')
        else: require(len(data) == manifest['files'][name]['bytes'] and hashlib.sha256(data).hexdigest() == manifest['files'][name]['sha256'],
                      'SYNC_TIMEOUT_ORIGINAL_CHANGED_DURING_EXPORT')
        exclusive(target,data)
    require(manifest_path.read_bytes() == encoded, 'SYNC_TIMEOUT_MANIFEST_CHANGED_AFTER_EXPORT')
    for name,item in manifest['files'].items():
        original = raw/name
        if item is None:
            require(not original.exists() and not original.is_symlink(), 'SYNC_TIMEOUT_ORIGINAL_ABSENT_CHANGED_AFTER_EXPORT')
        else:
            require(original.is_file() and not original.is_symlink() and original.stat().st_size == item['bytes'],
                    'SYNC_TIMEOUT_ORIGINAL_CHANGED_AFTER_EXPORT')
            with original.open('rb') as stream: sha = hashlib.file_digest(stream,'sha256').hexdigest()
            require(sha == item['sha256'], 'SYNC_TIMEOUT_ORIGINAL_CHANGED_AFTER_EXPORT')
    return manifest


def collect_original(subject, label, *, stage=None):
    """No stop, status/open, GUI, root request or credential copy is issued here."""
    require(label in STAGES or label == 'failure', 'SYNC_TIMEOUT_COLLECTOR_LABEL')
    require((label == 'failure' and stage is None) or (label == stage and stage in STAGES), 'SYNC_TIMEOUT_COLLECTOR_STAGE')
    tools = exact_directory(subject.tools.absolute()); output = exact_directory(subject.output.absolute())
    require(not tools.is_relative_to(output) and not output.is_relative_to(tools), 'SYNC_TIMEOUT_PRIVATE_OBSERVER_OUTSIDE_ARTIFACT')
    private = tools/('timeout-original-observer-'+label)
    observer = StoppedReleaseObserver(subject.device,subject.args.serial,private,[NOTE])
    if label == 'failure': observer.allowed = {db+s for db in DATABASES for s in ('','-wal','-shm')}
    observer.identity = subject.observer.identity
    require(subject.identity is not None and observer.observe_identity() == subject.identity == observer.identity,
            'SYNC_TIMEOUT_COLLECTOR_OWNER_CHANGED')
    require(observer.text('id','-u') == '0', 'SYNC_TIMEOUT_COLLECTOR_ROOT_UNAVAILABLE')
    # Both methods reject running products; neither invokes a lifecycle command.
    root = observer.discover_data_root(); observer.copy_stopped(root,'snapshot')
    raw = private/'snapshot'; public = output/'content-originals'/label/'snapshot'
    publish_original(raw,public,subject.identity,include_note=label != 'failure')
    if label == 'failure': return public
    if label == 'timed-out': subject.timeout_originals_preserved = True
    value = project_timeout(public,tools/('timeout-working-'+label),stage=stage)
    record(output/(label+'-content-projection.json'),value)
    return value,(public/'vault'/NOTE).read_bytes()


def stop_completed_phase(subject):
    """Explicit stopped sampling after an exact successful timeout GUI phase.

    No failure/finally path calls this action. It is neither the transport
    failure nor an acknowledgement that the client received the held response.
    """
    from android_release_upgrade import ReleaseUpgrade
    from android_sync_timeout_host import ENTRY,TIMEOUT,RESUME,validate_parent,process_lifetime,_query,_reads,_approved_stdout,ReleaseAuthBackground
    from android_release_sync_timeout_validation import validate_host
    from android_sync_timeout_cleanup import LABEL
    require(subject.phases, 'SYNC_TIMEOUT_SNAPSHOT_STOP_NO_PHASE')
    phase=subject.phases[-1];kind=phase.get('phase')
    labels={TIMEOUT:(LABEL,'timed-out-snapshot-stop'),RESUME:('timeout-original-reconciled','reconciled-snapshot-stop')}
    require(kind in labels,'SYNC_TIMEOUT_SNAPSHOT_STOP_EXACT_PHASE')
    label,stage=labels[kind]
    require(subject.timeout_stage==stage and (kind==TIMEOUT or
        getattr(subject,'timeout_originals_preserved',False) is True and getattr(subject,'timeout_read_verified',False) is True),
        'SYNC_TIMEOUT_SNAPSHOT_STOP_SEQUENCE')
    output=exact_directory(subject.output);tools=exact_directory(subject.tools)
    require(not tools.is_relative_to(output) and not output.is_relative_to(tools), 'SYNC_TIMEOUT_SNAPSHOT_STOP_PRIVATE_PATH')
    def original(suffix):
        path=output/(label+'-'+suffix+'.json')
        require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=4096, 'SYNC_TIMEOUT_SNAPSHOT_STOP_ORIGINAL_RECORD')
        return closed_json(path.read_bytes())
    parent=validate_parent(original('original-parent'),kind);lifetime=original('original-lifetime')
    require(same_fields(original('host-phase-intent'),dict(phase=kind,entry=ENTRY,automatic_retry=False,native_timeout_verified=False)),
        'SYNC_TIMEOUT_SNAPSHOT_STOP_EXACT_ENTRY')
    require(type(lifetime) is dict and set(lifetime)=={'package','pid','uid','start_ticks'} and lifetime['package']==APP
        and type(lifetime['pid']) is int and 0<lifetime['pid']<2**31
        and type(lifetime['uid']) is int and 10000<=lifetime['uid']<100000
        and type(lifetime['start_ticks']) is int and 0<lifetime['start_ticks']<2**64, 'SYNC_TIMEOUT_SNAPSHOT_STOP_LIFETIME')
    expected=dict(phase=kind,label=label,pid=lifetime['pid'],passed=True,
        original_parent_intent_id=parent['parent_intent_id'],original_input_intent_id=parent['input_intent_id'],
        same_live_instrumentation_through_original_release=kind==TIMEOUT,failed_UI_observed_while_original_response_held=kind==TIMEOUT,
        native_timeout_verified=False,native_cycle_acknowledged=False,client_receipt_known=False)
    require(same_fields(phase,expected) and same_fields(original('instrument-result'),dict(returncode=0,unknown_result=False,automatic_retry=False,
        early_cleanup_not_success=False,no_final_product_force_stop_or_reopen=True)), 'SYNC_TIMEOUT_SNAPSHOT_STOP_DRIVER_NOT_COMPLETE')
    path=output/(label+'-instrument.stdout')
    require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=4096,'SYNC_TIMEOUT_SNAPSHOT_STOP_STATIC_RESULT')
    stdout=path.read_bytes();_approved_stdout(stdout,kind)
    if kind==TIMEOUT:
        held=subject.timeout_control.held
        require(held is not None and held.release_attempted and held.release_control is not None,
            'SYNC_TIMEOUT_SNAPSHOT_STOP_ORIGINAL_RELEASE_MISSING')
        keys=('original-parent','ready','failed-ready','original-lifetime','input-owner','input-owner-at-command','host-phase-intent',
            'host-touch-intent','host-touch-result','host-touch-after-input-owner','original-window','failure-clock','held-service-marker',
            'original-response-release','release-control-intent','release-control-attempt','release-control-receipt',
            'input-finish-intent','input-finish-result','input-finish-readback','released-intent','released-result','released-readback','instrument-result')
        correlation=validate_host(phase,{key:original(key) for key in keys},stdout,held.control,held.release_control)
        record(output/'timeout-original-host-correlated.json',correlation)
    pid=str(lifetime['pid']).encode();uid=lifetime['uid']
    def owner():
        require(_reads(subject).observer.observe_identity()==subject.identity
            and _query(subject,('id','-u'),16)==b'0\n'
            and _query(subject,('pidof',APP),64).strip()==pid
            and ReleaseAuthBackground.app_is_resumed(_reads(subject))
            and process_lifetime(subject,pid,uid)==lifetime,'SYNC_TIMEOUT_SNAPSHOT_STOP_OWNER_CHANGED')
    prefix=label+'-snapshot-stop';private=tools/prefix;private.mkdir(exist_ok=False)
    owner();command=('shell','-T',shlex.join(('am','force-stop','--user','0',APP)))
    record(output/(prefix+'-intent.json'),dict(arguments=list(command),phase=kind,
        original_parent_intent_id=parent['parent_intent_id'],original_input_intent_id=parent['input_intent_id'],
        original_lifetime=lifetime,owned_boot=subject.identity,original_driver_completed=True,
        snapshot_stop_not_transport_failure=True,automatic_retry=False))
    owner();invoked=False
    def device(*args,**kwargs):
        nonlocal invoked
        require(tuple(args)==command and not invoked,'SYNC_TIMEOUT_SNAPSHOT_STOP_REENTRY')
        # This last full owner check is after the real recorder's private
        # command intent, immediately before the once-only device command.
        owner();invoked=True;return subject.device(*args,**kwargs)
    recorder=SimpleNamespace(output=private,device=device)
    try:
        result=ReleaseUpgrade.original_command(recorder,'once-snapshot-stop',command,timeout=2)
        require(type(result.returncode) is int and result.returncode==0 and type(result.stdout) is bytes and result.stdout==b''
            and type(result.stderr) is bytes and result.stderr==b'','SYNC_TIMEOUT_SNAPSHOT_STOP_UNAPPROVED_STREAM')
    finally:
        path=private/'once-snapshot-stop-result.json';result=closed_json(path.read_bytes()) if path.exists() else None
        facts=dict(returncode=None if result is None else result['returncode'],device_command_invoked=invoked,
            unknown_result=result is None or result.get('unknown_result',False),automatic_retry=False,
            unvalidated_streams_exported=False,snapshot_stop_not_transport_failure=True)
        for stream in ('stdout','stderr'):
            path=private/('once-snapshot-stop.'+stream)
            if path.exists():
                size=path.stat().st_size;facts[stream+'_bytes']=size
                facts[stream+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest() if size<=1024**2 else None
        record(output/(prefix+'-result.json'),facts)
    reads=_reads(subject);observer=StoppedReleaseObserver(reads.observer.device,subject.args.serial,private/'stopped-check',[NOTE])
    observer.identity=subject.identity;observer.require_stopped()
    require(observer.observe_identity()==subject.identity and _query(subject,('id','-u'),16)==b'0\n',
        'SYNC_TIMEOUT_SNAPSHOT_STOP_FINAL_OWNER_CHANGED')
    record(output/(prefix+'-confirmed.json'),dict(original_driver_completed=True,same_owned_root_boot=True,
        original_app_and_services_absent=True,user0_force_stopped=True,snapshot_stop_not_transport_failure=True,
        native_apply_ACK=False,automatic_retry=False))


def freeze_failure(subject, error):
    """The original wholefalse record precedes cleanup and all observations."""
    stage = getattr(subject,'timeout_stage','prepare')
    stage = stage if stage in STEPS else 'unclassified'
    control = getattr(subject,'timeout_control',None)
    operation = getattr(control,'operation',None)
    if operation is not None:
        require(type(operation) is str and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',operation),
                'SYNC_TIMEOUT_FAILURE_ORIGINAL_CONTROL_ID')
    value = dict(passed=False,stage=stage,error_type=error_kind(error),original_hold_operation_id=operation,
        automatic_retry=False,whole_timeout_component_passed=False,Native_RAM_invocation_id_observed=False,
        no_implicit_product_stop_or_reopen=True)
    record(subject.output/'timeout-component-failure.json',value)
    return value


def handle_failure(subject, error):
    require(not getattr(subject,'timeout_failure_handled',False),'SYNC_TIMEOUT_FAILURE_NOT_REPEATED')
    subject.timeout_failure_handled = True
    freeze_failure(subject,error)
    control = getattr(subject,'timeout_control',None)
    if control is not None:
        try: control.cleanup()
        except Exception as failure:
            record(subject.output/'timeout-failure-control-cleanup-unavailable.json',dict(error_type=error_kind(failure),
                whole_timeout_component_passed=False,automatic_retry=False,client_receipt_known=False))
    # Retain whatever is actually stopped without creating that state. Unknown
    # lifecycle results never authorize another force-stop or product reopen.
    try: collect_original(subject,'failure')
    except Exception as failure:
        record(subject.output/'timeout-failure-original-unavailable.json',dict(error_type=error_kind(failure),
            whole_timeout_component_passed=False,product_stop_or_reopen_issued=False))
        return
    # Only after a stopped DB-only original has been retained may the existing
    # private auth copier/stat-hash observer and bounded provider reads run.
    for label,read in (('auth',lambda:subject.snapshot('timeout-failure-auth')),
                       ('cipher-fingerprints',subject.observer.ciphertext_fingerprints),
                       ('provider',lambda:subject.observe('timeout-failure')),
                       ('sync-provider',lambda:subject.sync_provider('timeout-failure'))):
        try:
            value = read()
            if label == 'cipher-fingerprints': record(subject.output/'timeout-failure-cipher-fingerprints.json',value)
        except Exception as failure:
            record(subject.output/('timeout-failure-'+label+'-unavailable.json'),dict(error_type=error_kind(failure),
                whole_timeout_component_passed=False,automatic_retry=False))


def run_guarded(subject, action):
    try: return action()
    except Exception as error:
        handle_failure(subject,error)
        raise


def main(probe_type, argv=None):
    """Dedicated CLI: no normal auth main/failure hook and no final stop."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','sdk','java','output'): parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args(argv); probe = None
    try:
        # The workflow supplies a workspace-relative output. Freeze that exact
        # location before the real constructor derives its private tools path;
        # do not resolve aliases into an apparently admissible directory.
        require('..' not in args.output.parts, 'SYNC_TIMEOUT_CLI_OUTPUT_ALIAS')
        output = args.output.absolute()
        require(output.resolve() == output and all(not p.is_symlink() for p in (output,*output.parents)),
                'SYNC_TIMEOUT_CLI_OUTPUT_ALIAS')
        probe = probe_type(args.inputs,args.sdk,args.java,output)
        result = probe.run()
        require(result['passed'] is True and result['physical_phone_verified'] is False and result['published'] is False,
                'SYNC_TIMEOUT_CLI_RESULT')
        print(json.dumps({k:result[k] for k in ('kind','passed','abi','physical_phone_verified','published')}))
        return 0
    except Exception as error:
        if probe is not None and not getattr(probe,'timeout_failure_handled',False):
            try: handle_failure(probe,error)
            except Exception as failure:
                # A pre-existing exclusive record is preserved, never repaired.
                try: record(probe.output/'timeout-failure-policy-unavailable.json',dict(error_type=error_kind(failure),
                        passed=False,automatic_retry=False,no_implicit_product_stop_or_reopen=True))
                except Exception: pass
        print('RELEASE_SYNC_TIMEOUT_FAILED_'+error_kind(error))
        return 1
