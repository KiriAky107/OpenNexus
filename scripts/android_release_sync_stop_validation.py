"""Original Stop/late release/read-reconcile facts; no GUI or network mutations."""
import re

from android_release_sync_stop_snapshot import NOTE, HASH, canonical, preserve_note, revision_json
from android_runtime_probe import BODY, require
from android_sync_stop_host import same_fields


def validate_controls(hold, held, release, released):
    require(same_fields(hold, dict(operation_id=hold['operation_id'], mode='hold_revision', expected_revision=0, result_revision=1))
            and same_fields(release, dict(operation_id=release['operation_id'], mode='release', expected_revision=1, result_revision=2))
            and canonical(hold['operation_id']) and canonical(release['operation_id']) and hold['operation_id'] != release['operation_id'],
            'RELEASE_SYNC_STOP_ORIGINAL_CONTROL_LINEAGE')
    require(same_fields(held, dict(operation_id=hold['operation_id'], mode='hold_revision', revision=1, action='revision',
                service_status=200, service_response_complete=True, raw_body_recorded=False))
            and type(released) is dict and set(released) == {'operation_id','original_response_send_attempted','ASGI_send_returned','client_receipt_known'}
            and released['operation_id'] == hold['operation_id'] and released['original_response_send_attempted'] is True
            and type(released['ASGI_send_returned']) is bool and released['client_receipt_known'] is False,
            'RELEASE_SYNC_STOP_ORIGINAL_HELD_RELEASE_MARKER')


def revision_counts(provider):
    counts = provider['delivery']['counts']
    require(type(counts) is dict and len(counts) <= 64 and all(type(k) is str and re.fullmatch(r'[a-z_]+:[0-9]{3}', k)
            and type(v) is int and 0 <= v <= 10000 for k,v in counts.items()), 'RELEASE_SYNC_STOP_REQUEST_COUNTER_SHAPE')
    return {k:v for k,v in counts.items() if k.startswith('revision:')}


def validate_stopped(before, after, provider_before, provider, device, hold, held, release, released):
    validate_controls(hold, held, release, released); preserve_note(before, after)
    a,b = before['native'], after['native']; archived, upload = a['outbox']; job, = b['jobs']; attempt, = b['attempts']
    require(before['stop_stage'] == 'associated' and after['stop_stage'] == 'stopped'
            and a['binding'] == b['binding'] and a['association'] == b['association']
            and all(not a[k] for k in ('jobs','attempts','heads','inbox','issuers','conflicts'))
            and provider_before['vault']['sequence'] == 0 and not revision_counts(provider_before)
            and all(not provider_before['rows'][k] for k in ('revisions','files','objects','uploads')),
            'RELEASE_SYNC_STOP_ORIGINAL_FRESH_SCOPE_CHANGED')
    require(type(device) is str and re.fullmatch(r'[0-9a-f]{32}', device)
            and len(provider['rows']['revisions']) == 1 and provider['vault']['sequence'] == 1
            and provider['vault']['id'] == provider_before['vault']['id'] == b['binding']['remote_vault']
            and hold['operation_id'] not in (archived['operation_id'], upload['operation_id'])
            and release['operation_id'] not in (archived['operation_id'], upload['operation_id']), 'RELEASE_SYNC_STOP_ORIGINAL_VAULT_OR_ID_CHANGED')
    remote, = provider['rows']['revisions']
    require(type(remote) is dict and set(remote) == {'sequence','file_id','base_revision','path','operation','hash','size','device_id','operation_id','fingerprint'}
            and type(remote['fingerprint']) is str and re.fullmatch(r'[0-9a-f]{64}', remote['fingerprint'])
            and same_fields(remote, dict(operation_id=upload['operation_id'], file_id=upload['file_id'], base_revision=0, path=NOTE, operation='put',
                hash=HASH, size=len(BODY.encode()), sequence=1, device_id=device, fingerprint=remote['fingerprint']))
            and all(remote[k] == job[k] for k in ('operation_id','file_id','path','hash','size')),
            'RELEASE_SYNC_STOP_ORIGINAL_REMOTE_COMMIT_CHANGED')
    require(b['outbox'] == [archived,dict(upload,state='queued')] and job['state'] == 'committing'
            and job['remote_revision'] is None and job['error'] is None and b['binding']['cursor'] == 0
            and same_fields(attempt, dict(operation_id=upload['operation_id'], attempts=1, outcome='interrupted', error=None))
            and b['issuers'] == [dict(operation_id=upload['operation_id'],device_id=device)] and not b['heads'] and not b['inbox'] and not b['conflicts']
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id')),
            'RELEASE_SYNC_STOP_ORIGINAL_NOT_INTERRUPTED_OR_PREMATURE_APPLY')
    require(provider['rows']['objects'] == [dict(hash=HASH,size=len(BODY.encode()))]
            and provider['rows']['files'] == [dict(file_id=upload['file_id'],sequence=1,path_key=NOTE,deleted=0)]
            and not provider['rows']['uploads'], 'RELEASE_SYNC_STOP_PROVIDER_ORIGINAL_BYTES_SCOPE')
    events = provider['delivery']['events']; require(type(events) is list and len(events) <= 256, 'RELEASE_SYNC_STOP_DELIVERY_BOUND')
    fault = [v for v in events if v['action'] == 'revision' or v['fault_operation'] == hold['operation_id']]
    require(fault == [dict(action='revision',service_status=200,delivery_status=None,fault_operation=hold['operation_id'])]
            and revision_counts(provider) == {'revision:200':1}, 'RELEASE_SYNC_STOP_ORIGINAL_REVISION_NOT_HELD_ONCE')
    return dict(remote, vault_id=provider['vault']['id'])


def validate_reconciled(before, after, provider_before, provider_after, receipt):
    preserve_note(before, after); a,b = before['native'],after['native']
    require(before['stop_stage'] == 'stopped-read' and after['stop_stage'] == 'reconciled'
            and b['binding'] == dict(a['binding'],cursor=1)
            and all(b[k] == a[k] for k in ('association','attempts','issuers','conflicts'))
            and b['outbox'] == [a['outbox'][0],dict(a['outbox'][1],state='acked')]
            and b['jobs'] == [dict(a['jobs'][0],state='acked',remote_revision=1,error=None)]
            and all(before[k] == after[k] for k in ('original_Save_outbox_id','original_association_upload_id','original_job_commit')),
            'RELEASE_SYNC_STOP_REPLACED_OR_RELABELLED_ORIGINAL')
    require(provider_after['vault'] == provider_before['vault']
            and all(provider_after['rows'][k] == provider_before['rows'][k] for k in ('revisions','files','objects','uploads'))
            and revision_counts(provider_before) == revision_counts(provider_after) == {'revision:200':1}
            and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')} ==
                {k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')}
            and provider_after['delivery']['counts'].get('changes:200',0) > provider_before['delivery']['counts'].get('changes:200',0)
            and [v for v in provider_after['delivery']['events'] if v['action'] != 'changes'] ==
                [v for v in provider_before['delivery']['events'] if v['action'] != 'changes'],
            'RELEASE_SYNC_STOP_ANOTHER_WRITE_OR_NO_ORIGINAL_CHANGES_READ')
    inbox, = b['inbox']; revision = revision_json(inbox['revision'])
    keys = ('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')
    require(type(revision) is dict and set(revision) == set(keys) and all(revision[k] == receipt[k] for k in keys)
            and inbox['state'] == 'applied' and b['heads'] == [dict(file_id=receipt['file_id'],revision=1,path=NOTE,hash=HASH)],
            'RELEASE_SYNC_STOP_ORIGINAL_NOT_APPLIED')
