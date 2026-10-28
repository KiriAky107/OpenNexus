"""Original network-failure/held response/read facts; no Native timeout-cause inference."""
import hashlib
import re
import shlex

from android_release_sync_timeout_snapshot import NOTE, HASH, canonical, preserve_note, preserve_read, revision_json
from android_release_observation import APP
from android_sync_timeout_host import TIMEOUT, ENTRY, validate_parent, validate_ready, validate_failed, _approved_stdout
from android_runtime_probe import BODY, DRIVER, require
from android_sync_stop_host import same_fields


def validate_controls(hold, held, release, released):
    require(same_fields(hold, dict(operation_id=hold['operation_id'], mode='hold_revision', expected_revision=0, result_revision=1))
            and same_fields(release, dict(operation_id=release['operation_id'], mode='release', expected_revision=1, result_revision=2))
            and canonical(hold['operation_id']) and canonical(release['operation_id']) and hold['operation_id'] != release['operation_id'],
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_CONTROL_LINEAGE')
    require(same_fields(held, dict(operation_id=hold['operation_id'], mode='hold_revision', revision=1, action='revision',
                service_status=200, service_response_complete=True, raw_body_recorded=False))
            and type(released) is dict and set(released) == {'operation_id','original_response_send_attempted','ASGI_send_returned','client_receipt_known'}
            and released['operation_id'] == hold['operation_id'] and released['original_response_send_attempted'] is True
            and type(released['ASGI_send_returned']) is bool and released['client_receipt_known'] is False,
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_HELD_RELEASE_MARKER')


def revision_counts(provider):
    counts = provider['delivery']['counts']
    require(type(counts) is dict and len(counts) <= 64 and all(type(k) is str and re.fullmatch(r'[a-z_]+:[0-9]{3}', k)
            and type(v) is int and 0 <= v <= 10000 for k,v in counts.items()), 'RELEASE_SYNC_TIMEOUT_REQUEST_COUNTER_SHAPE')
    return {k:v for k,v in counts.items() if k.startswith('revision:')}


def validate_read(before, after, provider_before, provider_after):
    preserve_read(before, after)
    require(provider_before == provider_after, 'RELEASE_SYNC_TIMEOUT_READ_CHANGED_PROVIDER')


def validate_host(phase, records, stdout, hold, release):
    """Correlate approved original metadata, without reading private input/UI.

    Runtime owner checks and the marker's admission deadline are executed by
    the pinned host. There is no durable timestamp for every release step, so
    this function does not claim an independent replay of those live guards.
    """
    parent = records['original-parent']; validate_parent(parent, TIMEOUT)
    ready = records['ready']; failed = records['failed-ready']; life = records['original-lifetime']
    require(type(life) is dict and set(life) == {'package','pid','uid','start_ticks'}
            and life['package'] == APP and type(life['pid']) is int and 0 < life['pid'] < 2**31
            and type(life['uid']) is int and 10000 <= life['uid'] < 100000
            and type(life['start_ticks']) is int and 0 < life['start_ticks'] < 2**64,
            'RELEASE_SYNC_TIMEOUT_HOST_ORIGINAL_LIFETIME')
    validate_ready(ready, parent, ready['elapsed_ms'], life['uid'])
    for key in ('input-owner','input-owner-at-command'):
        owner = records[key]
        expected = dict(original_lifetime=life,same_original_foreground_PID_UID_boot=True,
            original_instrumentation_alive=True,ready_age_ms=owner['ready_age_ms'],ready_age_bound_ms=2000,
            native_cycle_acknowledged=False)
        if key.endswith('at-command'): expected['observed_after_private_command_intent'] = True
        require(same_fields(owner, expected) and type(owner['ready_age_ms']) is int
                and 0 <= owner['ready_age_ms'] <= 2000, 'RELEASE_SYNC_TIMEOUT_HOST_INPUT_OWNER')
    require(same_fields(records['host-phase-intent'],dict(phase=TIMEOUT,entry=ENTRY,automatic_retry=False,native_timeout_verified=False)),
            'RELEASE_SYNC_TIMEOUT_HOST_EXACT_ENTRY')
    require(same_fields(records['host-touch-intent'],dict(arguments=['shell','-T',shlex.join(('input','touchscreen','tap',str(ready['x']),str(ready['y'])))],
            package=APP,automatic_retry=False)), 'RELEASE_SYNC_TIMEOUT_HOST_ORIGINAL_TAP')
    empty = hashlib.sha256(b'').hexdigest()
    require(same_fields(records['host-touch-result'],dict(returncode=0,unknown_result=False,device_command_invoked=True,
            automatic_retry=False,unvalidated_streams_exported=False,stdout_bytes=0,stdout_sha256=empty,stderr_bytes=0,stderr_sha256=empty)),
            'RELEASE_SYNC_TIMEOUT_HOST_TAP_UNKNOWN')
    require(same_fields(records['host-touch-after-input-owner'],dict(input_command_returned=True,native_cycle_acknowledged=False,
            observation_unavailable=False,original_instrumentation_alive=True,same_original_PID=True,app_resumed=True,same_owned_boot_identity=True)),
            'RELEASE_SYNC_TIMEOUT_HOST_POST_INPUT_OWNER')
    window = records['original-window']; clock = records['failure-clock']
    require(type(window) is dict and set(window) == {'preinput_monotonic_ms','hold_release_deadline_monotonic_ms','preinput_elapsed_ms',
            'original_ready_elapsed_ms','host_release_bound_ms','driver_failure_bound_ms','product_client_bound_ms','proxy_hold_bound_ms',
            'clock_reset_on_marker','native_timeout_verified'}
            and all(type(window[k]) is int and 0 <= window[k] < 2**63 for k in window if k not in ('clock_reset_on_marker','native_timeout_verified'))
            and same_fields(window,dict(preinput_monotonic_ms=window['preinput_monotonic_ms'],
                hold_release_deadline_monotonic_ms=window['preinput_monotonic_ms']+42000,
                preinput_elapsed_ms=ready['elapsed_ms']+records['input-owner']['ready_age_ms'],original_ready_elapsed_ms=ready['elapsed_ms'],
                host_release_bound_ms=42000,driver_failure_bound_ms=36000,product_client_bound_ms=30000,proxy_hold_bound_ms=45000,
                clock_reset_on_marker=False,native_timeout_verified=False)), 'RELEASE_SYNC_TIMEOUT_HOST_FROZEN_WINDOW')
    require(type(clock) is dict and set(clock) == {'sample_start_monotonic_ms','sample_end_monotonic_ms','sampled_elapsed_ms',
            'failed_elapsed_ms','signal_deadline_monotonic_ms','original_preinput_monotonic_ms','native_timeout_verified'}
            and clock['native_timeout_verified'] is False
            and all(type(clock[k]) is int and 0 <= clock[k] < 2**63 for k in clock if k != 'native_timeout_verified')
            and clock['original_preinput_monotonic_ms'] == window['preinput_monotonic_ms']
            and window['preinput_monotonic_ms']+30000 <= clock['sample_start_monotonic_ms'] <= clock['sample_end_monotonic_ms']
                <= clock['sample_start_monotonic_ms']+1000
            and clock['failed_elapsed_ms'] == failed['elapsed_ms'] >= window['preinput_elapsed_ms']+30000
            and abs(clock['signal_deadline_monotonic_ms']-min(window['hold_release_deadline_monotonic_ms'],
                clock['sample_start_monotonic_ms']+failed['elapsed_ms']+5000-clock['sampled_elapsed_ms'])) <= 1,
            'RELEASE_SYNC_TIMEOUT_HOST_FAILURE_CLOCK')
    validate_failed(failed,parent,ready,clock['sampled_elapsed_ms'])
    validate_controls(hold,records['held-service-marker'],release,records['original-response-release'])
    require(len({hold['operation_id'],release['operation_id'],parent['parent_intent_id'],parent['input_intent_id']}) == 4,
            'RELEASE_SYNC_TIMEOUT_HOST_REUSED_IDS')
    for key, extra in (('release-control-intent',dict(client_receipt_known=False)),
            ('release-control-attempt',dict(receipt_repair_allowed=False))):
        require(same_fields(records[key],dict(operation_id=release['operation_id'],mode='release',expected_revision=1,
                held_operation_id=hold['operation_id'],automatic_retry=False,**extra)), 'RELEASE_SYNC_TIMEOUT_HOST_RELEASE_LINEAGE')
    require(same_fields(records['release-control-receipt'],release), 'RELEASE_SYNC_TIMEOUT_HOST_RELEASE_RECEIPT')
    for prefix, path in (('input-finish','no_backup/owned-sync-timeout-touch-'+parent['input_intent_id']+'.finish'),
            ('released','no_backup/owned-sync-timeout-released-'+parent['parent_intent_id']+'.finish')):
        command = ['shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote('umask 077; test ! -e '+path+' && test ! -L '+path+' && cat > '+path)]
        require(same_fields(records[prefix+'-intent'],dict(arguments=command,bytes=7,automatic_retry=False))
                and same_fields(records[prefix+'-result'],dict(returncode=0,automatic_retry=False,stdout_bytes=0,stdout_sha256=empty,
                    stderr_bytes=0,stderr_sha256=empty,unvalidated_streams_exported=False))
                and same_fields(records[prefix+'-readback'],dict(exact_static_bytes=True,bytes=7,private_signal_bytes_exported=False)),
                'RELEASE_SYNC_TIMEOUT_HOST_ORIGINAL_SIGNAL')
    require(same_fields(records['instrument-result'],dict(returncode=0,unknown_result=False,automatic_retry=False,
            early_cleanup_not_success=False,no_final_product_force_stop_or_reopen=True)), 'RELEASE_SYNC_TIMEOUT_HOST_DRIVER_UNKNOWN')
    require(type(stdout) is bytes and len(stdout) <= 1024**2, 'RELEASE_SYNC_TIMEOUT_HOST_STATIC_STREAM_BOUND')
    _approved_stdout(stdout,TIMEOUT)
    require(same_fields(phase,dict(phase=TIMEOUT,label=phase['label'],pid=life['pid'],passed=True,
            original_parent_intent_id=parent['parent_intent_id'],original_input_intent_id=parent['input_intent_id'],
            same_live_instrumentation_through_original_release=True,failed_UI_observed_while_original_response_held=True,
            native_timeout_verified=False,native_cycle_acknowledged=False,client_receipt_known=False))
            and type(phase['label']) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',phase['label']),
            'RELEASE_SYNC_TIMEOUT_HOST_ORIGINAL_PHASE')
    return dict(original_input_id=parent['input_intent_id'],original_hold_id=hold['operation_id'],original_release_id=release['operation_id'],
        original_clock_and_metadata_correlated=True,all_live_deadlines_independently_recomputed=False,
        native_timeout_cause_independently_observed=False,native_RAM_invocation_observed=False,native_apply_ACK=False)


def validate_timed_out(before, after, provider_before, provider, device, hold, held, release, released):
    validate_controls(hold, held, release, released); preserve_note(before, after)
    a,b = before['native'], after['native']; archived, upload = a['outbox']; job, = b['jobs']; attempt, = b['attempts']
    require(before['timeout_stage'] == 'associated' and after['timeout_stage'] == 'timed-out'
            and a['binding'] == b['binding'] and a['association'] == b['association']
            and all(not a[k] for k in ('jobs','attempts','heads','inbox','issuers','conflicts'))
            and type(provider_before['vault']['sequence']) is int and provider_before['vault']['sequence'] == 0
            and type(provider_before['vault']['used']) is int and provider_before['vault']['used']==0 and not revision_counts(provider_before)
            and all(not provider_before['rows'][k] for k in ('revisions','files','objects','uploads')),
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_FRESH_SCOPE_CHANGED')
    require(type(device) is str and re.fullmatch(r'[0-9a-f]{32}', device)
            and len(provider['rows']['revisions']) == 1 and type(provider['vault']['sequence']) is int and provider['vault']['sequence'] == 1
            and type(provider['vault']['used']) is int and provider['vault']['used']==len(BODY.encode())
            and provider['vault']['id'] == provider_before['vault']['id'] == b['binding']['remote_vault']
            and hold['operation_id'] not in (archived['operation_id'], upload['operation_id'])
            and release['operation_id'] not in (archived['operation_id'], upload['operation_id']), 'RELEASE_SYNC_TIMEOUT_ORIGINAL_VAULT_OR_ID_CHANGED')
    remote, = provider['rows']['revisions']
    require(type(remote) is dict and set(remote) == {'sequence','file_id','base_revision','path','operation','hash','size','device_id','operation_id','fingerprint'}
            and type(remote['fingerprint']) is str and re.fullmatch(r'[0-9a-f]{64}', remote['fingerprint'])
            and same_fields(remote, dict(operation_id=upload['operation_id'], file_id=upload['file_id'], base_revision=0, path=NOTE, operation='put',
                hash=HASH, size=len(BODY.encode()), sequence=1, device_id=device, fingerprint=remote['fingerprint']))
            and all(remote[k] == job[k] for k in ('operation_id','file_id','path','hash','size')),
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_REMOTE_COMMIT_CHANGED')
    require(b['outbox'] == [archived,dict(upload,state='queued')] and job['state'] == 'committing'
            and job['remote_revision'] is None and job['error'] is None and b['binding']['cursor'] == 0
            and attempt['outcome'] == 'failed'
            and same_fields(attempt, dict(operation_id=upload['operation_id'], attempts=1, outcome='failed', error='SYNC_NETWORK_ERROR'))
            and b['issuers'] == [dict(operation_id=upload['operation_id'],device_id=device)] and not b['heads'] and not b['inbox'] and not b['conflicts']
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id')),
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_NOT_NETWORK_FAILED_OR_PREMATURE_APPLY')
    require(provider['rows']['objects'] == [dict(hash=HASH,size=len(BODY.encode()))]
            and provider['rows']['files'] == [dict(file_id=upload['file_id'],sequence=1,path_key=NOTE,deleted=0)]
            and not provider['rows']['uploads'], 'RELEASE_SYNC_TIMEOUT_PROVIDER_ORIGINAL_BYTES_SCOPE')
    events = provider['delivery']['events']; require(type(events) is list and len(events) <= 256, 'RELEASE_SYNC_TIMEOUT_DELIVERY_BOUND')
    fault = [v for v in events if v['action'] == 'revision' or v['fault_operation'] == hold['operation_id']]
    require(fault == [dict(action='revision',service_status=200,delivery_status=None,fault_operation=hold['operation_id'])]
            and revision_counts(provider) == {'revision:200':1}, 'RELEASE_SYNC_TIMEOUT_ORIGINAL_REVISION_NOT_HELD_ONCE')
    return dict(remote, vault_id=provider['vault']['id'])


def validate_reconciled(before, after, provider_before, provider_after, receipt):
    preserve_note(before, after); a,b = before['native'],after['native']
    require(before['timeout_stage'] == 'timed-out-read' and after['timeout_stage'] == 'reconciled'
            and b['binding'] == dict(a['binding'],cursor=1)
            and all(b[k] == a[k] for k in ('association','attempts','issuers','conflicts'))
            and b['outbox'] == [a['outbox'][0],dict(a['outbox'][1],state='acked')]
            and b['jobs'] == [dict(a['jobs'][0],state='acked',remote_revision=1,error=None)]
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id','original_job_commit')),
            'RELEASE_SYNC_TIMEOUT_REPLACED_OR_RELABELLED_ORIGINAL')
    require(provider_after['vault'] == provider_before['vault']
            and all(provider_after['rows'][k] == provider_before['rows'][k] for k in ('revisions','files','objects','uploads'))
            and revision_counts(provider_before) == revision_counts(provider_after) == {'revision:200':1}
            and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')} ==
                {k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')}
            and provider_after['delivery']['counts'].get('changes:200',0) > provider_before['delivery']['counts'].get('changes:200',0)
            and [v for v in provider_after['delivery']['events'] if v['action'] != 'changes'] ==
                [v for v in provider_before['delivery']['events'] if v['action'] != 'changes'],
            'RELEASE_SYNC_TIMEOUT_ANOTHER_WRITE_OR_NO_ORIGINAL_CHANGES_READ')
    inbox, = b['inbox']; revision = revision_json(inbox['revision'])
    keys = ('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')
    require(type(revision) is dict and set(revision) == set(keys) and all(revision[k] == receipt[k] for k in keys)
            and inbox['state'] == 'applied' and b['heads'] == [dict(file_id=receipt['file_id'],revision=1,path=NOTE,hash=HASH)],
            'RELEASE_SYNC_TIMEOUT_ORIGINAL_NOT_APPLIED')
