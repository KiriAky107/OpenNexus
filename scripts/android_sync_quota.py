"""A fresh owned real 64-byte vault; no fabricated quota error or session SQL."""
from copy import deepcopy
from pathlib import Path
import argparse,hashlib,json,os
import android_auth_fixture as auth
from android_auth_fixture import atomic_json,read_json,require
QUOTA=64


def validate_quota_rejection(before,after,provider,remote,device,original,observed):
    from android_sync_rejection import validate_refresh_local
    preserved=validate_refresh_local(before,before,original,observed)
    operation=preserved['operation_id'];file=preserved['file_id'];digest=preserved['sha256']
    expected=deepcopy(before)
    expected['jobs']=[dict(operation_id=operation,file_id=file,path='runtime-note.md',hash=digest,
        size=len(original),state='pending',remote_revision=None,error=None)]
    expected['attempts']=[dict(operation_id=operation,attempts=1,outcome='failed',error='QUOTA_EXCEEDED')]
    expected['issuers']=[dict(operation_id=operation,device_id=device)]
    for row in expected['outbox']:
        if row['operation_id']==operation:row.update(state='queued',payload_size=0)
    require(after==expected,'Quota rejection replaced or acknowledged an original local operation')
    require(provider['vault']['quota']==QUOTA<len(original) and provider['vault']['used']==provider['vault']['sequence']==0,
        'Quota case is not the declared new actual capacity boundary')
    require(all(not provider['rows'][key] for key in ('revisions','files','objects','uploads')),'Quota vault was not empty')
    previous={r['device_id']:r for r in provider['rows']['device_progress']}
    current={r['device_id']:r for r in remote['rows']['device_progress']}
    require(len(previous)==len(provider['rows']['device_progress']) and len(current)==len(remote['rows']['device_progress'])
        and set(current)==set(previous)|{device},'Quota progress changed another device identity')
    require(all(current[k]==v for k,v in previous.items() if k!=device),'Quota changed another device progress')
    offered=current[device];prior=previous.get(device)
    require(set(offered)=={'device_id','committed_sequence','committed_at','offered_sequence','offered_at'}
        and offered['committed_sequence'] is None and offered['committed_at'] is None
        and offered['offered_sequence']==0 and type(offered['offered_at']) is int and offered['offered_at']>0,
        'A generated empty page was misrepresented as a client apply or committed revision')
    if prior:
        require(prior['committed_sequence'] is None and prior['committed_at'] is None
            and prior['offered_sequence']==0 and offered['offered_at']>=prior['offered_at'],'Quota altered the original offered boundary')
    expected_remote=deepcopy(provider);expected_remote['rows']['device_progress']=remote['rows']['device_progress']
    counts=expected_remote['delivery']['counts']
    for key in ('changes:200','upload:413'):counts[key]=counts.get(key,0)+1
    expected_remote['delivery']['events'] += [dict(action=kind,service_status=status,delivery_status=status,fault_operation=None)
        for kind,status in (('changes',200),('upload',413))]
    require(len(expected_remote['delivery']['events'])<=64 and remote==expected_remote,
        'Actual quota413 is absent or original provider content/reservations were changed')
    return dict(preserved,actual_failed_attempt_operation_id=operation,actual_service_quota=QUOTA,
        original_pending_job_and_payload_retained=True,unchanged_native_boundary=False,
        initial_no_job_boundary_validated=True,actual_original_pending_job_and_payload_retained=True,
        generated_empty_page_is_not_client_ACK=True)


def run_quota(probe,setup,prepared,device):
    from android_sync_rejection import original_driver_intent
    from android_runtime_probe import DRAFT
    require(read_json(probe.owner/'owner-state.json')['fixture']=='sync-quota','OWNED_QUOTA_FIXTURE_INVALID')
    original=probe.original('runtime-note.md');require(original==DRAFT.encode(),'Original quota note differs')
    before=probe.host_snapshot('quota-before',include_outbox=True,include_payloads=True)
    provider=probe.observe('quota-before');login=probe.snapshot('quota-login',include_rejections=True)
    sealed=probe.ciphertext_fingerprints();require(bool(sealed),'Actual login ciphertext is absent')
    atomic_json(probe.output/'quota-scene-intent.json',dict(actual_default_quota=QUOTA,note_bytes=len(original),
        original_operation_ids=[r['operation_id'] for r in before['outbox']],automatic_retry=False,
        factory_default_is_declared_fixture_configuration=True,provider_SQL_or_response_fabrication=False),exclusive=True)
    probe.sync_phase('cycle-quota');driver=original_driver_intent(probe,'cycle-quota')
    atomic_json(probe.output/'quota-original-driver-intent.json',driver,exclusive=True)
    after=probe.host_snapshot('quota-after-cycle',include_outbox=True,include_payloads=True);remote=probe.observe('quota-after-cycle')
    preserved=validate_quota_rejection(before,after,provider,remote,device,original,probe.original('runtime-note.md'))
    spool=probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout
    require(spool==original and probe.snapshot('quota-after-cycle',include_rejections=True)==login
        and probe.ciphertext_fingerprints()==sealed,'Quota failure changed original bytes or authentication')
    probe.force_stop();probe.sync_phase('quota-status')
    require(probe.host_snapshot('quota-cold',include_outbox=True,include_payloads=True)==after
        and probe.observe('quota-cold')==remote and probe.snapshot('quota-cold',include_rejections=True)==login
        and probe.original('runtime-note.md')==original
        and probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout==spool,
        'Cold readonly quota status retried or replaced an original operation')
    (probe.output/'quota-original-note.md').write_bytes(original)
    atomic_json(probe.output/'sync-quota-proof.json',dict(passed=True,run_id=os.environ['GITHUB_RUN_ID'],
        tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
        service_source=prepared['source'],phases=probe.phases,original_local=preserved,original_driver_intent=driver,
        actual_reservation_service413=True,actual_native_known_failed_attempt_and_cold_readonly=True,
        original_spool_bytes_and_ciphertext_preserved=True,no_quota_increase_or_retry_or_new_Save=True,
        physical_invalid_certificate_or_whole_A4_complete=False),exclusive=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('seed','serve'));parser.add_argument('--owner-run');args=parser.parse_args()
    owner=auth.owned_root();require(owner.is_dir(),'OWNED_QUOTA_NOT_PREPARED')
    if args.action=='seed':auth.main();return
    require(args.owner_run==os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'],'OWNED_AUTH_RUN_INVALID')
    auth.fixed_service(owner)
    from sync_server.__main__ import Database,S3Objects,create_app,operations_path
    from android_sync_fixture import TargetedDeliveryProxy,control_root
    control_root(owner)
    app=create_app(Database(os.environ['SYNC_DATABASE_URL']),
        S3Objects(os.environ['SYNC_S3_ENDPOINT'],os.environ['SYNC_S3_BUCKET']),Path(os.environ['SYNC_STAGING_DIR']),
        quota=QUOTA,operations_path=operations_path(),operator_user_ids=())
    import uvicorn
    uvicorn.run(auth.DeliveryProxy(TargetedDeliveryProxy(app,owner,uploads=True),owner),host='127.0.0.1',port=19073,
        workers=1,access_log=False,log_level='warning')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_QUOTA_FAILED_'+type(error).__name__,file=__import__('sys').stderr);raise SystemExit(1)
