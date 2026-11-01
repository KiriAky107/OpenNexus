"""Closed failure facts only; no UI text, AX trees, action retries or new ACKs."""
import time
import re
import hashlib
import json
from android_runtime_probe import DRIVER,require
from android_release_observation import APP
from android_sync_stop_host import canonical,closed_json,record,same_fields
from android_sync_timeout_alerts import CODES as ALERT_CODES,validate as validate_alerts

PHASE='cycle-timeout-original'
SAFE_CODES=frozenset(('SYNC_PROCESS_OWNED_BOOT_ROOT_OR_DRIVER_CHANGED','SYNC_PROCESS_ORIGINAL_FOREGROUND_LIFETIME_CHANGED',
    'SYNC_PROCESS_WAIT_BOUND','SYNC_PROCESS_WAIT_CLOCK_OR_TIMEOUT','SYNC_PROCESS_OBSERVATION_FINISHED_TOO_LATE',
    'SYNC_PROCESS_FINAL_GUARD_FINISHED_TOO_LATE','SYNC_PROCESS_WAIT_TIMEOUT','SYNC_PROCESS_READ_ONLY_QUERY_UNKNOWN',
    'SYNC_TIMEOUT_FAILED_READY_OWNER_OR_CLOCK','SYNC_TIMEOUT_FAILURE_BEFORE_CLIENT_BOUND','SYNC_TIMEOUT_CLOCK_QUERY_BOUND',
    'SYNC_TIMEOUT_DEVICE_CLOCK_SHAPE','SYNC_TIMEOUT_DEVICE_CLOCK_BOUND',
    'SYNC_TIMEOUT_RELEASE_IDENTITY_SCOPE','SYNC_TIMEOUT_RELEASE_IDENTITY_BOUND','SYNC_TIMEOUT_RELEASE_IDENTITY_QUERY','SYNC_TIMEOUT_RELEASE_IDENTITY_SHAPE',
    'SYNC_TIMEOUT_RELEASE_MARGIN_EXHAUSTED','SYNC_TIMEOUT_RELEASE_NOT_ORIGINAL','SYNC_TIMEOUT_RELEASE_INTENT_FINISHED_TOO_LATE',
    'SYNC_TIMEOUT_RELEASE_ATTEMPT_FINISHED_TOO_LATE','SYNC_TIMEOUT_RELEASE_RECEIPT_CHANGED','SYNC_TIMEOUT_RELEASE_CAS_FINISHED_TOO_LATE',
    'SYNC_TIMEOUT_SIGNAL_MARGIN_EXHAUSTED','SYNC_TIMEOUT_SIGNAL_FINISHED_TOO_LATE','SYNC_TIMEOUT_HELD_MARKER_CHANGED'))


def failure_reason(error):
    # An arbitrary exception message is never exported, even when it has the
    # appearance of a code. Only these exact source-bound constants may pass.
    value=str(error) if type(error) is RuntimeError else ''
    return value if value in SAFE_CODES else 'unclassified'


def failure_kind(error):
    name=type(error).__name__
    return name if name in ('RuntimeError','TimeoutError','TimeoutExpired','FileExistsError','FileNotFoundError','PermissionError',
        'ValueError','TypeError','KeyError','OSError','AssertionError','JSONDecodeError','UnicodeDecodeError') else 'Exception'


def validate_observation(value,parent,ready):
    require(parent['phase']==PHASE and type(parent['schema']) is int and parent['schema']==1
        and canonical(parent['parent_intent_id']) and canonical(parent['input_intent_id'])
        and parent['parent_intent_id']!=parent['input_intent_id'] and ready['package']==APP
        and type(ready['uid']) is int and 10000<=ready['uid']<100000 and type(ready['window']) is int and 0<=ready['window']<=65535
        and type(ready['elapsed_ms']) is int and 0<=ready['elapsed_ms']<2**63,'SYNC_TIMEOUT_FAILURE_CONTEXT')
    keys={'schema','phase','parent_intent_id','input_intent_id','package','uid','window','phase_stage','original_ready_elapsed_ms',
        'input_finished','last_scene_scan_finished','label_counts','observed_elapsed_ms','count_saturated_at','driver_error_kind',
        'failure_only','native_timeout_verified','raw_UI_recorded','alert_diagnostics'}
    require(type(value) is dict and set(value)==keys,'SYNC_TIMEOUT_FAILURE_OBSERVATION_SCOPE')
    expected=dict(schema=3,phase=PHASE,parent_intent_id=parent['parent_intent_id'],input_intent_id=parent['input_intent_id'],
        package=APP,uid=ready['uid'],window=ready['window'],original_ready_elapsed_ms=ready['elapsed_ms'],input_finished=True,
        count_saturated_at=2,failure_only=True,native_timeout_verified=False,raw_UI_recorded=False)
    require(parent['phase']==PHASE and all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items())
        and value['phase_stage'] in ('waiting_failed_scene','waiting_release','other')
        and value['driver_error_kind'] in ('sequence_guard','unclassified') and type(value['last_scene_scan_finished']) is bool,
        'SYNC_TIMEOUT_FAILURE_OBSERVATION_OWNER')
    if value['last_scene_scan_finished']:
        require(type(value['label_counts']) is list and len(value['label_counts'])==8
            and all(type(v) is int and 0<=v<=2 for v in value['label_counts'])
            and type(value['observed_elapsed_ms']) is int and ready['elapsed_ms']<=value['observed_elapsed_ms']<2**63,
            'SYNC_TIMEOUT_FAILURE_OBSERVATION_BOUNDED_SCENE')
        require(value['alert_diagnostics'] is None or validate_alerts(value['alert_diagnostics'],
                value['label_counts'],value['observed_elapsed_ms']),'SYNC_TIMEOUT_FAILURE_ALERT_SCOPE')
    else:require(value['label_counts'] is None and value['observed_elapsed_ms'] is None,'SYNC_TIMEOUT_FAILURE_OBSERVATION_ABSENT')
    require(value['last_scene_scan_finished'] or value['alert_diagnostics'] is None,'SYNC_TIMEOUT_FAILURE_ALERT_ABSENT')
    return dict(value)


def retain_failure(subject,label,parent,ready):
    """One bounded read from this driver; never read arbitrary error/UI streams."""
    require(type(label) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',label),'SYNC_TIMEOUT_FAILURE_LABEL')
    output=subject.output/(label+'-driver-failure-observation.json')
    if output.exists() or output.is_symlink():raise FileExistsError(output)
    try:
        path='no_backup/owned-sync-timeout-'+PHASE+'-failure-observation.json'
        start=time.monotonic()
        result=subject.shell('run-as',DRIVER,'sh','-c','test -f '+path+' && test ! -L '+path+' && cat '+path,check=False,timeout=1)
        require(0<=start<=time.monotonic()<=start+1,'SYNC_TIMEOUT_FAILURE_OBSERVATION_READ_BOUND')
        require(type(result.returncode) is int and result.returncode==0 and type(result.stdout) is bytes and 0<len(result.stdout)<=4096
            and type(result.stderr) is bytes and result.stderr==b'','SYNC_TIMEOUT_FAILURE_OBSERVATION_READ')
        value=validate_observation(closed_json(result.stdout),parent,ready)
    except Exception:
        record(output,dict(available=False,reason='not_approved_or_unavailable',
            failure_only=True,automatic_retry=False,unvalidated_streams_exported=False,native_timeout_verified=False));return
    record(output,dict(available=True,observation=value,
        label_order=['failed','disabled_cycle','unconfirmed','network_alert','enabled_stop','completed','stopped','enabled_cycle'],
        alert_kind_order=list(ALERT_CODES),alert_diagnostics_available=value['alert_diagnostics'] is not None,
        failure_only=True,automatic_retry=False,unvalidated_streams_exported=False,native_timeout_verified=False))


def owner_failure_observation(checks,parent,ready):
    """Pure closed projection; no new poll, identity, root or clock sample."""
    from android_sync_timeout_host import validate_parent,validate_ready
    validate_parent(parent,PHASE)
    validate_ready(ready,parent,ready['elapsed_ms'],ready['uid'])
    require(type(checks) is list and len(checks)==3 and all(v is None or type(v) is bool for v in checks),
        'SYNC_TIMEOUT_OWNER_FAILURE_CHECK_TYPES')
    patterns=([False,None,None],[True,False,None],[True,True,False])
    require(checks in patterns,'SYNC_TIMEOUT_OWNER_FAILURE_CHECK_SEQUENCE')
    order=['original_driver_alive','same_original_owned_identity','original_root_zero_LF']
    digest=lambda value:hashlib.sha256((json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()).hexdigest()
    return dict(schema=1,phase=PHASE,phase_stage='original-failed-ready',parent_intent_id=parent['parent_intent_id'],
        input_intent_id=parent['input_intent_id'],package=APP,uid=ready['uid'],window=ready['window'],
        original_ready_elapsed_ms=ready['elapsed_ms'],parent_metadata_sha256=digest(parent),ready_metadata_sha256=digest(ready),
        guard_code='SYNC_PROCESS_OWNED_BOOT_ROOT_OR_DRIVER_CHANGED',check_order=order,check_results=list(checks),
        first_failed_check=order[checks.index(False)],same_original_guard_observations=True,unexecuted_checks_are_null=True,
        failure_only=True,whole_timeout_component_passed=False,native_timeout_verified=False,
        raw_identity_or_root_output_recorded=False,automatic_retry=False)


def retain_owner_failure(subject,label,parent,ready,cached,error):
    """One exclusive metadata write after wholefalse; never replace a guard."""
    require(type(label) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',label),'SYNC_TIMEOUT_OWNER_FAILURE_LABEL')
    require(failure_reason(error)=='SYNC_PROCESS_OWNED_BOOT_ROOT_OR_DRIVER_CHANGED',
        'SYNC_TIMEOUT_OWNER_FAILURE_NOT_COMPOSITE')
    require(type(cached) is tuple and len(cached)==3 and type(cached[0]) is dict and type(cached[1]) is dict
        and cached[0]==parent and cached[1]==ready,'SYNC_TIMEOUT_OWNER_FAILURE_CACHE_SCOPE')
    value=owner_failure_observation(cached[2],cached[0],cached[1])
    # Bind both current and cached typed metadata; equality alone accepts bools
    # as integers and must not admit a differently typed caller context.
    require(value==owner_failure_observation(cached[2],parent,ready),'SYNC_TIMEOUT_OWNER_FAILURE_CONTEXT')
    record(subject.output/(label+'-host-owner-failure-observation.json'),value)


def validate_owner_failure(value,parent,ready):
    """Validate a future retained original; partial or extra metadata is unknown."""
    require(type(value) is dict and 'check_results' in value,'SYNC_TIMEOUT_OWNER_FAILURE_RECORD_SCOPE')
    expected=owner_failure_observation(value['check_results'],parent,ready)
    require(same_fields(value,expected),'SYNC_TIMEOUT_OWNER_FAILURE_RECORD_CONTEXT')
    return dict(value)
