"""An explicit clock for one owned hosted Sync service, never a production entry.

The fixed production factory/provider make the actual expiry decisions. This
fixture changes no session rows, returns no fabricated HTTP error and exposes
no HTTP clock-control route. Advancing fixture time is not elapsed wall time.
"""
from pathlib import Path
import argparse,json,os,time,uuid
import android_auth_fixture as auth
from android_auth_fixture import atomic_json,read_json,require

OFFSETS={'normal':0,'access_expired':901,'refresh_expired':31*86400}
REVISIONS={'normal':0,'access_expired':1,'refresh_expired':2}


class OwnedClock:
    def __init__(self,owner,*,now=time.time):
        require(owner.resolve()==auth.owned_root(),'OWNED_EXPIRY_ROOT_INVALID')
        self.root=owner/'expiry-clock';self.now=now
        require(self.root.is_dir() and not self.root.is_symlink(),'OWNED_EXPIRY_NOT_PREPARED')

    @staticmethod
    def initialize(owner):
        require(owner.resolve()==auth.owned_root(),'OWNED_EXPIRY_ROOT_INVALID')
        root=owner/'expiry-clock'
        require(not root.exists() and not root.is_symlink(),'OWNED_EXPIRY_ALREADY_PREPARED')
        root.mkdir(mode=0o700);auth.initialize_control(root)

    def state(self):
        value=read_json(self.root/'control.json')
        require(set(value)=={'revision','operation_id','mode'} and value['mode'] in OFFSETS
            and type(value['revision']) is int and value['revision']==REVISIONS[value['mode']],
            'OWNED_EXPIRY_CLOCK_INVALID')
        require(value['operation_id'] is None if value['revision']==0
            else type(value['operation_id']) is str and str(uuid.UUID(value['operation_id']))==value['operation_id'],
            'OWNED_EXPIRY_CLOCK_INVALID')
        return value

    def __call__(self):return self.now()+OFFSETS[self.state()['mode']]

    def advance(self,mode,operation,expected_revision):
        require(mode in ('access_expired','refresh_expired') and type(expected_revision) is int
            and expected_revision==REVISIONS[mode]-1,'OWNED_EXPIRY_TRANSITION_INVALID')
        receipt=auth.arm_control(self.root,operation,mode,expected_revision,allowed_modes=set(OFFSETS))
        current=self.state()
        require(current['operation_id']==operation and current['mode']==mode,'OWNED_EXPIRY_CLOCK_CHANGED')
        return dict(receipt,offset_seconds=OFFSETS[mode],actual_elapsed_time_claimed=False,
            clock_control_only_no_session_SQL_or_HTTP=True)


def run_expiration(probe,setup,prepared,device):
    from android_auth_fixture import observe_provider as observe_auth
    from android_sync_rejection import validate_refresh_local,validate_rejected_sync,validate_rejected_refresh,original_driver_intent
    from android_runtime_probe import DRAFT
    require(read_json(probe.owner/'owner-state.json')['fixture']=='sync-expiration','OWNED_EXPIRY_FIXTURE_INVALID')
    clock=OwnedClock(probe.owner);require(clock.state()['revision']==0,'OWNED_EXPIRY_ALREADY_ADVANCED')
    original=probe.original('runtime-note.md');require(original==DRAFT.encode(),'Original fresh expiry note differs')
    native=probe.host_snapshot('expiry-before',include_outbox=True,include_payloads=True)
    provider=probe.observe('expiry-before');accounts=observe_auth(probe.owner)
    login=probe.snapshot('expiry-login',include_rejections=True);sealed=probe.ciphertext_fingerprints()
    require(bool(sealed),'Actual login ciphertext is absent')
    validate_refresh_local(native,native,original,original)
    def unchanged_accounts(label,expected):
        current=observe_auth(probe.owner);atomic_json(probe.output/(label+'-accounts.json'),current,exclusive=True)
        require(current['users']==accounts['users'],'Expiry changed an original device, session count or account')
        require(current['requests']==expected,'Original auth request counts differ')
        return current
    atomic_json(probe.output/'expiry-before-accounts.json',accounts,exclusive=True)
    controls=[]
    def advance(mode,revision):
        operation=str(uuid.uuid4())
        atomic_json(probe.output/('expiry-'+mode+'-intent.json'),dict(operation_id=operation,mode=mode,
            expected_revision=revision,automatic_retry=False,session_SQL_changed=False),exclusive=True)
        receipt=clock.advance(mode,operation,revision);controls.append(receipt)
        atomic_json(probe.output/('expiry-'+mode+'-receipt.json'),receipt,exclusive=True)
    advance('access_expired',0)
    # Existing same-message driver phases are UI actions, not the cause of the
    # rejection: the independent service clock is retained above as its cause.
    probe.sync_phase('cycle-revoked');cycle=original_driver_intent(probe,'cycle-revoked')
    atomic_json(probe.output/'expiry-cycle-original-driver-intent.json',cycle,exclusive=True)
    after=probe.host_snapshot('expiry-after-cycle',include_outbox=True,include_payloads=True)
    remote=probe.observe('expiry-after-cycle')
    preserved=validate_rejected_sync(native,after,provider,remote,original,probe.original('runtime-note.md'))
    unchanged_accounts('expiry-after-cycle',accounts['requests'])
    spool=probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout
    require(spool==original,'Access expiry changed original spool bytes')
    advance('refresh_expired',1)
    probe.sync_phase('refresh-revoked');refresh_intent=original_driver_intent(probe,'refresh-revoked')
    atomic_json(probe.output/'expiry-refresh-original-driver-intent.json',refresh_intent,exclusive=True)
    rejected=probe.snapshot('expiry-refresh-rejected',include_rejections=True)
    refresh=validate_rejected_refresh(login,rejected,device)
    counts=dict(accounts['requests']['counts']);counts['refresh:401']=counts.get('refresh:401',0)+1
    events=accounts['requests']['events']+[dict(action='refresh',service_status=401,delivery_status=401,fault_operation=None)]
    unchanged_accounts('expiry-after-refresh',dict(counts=counts,events=events))
    require(probe.ciphertext_fingerprints()==sealed and probe.observe('expiry-after-refresh')==remote,
        'Refresh expiry replaced sealed credentials or wrote provider content')
    probe.auth_phase('current-unusable',cold=True)
    require(probe.snapshot('expiry-cold',include_rejections=True)==rejected
        and probe.host_snapshot('expiry-cold',include_outbox=True,include_payloads=True)==after
        and probe.original('runtime-note.md')==original
        and probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout==spool,
        'Cold rejection changed original local state or activated expired credentials')
    (probe.output/'expiry-original-note.md').write_bytes(original)
    atomic_json(probe.output/'sync-expiration-proof.json',dict(passed=True,run_id=os.environ['GITHUB_RUN_ID'],
        tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
        service_source=prepared['source'],phases=probe.phases,original_local=preserved,clock_controls=controls,
        original_driver_intents=[cycle,refresh_intent],original_refresh_operation_id=refresh,
        actual_service_access401_and_refresh401=True,actual_Native_rejection_and_cold_preservation=True,
        original_spool_bytes_and_ciphertext_preserved=True,no_device_revocation_or_session_SQL=True,
        actual_elapsed_time_claimed=False,whole_A4_or_physical_or_release_complete=False),exclusive=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('seed','serve'));parser.add_argument('--owner-run');args=parser.parse_args()
    owner=auth.owned_root();require(owner.is_dir(),'OWNED_EXPIRY_NOT_PREPARED')
    if args.action=='seed':auth.main();return
    require(args.owner_run==os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'],'OWNED_AUTH_RUN_INVALID')
    auth.fixed_service(owner);clock=OwnedClock(owner)
    require(clock.state()['revision']==0,'OWNED_EXPIRY_ALREADY_ADVANCED')
    from sync_server.__main__ import Database,S3Objects,create_app,operations_path
    from android_sync_fixture import TargetedDeliveryProxy,control_root
    control_root(owner)
    app=create_app(Database(os.environ['SYNC_DATABASE_URL']),
        S3Objects(os.environ['SYNC_S3_ENDPOINT'],os.environ['SYNC_S3_BUCKET']),Path(os.environ['SYNC_STAGING_DIR']),
        operations_path=operations_path(),operator_user_ids=(),clock=clock)
    import uvicorn
    uvicorn.run(auth.DeliveryProxy(TargetedDeliveryProxy(app,owner),owner),host='127.0.0.1',port=19073,
        workers=1,access_log=False,log_level='warning')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_EXPIRY_FAILED_'+type(error).__name__,file=__import__('sys').stderr);raise SystemExit(1)
