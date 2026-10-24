"""One signed original commit response loss and explicit changes-read recovery."""
import json
import os
import uuid

from android_auth_fixture import read_json
from android_auth_runtime import active_intent, expect_requests, provider_account
from android_release_auth import main
from android_release_observation import APP, StoppedReleaseObserver, exclusive
from android_release_sync import ReleaseSync, validate_association, read_original_object
from android_release_sync_recovery_snapshot import NOTE, HASH, project_recovery, preserve_note, same_recovery
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import BODY, require
from android_sync_fixture import arm_control, control_root
from android_sync_host_touch import run_hosted_recovery_cycle
from android_sync_setup import Setup
from release_plan import atomic_json, digest

PHASES = ('cycle-drop-original', 'cycle-reconcile-original')


def validate_auth_provider(value, device, setup_device):
    expect_requests(value,2,0,0)
    account = provider_account(value,0)
    require(device != setup_device and {v['id'] for v in account['devices']} == {device,setup_device}
            and len(account['devices']) == 2
            and sorted(account['session_counts'],key=lambda v:v['device_id']) ==
                sorted([dict(device_id=device,sessions=1),dict(device_id=setup_device,sessions=1)],key=lambda v:v['device_id'])
            and not provider_account(value,1)['devices'] and not provider_account(value,1)['session_counts'],
            'RELEASE_SYNC_RECOVERY_AUTH_PROVIDER_IDENTITY')


def validate_unknown(before, after, provider_before, provider, device, control, marker):
    preserve_note(before,after); a,b = before['native'],after['native']
    original, = a['outbox']; job, = b['jobs']; attempt, = b['attempts']
    require(before['recovery_stage'] == 'associated' and after['recovery_stage'] == 'unknown'
            and a['binding'] == b['binding'] and a['association'] == b['association']
            and not provider_before['rows']['revisions'] and provider_before['vault']['sequence'] == 0
            and len(provider['rows']['revisions']) == 1 and provider['vault']['sequence'] == 1,
            'RELEASE_SYNC_RECOVERY_ORIGINAL_SCOPE_CHANGED')
    require(marker == dict(operation_id=control['operation_id'], mode='drop_revision', revision=control['result_revision'],
                action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
            and control['mode'] == 'drop_revision' and control['result_revision'] == control['expected_revision']+1,
            'RELEASE_SYNC_RECOVERY_ORIGINAL_DROP_NOT_OBSERVED')
    remote, = provider['rows']['revisions']; receipt = dict(remote,vault_id=provider['vault']['id'])
    require(provider['vault']['id'] == provider_before['vault']['id'] == b['binding']['remote_vault']
            and all(remote[k] == original[k] == job[k] for k in ('operation_id','file_id','path','hash'))
            and remote['base_revision'] == 0 and remote['operation'] == 'put' and remote['device_id'] == device
            and remote['size'] == job['size'] == len(BODY.encode()) and remote['hash'] == HASH
            and remote['sequence'] == 1 and b['issuers'] == [dict(operation_id=original['operation_id'],device_id=device)]
            and b['outbox'] == [dict(original,state='queued')] and job['state'] == 'committing'
            and job['remote_revision'] is None and attempt['outcome'] == 'failed' and attempt['attempts'] == 1,
            'RELEASE_SYNC_RECOVERY_CHANGED_OR_DUPLICATE_ORIGINAL')
    require(provider['rows']['objects'] == [dict(hash=HASH,size=len(BODY.encode()))]
            and provider['rows']['files'] == [dict(file_id=original['file_id'],sequence=1,path_key=NOTE,deleted=0)]
            and not provider['rows']['uploads'], 'RELEASE_SYNC_RECOVERY_ORIGINAL_PROVIDER_BYTES_SCOPE')
    events = [v for v in provider['delivery']['events'] if v['fault_operation'] == control['operation_id']]
    require(events == [dict(action='revision',service_status=200,delivery_status=503,fault_operation=control['operation_id'])]
            and provider['delivery']['counts'].get('revision:200') == 1
            and not any(k.startswith('revision:') for k in provider_before['delivery']['counts']),
            'RELEASE_SYNC_RECOVERY_ORIGINAL_REVISION_NOT_ONCE')
    return receipt


def validate_reconciled(before, after, provider_before, provider_after, receipt):
    preserve_note(before,after); a,b = before['native'],after['native']
    require(after['recovery_stage'] == 'reconciled' and b['binding'] == dict(a['binding'],cursor=1)
            and all(b[k] == a[k] for k in ('association','attempts','issuers','conflicts'))
            and b['outbox'] == [dict(a['outbox'][0],state='acked')]
            and b['jobs'] == [dict(a['jobs'][0],state='acked',remote_revision=1,error=None)]
            and before['original_job_commit'] == after['original_job_commit'],
            'RELEASE_SYNC_RECOVERY_REPLACED_OR_RELABELLED_ORIGINAL')
    require(provider_after['vault'] == provider_before['vault']
            and all(provider_after['rows'][k] == provider_before['rows'][k] for k in ('revisions','files','objects','uploads'))
            and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')} ==
                {k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')}
            and provider_after['delivery']['counts'].get('changes:200',0) > provider_before['delivery']['counts'].get('changes:200',0),
            'RELEASE_SYNC_RECOVERY_ANOTHER_WRITE_OR_NO_CHANGES_READ')
    inbox, = b['inbox']; revision = json.loads(inbox['revision'])
    keys = ('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')
    require(set(revision) == set(keys) and all(revision[k] == receipt[k] for k in keys)
            and inbox['state'] == 'applied' and b['heads'] == [dict(file_id=receipt['file_id'],revision=1,path=NOTE,hash=HASH)],
            'RELEASE_SYNC_RECOVERY_ORIGINAL_NOT_APPLIED')


class ReleaseSyncRecovery(ReleaseSync):
    allowed_phases = ReleaseSync.allowed_phases + PHASES

    def phase(self, phase, label):
        if phase not in PHASES: return super().phase(phase,label)
        require(self.driver_entry == 'ReleaseSyncRuntimeProbe','RELEASE_SYNC_RECOVERY_ENTRY_CHANGED')
        self.driver_entry = 'ReleaseSyncRecoveryRuntimeProbe'
        try: return run_hosted_recovery_cycle(self,phase,label)
        finally: self.driver_entry = 'ReleaseSyncRuntimeProbe'

    def recovery_snapshot(self,label,stage):
        observer = StoppedReleaseObserver(self.device,self.args.serial,self.output/'content-originals'/label,[NOTE])
        observer.identity = self.observer.identity
        require(observer.observe_identity() == self.identity,'RELEASE_SYNC_RECOVERY_OWNER_CHANGED')
        root = observer.discover_data_root(); observer.copy_stopped(root,'snapshot')
        raw = observer.output/'snapshot'
        value = project_recovery(raw,self.tools/('recovery-working-'+label),stage=stage)
        atomic_json(self.output/(label+'-content-projection.json'),value)
        body = (raw/'vault'/NOTE).read_bytes()
        require(body == BODY.encode(),'RELEASE_SYNC_RECOVERY_ORIGINAL_RAW_BYTES')
        return value,body

    def preserve_auth(self,label,auth,sealed):
        require(self.snapshot(label) == auth and self.observer.ciphertext_fingerprints() == sealed
                and self.observe(label) == self.recovery_auth_provider,
                'RELEASE_SYNC_RECOVERY_ORIGINAL_AUTH_CHANGED')

    def run(self):
        self.prepare()
        try:
            self.enable_test_ime(); ReleaseUpgrade.phase(self,'markdown','recovery-seed-note')
        finally: self.restore_test_ime()
        seeded,body = self.recovery_snapshot('seeded','seeded')
        self.phase('login-a','recovery-login'); auth = self.snapshot('recovery-login')
        require(len(auth['intents']) == 1,'RELEASE_SYNC_RECOVERY_ONE_LOGIN')
        login = auth['intents'][0]; active_intent(auth,'login',login['device_id'])
        sealed = self.observer.ciphertext_fingerprints()
        atomic_json(self.output/'recovery-original-cipher-fingerprints.json',sealed)
        setup = Setup(self.owner); prepared = setup.run(); atomic_json(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_RECOVERY_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']); self.phase('associate','recovery-association'); frozen = self.original_association()
        associated,source = self.recovery_snapshot('associated','associated'); preserve_note(seeded,associated)
        validate_association(frozen,associated['native'],prepared,login)
        self.recovery_auth_provider = self.observe('recovery-associated')
        validate_auth_provider(self.recovery_auth_provider,login['device_id'],prepared['device_id'])
        provider_before = self.sync_provider('associated')
        root = control_root(self.owner); current = read_json(root/'control.json'); operation = str(uuid.uuid4())
        atomic_json(self.output/'recovery-drop-control-intent.json',dict(operation_id=operation,mode='drop_revision',
            expected_revision=current['revision'],native_or_server_operation_id=False,automatic_retry=False),exclusive=True)
        control = arm_control(self.owner,operation,'drop_revision',current['revision'])
        atomic_json(self.output/'recovery-drop-control-receipt.json',control,exclusive=True)
        self.phase('cycle-drop-original','recovery-original-dropped')
        unknown,source = self.recovery_snapshot('unknown','unknown'); provider_unknown = self.sync_provider('unknown')
        marker = read_json(root/'fault-events'/(operation+'.json'))
        receipt = validate_unknown(associated,unknown,provider_before,provider_unknown,login['device_id'],control,marker)
        atomic_json(self.output/'recovery-original-dropped-response.json',marker,exclusive=True)
        self.preserve_auth('recovery-unknown-auth',auth,sealed)
        remote = read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token'])
        require(remote == body == source,'RELEASE_SYNC_RECOVERY_ORIGINAL_S3_BYTES')
        exclusive(self.output/('remote-original-'+NOTE),remote)
        previous_pid = self.phases[-1]['pid']; self.phase('status','recovery-unknown-read')
        require(self.phases[-1]['pid'] != previous_pid,'RELEASE_SYNC_RECOVERY_NEW_UNKNOWN_COLD_PID')
        readonly,source = self.recovery_snapshot('unknown-read','unknown-read'); same_recovery(unknown,readonly)
        require(self.sync_provider('unknown-read') == provider_unknown,'RELEASE_SYNC_RECOVERY_READ_CHANGED_PROVIDER')
        self.preserve_auth('recovery-unknown-read-auth',auth,sealed)
        exclusive(self.output/'recovery-explicit-original-continue-intent.json',json.dumps(dict(
            operation_id=receipt['operation_id'],file_id=receipt['file_id'],phase='cycle-reconcile-original',
            original_read_checked=True,automatic_retry=False)).encode())
        self.phase('cycle-reconcile-original','recovery-original-reconciled')
        reconciled,source = self.recovery_snapshot('reconciled','reconciled'); provider_after = self.sync_provider('reconciled')
        validate_reconciled(unknown,reconciled,provider_unknown,provider_after,receipt)
        require(source == body,'RELEASE_SYNC_RECOVERY_RECONCILE_CHANGED_BYTES')
        previous_pid = self.phases[-1]['pid']; self.phase('status','recovery-final-cold')
        require(self.phases[-1]['pid'] != previous_pid,'RELEASE_SYNC_RECOVERY_NEW_FINAL_COLD_PID')
        cold,source = self.recovery_snapshot('cold','cold'); same_recovery(reconciled,cold)
        require(source == body and self.sync_provider('cold') == provider_after,'RELEASE_SYNC_RECOVERY_FINAL_COLD_CHANGED')
        self.preserve_auth('recovery-final-auth',auth,sealed)
        result = dict(schema=1,passed=True,kind='android-signed-release-original-commit-read-recovery',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_Save_id=seeded['all_Save_ids'][0],original_login_id=login['operation_id'],Android_device=login['device_id'],
            independent_setup_device=prepared['device_id'],original_association=frozen,original_commit_receipt=receipt,original_drop_control=control,
            service_original_revision_POST_once_and_response_delivery_lost=True,original_failed_attempt_preserved=True,
            original_job_outbox_head_inbox_reconciled_by_explicit_changes_read=True,provider_revision_object_upload_state_unchanged_during_recovery=True,
            actual_S3_and_original_note_bytes_verified=True,private_auth_cipher_UI_or_finish_bytes_exported=False,
            decoded_image_verified=False,Native_RAM_invocation_id_observed=False,physical_phone_verified=False,
            actual_desktop_product_pair_verified=False,offline_Stop_conflict_worker_verified=False,whole_Sync_A4_complete=False,published=False)
        atomic_json(self.output/'sync-recovery-runtime-proof.json',result); return result


if __name__ == '__main__': raise SystemExit(main(ReleaseSyncRecovery))
