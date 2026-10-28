"""Original signed held-response network failure and explicit same-record read."""
import os

from android_auth_runtime import active_intent
from android_release_observation import APP,exclusive
from android_release_sync import ReleaseSync,validate_association,read_original_object
from android_release_sync_recovery import validate_auth_provider
from android_release_sync_timeout_lifecycle import collect_original,main,run_guarded,stop_completed_phase
from android_release_sync_timeout_snapshot import NOTE,preserve_initial_outbox,preserve_read
from android_release_sync_timeout_validation import validate_timed_out,validate_read,validate_reconciled
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import BODY,require
from android_sync_setup import Setup
from android_sync_timeout_cleanup import OriginalTimeoutControl,LABEL
from android_sync_timeout_host import ENTRY,TIMEOUT,RESUME,run_hosted_timeout_cycle
from android_sync_stop_host import closed_json,record
from release_plan import digest

PHASES=(TIMEOUT,RESUME)


class ReleaseSyncTimeout(ReleaseSync):
    allowed_phases=ReleaseSync.allowed_phases+PHASES

    def phase(self,phase,label):
        if phase not in PHASES:return super().phase(phase,label)
        require(self.driver_entry=='ReleaseSyncRuntimeProbe','RELEASE_SYNC_TIMEOUT_ENTRY_CHANGED')
        held=self.timeout_control.held if phase==TIMEOUT else None
        self.driver_entry=ENTRY
        try:return run_hosted_timeout_cycle(self,phase,label,held=held)
        finally:self.driver_entry='ReleaseSyncRuntimeProbe'

    def preserve_auth(self,label,auth,sealed):
        actual=self.snapshot(label);current=self.observer.ciphertext_fingerprints()
        record(self.output/(label+'-cipher-fingerprints.json'),current)
        require(actual==auth and current==sealed and self.observe(label)==self.timeout_auth_provider,
                'RELEASE_SYNC_TIMEOUT_ORIGINAL_AUTH_CHANGED')

    def run(self):
        self.timeout_stage='prepare';self.timeout_control=None
        self.timeout_originals_preserved=False;self.timeout_read_verified=False
        return run_guarded(self,self.run_original)

    def run_original(self):
        self.prepare();self.timeout_stage='original-note-Save'
        try:
            self.enable_test_ime();ReleaseUpgrade.phase(self,'markdown','timeout-seed-note')
        finally:self.restore_test_ime()
        seeded,body=collect_original(self,'seeded',stage='seeded')
        require(body==BODY.encode(),'RELEASE_SYNC_TIMEOUT_SEEDED_NOTE_BYTES')
        self.timeout_stage='original-login'
        self.phase('login-a','timeout-login');auth=self.snapshot('timeout-login')
        require(len(auth['intents'])==1,'RELEASE_SYNC_TIMEOUT_ONE_LOGIN')
        login=auth['intents'][0];active_intent(auth,'login',login['device_id'])
        sealed=self.observer.ciphertext_fingerprints();record(self.output/'timeout-original-cipher-fingerprints.json',sealed)
        self.timeout_stage='independent-setup-and-association'
        setup=Setup(self.owner);prepared=setup.run();record(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_TIMEOUT_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']);self.phase('associate','timeout-association');frozen=self.original_association()
        associated,source=collect_original(self,'associated',stage='associated');preserve_initial_outbox(seeded,associated)
        validate_association(frozen,associated['native'],prepared,login)
        self.timeout_auth_provider=self.observe('timeout-associated')
        validate_auth_provider(self.timeout_auth_provider,login['device_id'],prepared['device_id'])
        provider_before=self.sync_provider('associated')
        self.timeout_stage='original-hold-control'
        self.timeout_control=OriginalTimeoutControl(self.owner,self.output.absolute(),prepared['vault']['vault_id'])
        hold=self.timeout_control.arm()
        self.timeout_stage='original-timeout-GUI'
        self.phase(TIMEOUT,LABEL)
        # The timeout host preserves the live product until its exact driver
        # completes. This distinct action validates that completion and original
        # held/clock/nonce journals before the once-only stopped snapshot.
        self.timeout_stage='timed-out-snapshot-stop';stop_completed_phase(self)
        self.timeout_stage='timed-out-original-projection'
        timed_out,source=collect_original(self,'timed-out',stage='timed-out')
        held=self.timeout_control.held
        require(held.marker is not None and held.release_control is not None,'RELEASE_SYNC_TIMEOUT_ORIGINAL_RELEASE_MISSING')
        released=held.read_released();require(released is not None,'RELEASE_SYNC_TIMEOUT_ORIGINAL_RELEASE_MARKER_MISSING')
        record(self.output/'timeout-original-held-response.json',held.marker)
        record(self.output/'timeout-original-release-response.json',released)
        provider_failed=self.sync_provider('timed-out')
        receipt=validate_timed_out(associated,timed_out,provider_before,provider_failed,login['device_id'],hold,held.marker,held.release_control,released)
        remote=read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token'])
        require(remote==body==source,'RELEASE_SYNC_TIMEOUT_ORIGINAL_S3_BYTES')
        exclusive(self.output/('remote-original-'+NOTE),remote)
        self.preserve_auth('timeout-failed-auth',auth,sealed)
        self.timeout_stage='timed-out-new-PID-read'
        previous_pid=self.phases[-1]['pid'];self.phase('status','timeout-failed-read')
        require(self.phases[-1]['pid']!=previous_pid,'RELEASE_SYNC_TIMEOUT_NEW_READ_PID')
        opened,source=collect_original(self,'timed-out-read',stage='timed-out-read')
        validate_read(timed_out,opened,provider_failed,self.sync_provider('timed-out-read'))
        self.preserve_auth('timeout-failed-read-auth',auth,sealed)
        self.timeout_read_verified=True
        self.timeout_stage='explicit-original-changes-reconcile'
        record(self.output/'timeout-explicit-original-continue-intent.json',dict(operation_id=receipt['operation_id'],file_id=receipt['file_id'],
            original_hold_operation_id=hold['operation_id'],phase=RESUME,original_failed_and_new_PID_read_checked=True,automatic_retry=False))
        self.phase(RESUME,'timeout-original-reconciled')
        self.timeout_stage='reconciled-snapshot-stop';stop_completed_phase(self)
        reconciled,source=collect_original(self,'reconciled',stage='reconciled');provider_after=self.sync_provider('reconciled')
        validate_reconciled(opened,reconciled,provider_failed,provider_after,receipt)
        require(source==body,'RELEASE_SYNC_TIMEOUT_RECONCILE_CHANGED_BYTES')
        self.preserve_auth('timeout-reconciled-auth',auth,sealed)
        self.timeout_stage='final-new-PID-cold-read'
        previous_pid=self.phases[-1]['pid'];self.phase('status','timeout-final-cold')
        require(self.phases[-1]['pid']!=previous_pid,'RELEASE_SYNC_TIMEOUT_NEW_FINAL_COLD_PID')
        cold,source=collect_original(self,'cold',stage='cold');preserve_read(reconciled,cold)
        require(source==body and self.sync_provider('cold')==provider_after,'RELEASE_SYNC_TIMEOUT_FINAL_COLD_CHANGED')
        self.preserve_auth('timeout-final-auth',auth,sealed)
        correlation=closed_json((self.output/'timeout-original-host-correlated.json').read_bytes())
        result=dict(schema=1,passed=True,kind='android-signed-release-original-held-network-failure-read-recovery',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_Save_id=seeded['all_Save_ids'][0],original_association_upload_id=associated['original_association_upload_id'],
            original_Save_outbox_archived_and_preserved=True,original_login_id=login['operation_id'],Android_device=login['device_id'],
            independent_setup_device=prepared['device_id'],original_association=frozen,original_commit_receipt=receipt,
            original_hold_control=hold,original_release_control=held.release_control,original_host_metadata=correlation,
            original_failed_attempt_count_and_SYNC_NETWORK_ERROR_preserved=True,
            held_service200_and_failed_UI_clock_chain_correlated=True,native_timeout_cause_independently_observed=False,
            all_live_host_deadlines_independently_recomputed=False,
            service_original_revision_POST_once_and_held_response_explicitly_released=True,client_receipt_known=False,
            original_job_outbox_head_inbox_reconciled_by_explicit_changes_read=True,
            provider_revision_object_upload_state_unchanged_during_recovery=True,actual_S3_and_original_note_bytes_verified=True,
            private_auth_cipher_UI_or_finish_bytes_exported=False,decoded_image_verified=False,Native_RAM_invocation_id_observed=False,
            physical_phone_verified=False,actual_desktop_product_pair_verified=False,full_native_worker_recovery_verified=False,
            device_disconnection_verified=False,offline_timeout_conflict_verified=False,whole_Sync_A4_complete=False,published=False)
        record(self.output/'sync-timeout-runtime-proof.json',result);return result


if __name__=='__main__':raise SystemExit(main(ReleaseSyncTimeout))
