"""Original killed-process/held release/explicit read facts; no device or network mutations."""
import hashlib
import re

from android_release_sync_process_snapshot import NOTE, HASH, canonical, preserve_note, preserve_open, revision_json
from android_release_observation import APP
from android_sync_process_host import LOSS, validate_parent, validate_ready, validate_loss_ready
from android_runtime_probe import BODY, require
from android_sync_stop_host import same_fields


def validate_controls(hold, held, release, released):
    require(same_fields(hold, dict(operation_id=hold['operation_id'], mode='hold_revision', expected_revision=0, result_revision=1))
            and same_fields(release, dict(operation_id=release['operation_id'], mode='release', expected_revision=1, result_revision=2))
            and canonical(hold['operation_id']) and canonical(release['operation_id']) and hold['operation_id'] != release['operation_id'],
            'RELEASE_SYNC_PROCESS_ORIGINAL_CONTROL_LINEAGE')
    require(same_fields(held, dict(operation_id=hold['operation_id'], mode='hold_revision', revision=1, action='revision',
                service_status=200, service_response_complete=True, raw_body_recorded=False))
            and type(released) is dict and set(released) == {'operation_id','original_response_send_attempted','ASGI_send_returned','client_receipt_known'}
            and released['operation_id'] == hold['operation_id'] and released['original_response_send_attempted'] is True
            and type(released['ASGI_send_returned']) is bool and released['client_receipt_known'] is False,
            'RELEASE_SYNC_PROCESS_ORIGINAL_HELD_RELEASE_MARKER')


def revision_counts(provider):
    counts = provider['delivery']['counts']
    require(type(counts) is dict and len(counts) <= 64 and all(type(k) is str and re.fullmatch(r'[a-z_]+:[0-9]{3}', k)
            and type(v) is int and 0 <= v <= 10000 for k,v in counts.items()), 'RELEASE_SYNC_PROCESS_REQUEST_COUNTER_SHAPE')
    return {k:v for k,v in counts.items() if k.startswith('revision:')}


def validate_killed(before, after, provider_before, provider, device, hold, held, release, released):
    validate_controls(hold, held, release, released); preserve_note(before, after)
    a,b = before['native'], after['native']; archived, upload = a['outbox']; job, = b['jobs']; attempt, = b['attempts']
    require(before['process_stage'] == 'associated' and after['process_stage'] == 'killed'
            and a['binding'] == b['binding'] and a['association'] == b['association']
            and all(not a[k] for k in ('jobs','attempts','heads','inbox','issuers','conflicts'))
            and type(provider_before['vault']['sequence']) is int and provider_before['vault']['sequence'] == 0
            and type(provider_before['vault']['used']) is int and provider_before['vault']['used']==0 and not revision_counts(provider_before)
            and all(not provider_before['rows'][k] for k in ('revisions','files','objects','uploads')),
            'RELEASE_SYNC_PROCESS_ORIGINAL_FRESH_SCOPE_CHANGED')
    require(type(device) is str and re.fullmatch(r'[0-9a-f]{32}', device)
            and len(provider['rows']['revisions']) == 1 and type(provider['vault']['sequence']) is int and provider['vault']['sequence'] == 1
            and type(provider['vault']['used']) is int and provider['vault']['used']==len(BODY.encode())
            and provider['vault']['id'] == provider_before['vault']['id'] == b['binding']['remote_vault']
            and hold['operation_id'] not in (archived['operation_id'], upload['operation_id'])
            and release['operation_id'] not in (archived['operation_id'], upload['operation_id']), 'RELEASE_SYNC_PROCESS_ORIGINAL_VAULT_OR_ID_CHANGED')
    remote, = provider['rows']['revisions']
    require(type(remote) is dict and set(remote) == {'sequence','file_id','base_revision','path','operation','hash','size','device_id','operation_id','fingerprint'}
            and type(remote['fingerprint']) is str and re.fullmatch(r'[0-9a-f]{64}', remote['fingerprint'])
            and same_fields(remote, dict(operation_id=upload['operation_id'], file_id=upload['file_id'], base_revision=0, path=NOTE, operation='put',
                hash=HASH, size=len(BODY.encode()), sequence=1, device_id=device, fingerprint=remote['fingerprint']))
            and all(remote[k] == job[k] for k in ('operation_id','file_id','path','hash','size')),
            'RELEASE_SYNC_PROCESS_ORIGINAL_REMOTE_COMMIT_CHANGED')
    require(b['outbox'] == [archived,dict(upload,state='queued')] and job['state'] == 'committing'
            and job['remote_revision'] is None and job['error'] is None and b['binding']['cursor'] == 0
            and attempt['outcome'] in ('running','interrupted')
            and same_fields(attempt, dict(operation_id=upload['operation_id'], attempts=1, outcome=attempt['outcome'], error=None))
            and b['issuers'] == [dict(operation_id=upload['operation_id'],device_id=device)] and not b['heads'] and not b['inbox'] and not b['conflicts']
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id')),
            'RELEASE_SYNC_PROCESS_ORIGINAL_NOT_INTERRUPTED_OR_PREMATURE_APPLY')
    require(provider['rows']['objects'] == [dict(hash=HASH,size=len(BODY.encode()))]
            and provider['rows']['files'] == [dict(file_id=upload['file_id'],sequence=1,path_key=NOTE,deleted=0)]
            and not provider['rows']['uploads'], 'RELEASE_SYNC_PROCESS_PROVIDER_ORIGINAL_BYTES_SCOPE')
    events = provider['delivery']['events']; require(type(events) is list and len(events) <= 256, 'RELEASE_SYNC_PROCESS_DELIVERY_BOUND')
    fault = [v for v in events if v['action'] == 'revision' or v['fault_operation'] == hold['operation_id']]
    require(fault == [dict(action='revision',service_status=200,delivery_status=None,fault_operation=hold['operation_id'])]
            and revision_counts(provider) == {'revision:200':1}, 'RELEASE_SYNC_PROCESS_ORIGINAL_REVISION_NOT_HELD_ONCE')
    return dict(remote, vault_id=provider['vault']['id'])


def validate_open_read(before,after,provider_before,provider_after):
    transition=preserve_open(before,after)
    require(provider_before==provider_after,'RELEASE_SYNC_PROCESS_OPEN_READ_CHANGED_PROVIDER')
    return transition


def validate_lifecycle(phase,parent,ready,loss_ready,intent,result,owner,absence,final_absence,identity,hold_id):
    """Correlate original host journals; durable/PG facts are verified separately."""
    validate_parent(parent,LOSS)
    lifetime=intent['original_lifetime']
    require(type(lifetime) is dict and set(lifetime)=={'package','pid','uid','start_ticks'}
        and lifetime['package']==APP and type(lifetime['pid']) is int and 0<lifetime['pid']<2**31
        and type(lifetime['uid']) is int and 10000<=lifetime['uid']<100000
        and type(lifetime['start_ticks']) is int and 0<lifetime['start_ticks']<2**64,
        'RELEASE_SYNC_PROCESS_LIFECYCLE_EXACT_ORIGINAL')
    validate_ready(ready,parent,ready['elapsed_ms'],lifetime['uid'])
    require(type(owner) is dict and set(owner)=={'original_lifetime','same_original_foreground_PID_UID_boot','original_instrumentation_alive',
        'ready_age_ms','ready_age_bound_ms'} and owner['original_lifetime']==lifetime
        and owner['same_original_foreground_PID_UID_boot'] is True and owner['original_instrumentation_alive'] is True
        and type(owner['ready_age_ms']) is int and 0<=owner['ready_age_ms']<=2000
        and type(owner['ready_age_bound_ms']) is int and owner['ready_age_bound_ms']==2000,
        'RELEASE_SYNC_PROCESS_LIFECYCLE_ORIGINAL_OWNER')
    validate_loss_ready(loss_ready,parent,loss_ready['elapsed_ms']+owner['ready_age_ms'],lifetime['uid'],ready['window'])
    require(type(identity) is dict and set(identity)=={'avd','qemu','api','build_type','debuggable','boot_id'}
        and type(identity['avd']) is str and re.fullmatch(r'opennexus-ci-[1-9][0-9]{0,19}-[1-9][0-9]{0,5}',identity['avd'])
        and identity['qemu']=='1' and identity['api']=='36' and identity['build_type'] in ('userdebug','eng')
        and identity['debuggable']=='1' and canonical(identity['boot_id']),'RELEASE_SYNC_PROCESS_LIFECYCLE_BOOT_IDENTITY')
    require(same_fields(intent,dict(operation_id=intent['operation_id'],arguments=['shell','-T','am force-stop --user 0 '+APP],
        parent_intent_id=parent['parent_intent_id'],input_intent_id=parent['input_intent_id'],original_lifetime=lifetime,owned_boot=identity,
        held_operation_id=hold_id,product_UI_observation_closed=True,automatic_retry=False,
        ordinary_cleanup_is_not_tested_process_loss=True,native_invocation_id_observed=False))
        and canonical(intent['operation_id']) and canonical(hold_id)
        and len({intent['operation_id'],hold_id,parent['parent_intent_id'],parent['input_intent_id']})==4,
        'RELEASE_SYNC_PROCESS_LIFECYCLE_ORIGINAL_INTENT')
    empty=hashlib.sha256(b'').hexdigest()
    require(same_fields(result,dict(returncode=0,unknown_result=False,automatic_retry=False,unvalidated_streams_exported=False,
        original_operation_id=intent['operation_id'],stdout_bytes=0,stdout_sha256=empty,stderr_bytes=0,stderr_sha256=empty)),
        'RELEASE_SYNC_PROCESS_LIFECYCLE_RESULT_UNKNOWN')
    expected=dict(package=APP,original_pid=lifetime['pid'],product_uid=lifetime['uid'],original_pid_absent=True,
        all_owned_package_processes_absent=True,user0_force_stopped=True,same_owned_root_boot=True,original_driver_alive=True,
        original_driver_completed=False,raw_process_list_exported=False,native_cycle_acknowledged=False)
    require(same_fields(absence,expected) and same_fields(final_absence,dict(expected,original_driver_alive=False,original_driver_completed=True)),
        'RELEASE_SYNC_PROCESS_LIFECYCLE_ABSENCE_OR_DRIVER')
    require(type(phase) is dict and set(phase)=={'phase','label','pid','passed','original_parent_intent_id','original_input_intent_id',
        'actual_process_absence_observed','same_live_instrumentation_through_original_release','Native_RAM_invocation_id_observed',
        'native_original_state_verified','client_receipt_known'} and type(phase['label']) is str
        and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',phase['label']) and same_fields(phase,dict(phase=LOSS,label=phase['label'],pid=lifetime['pid'],
            passed=True,original_parent_intent_id=parent['parent_intent_id'],original_input_intent_id=parent['input_intent_id'],
            actual_process_absence_observed=True,same_live_instrumentation_through_original_release=True,
            Native_RAM_invocation_id_observed=False,native_original_state_verified=False,client_receipt_known=False)),
        'RELEASE_SYNC_PROCESS_LIFECYCLE_ORIGINAL_DRIVER_RESULT')
    return dict(original_lifecycle_id=intent['operation_id'],original_pid=lifetime['pid'],same_original_journals_correlated=True,
        input_ready_freshness_independently_recomputed=False,Native_RAM_invocation_id_observed=False,native_apply_ACK=False)


def validate_reconciled(before, after, provider_before, provider_after, receipt):
    preserve_note(before, after); a,b = before['native'],after['native']
    require(before['process_stage'] == 'killed-read' and after['process_stage'] == 'reconciled'
            and b['binding'] == dict(a['binding'],cursor=1)
            and all(b[k] == a[k] for k in ('association','attempts','issuers','conflicts'))
            and b['outbox'] == [a['outbox'][0],dict(a['outbox'][1],state='acked')]
            and b['jobs'] == [dict(a['jobs'][0],state='acked',remote_revision=1,error=None)]
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id','original_job_commit')),
            'RELEASE_SYNC_PROCESS_REPLACED_OR_RELABELLED_ORIGINAL')
    require(provider_after['vault'] == provider_before['vault']
            and all(provider_after['rows'][k] == provider_before['rows'][k] for k in ('revisions','files','objects','uploads'))
            and revision_counts(provider_before) == revision_counts(provider_after) == {'revision:200':1}
            and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')} ==
                {k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')}
            and provider_after['delivery']['counts'].get('changes:200',0) > provider_before['delivery']['counts'].get('changes:200',0)
            and [v for v in provider_after['delivery']['events'] if v['action'] != 'changes'] ==
                [v for v in provider_before['delivery']['events'] if v['action'] != 'changes'],
            'RELEASE_SYNC_PROCESS_ANOTHER_WRITE_OR_NO_ORIGINAL_CHANGES_READ')
    inbox, = b['inbox']; revision = revision_json(inbox['revision'])
    keys = ('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')
    require(type(revision) is dict and set(revision) == set(keys) and all(revision[k] == receipt[k] for k in keys)
            and inbox['state'] == 'applied' and b['heads'] == [dict(file_id=receipt['file_id'],revision=1,path=NOTE,hash=HASH)],
            'RELEASE_SYNC_PROCESS_ORIGINAL_NOT_APPLIED')
