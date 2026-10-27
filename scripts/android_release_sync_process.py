"""Original signed process loss, raw preopen state and explicit same-record read."""
import os

from android_auth_runtime import active_intent
from android_release_observation import APP,exclusive
from android_release_sync import ReleaseSync,validate_association,read_original_object
from android_release_sync_recovery import validate_auth_provider
from android_release_sync_process_lifecycle import collect_original,main,run_guarded,stop_completed_read
from android_release_sync_process_snapshot import NOTE,preserve_initial_outbox,preserve_read
from android_release_sync_process_validation import validate_killed,validate_lifecycle,validate_open_read,validate_reconciled
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import BODY,require
from android_sync_setup import Setup
from android_sync_process_cleanup import OriginalProcessControl,LABEL
from android_sync_process_host import ENTRY,LOSS,RESUME,run_hosted_process_cycle
from android_sync_stop_host import closed_json,record
from release_plan import digest

PHASES=(LOSS,RESUME)


class ReleaseSyncProcess(ReleaseSync):
    allowed_phases=ReleaseSync.allowed_phases+PHASES

    def phase(self,phase,label):
        if phase not in PHASES:return super().phase(phase,label)
        require(self.driver_entry=='ReleaseSyncRuntimeProbe','RELEASE_SYNC_PROCESS_ENTRY_CHANGED')
        held=self.process_control.held if phase==LOSS else None
        self.driver_entry=ENTRY
        try:return run_hosted_process_cycle(self,phase,label,held=held)
        finally:self.driver_entry='ReleaseSyncRuntimeProbe'

    def preserve_auth(self,label,auth,sealed):
        actual=self.snapshot(label);current=self.observer.ciphertext_fingerprints()
        record(self.output/(label+'-cipher-fingerprints.json'),current)
        require(actual==auth and current==sealed and self.observe(label)==self.process_auth_provider,
                'RELEASE_SYNC_PROCESS_ORIGINAL_AUTH_CHANGED')

    def original_lifecycle(self,killed,hold):
        phase=self.phases[-1]
        require(phase['phase']==LOSS and phase['label']==LABEL and killed['owned_snapshot_identity']==self.identity,
                'RELEASE_SYNC_PROCESS_KILLED_OWNER_OR_PHASE')
        def original(suffix):
            path=self.output/(LABEL+'-'+suffix+'.json')
            require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=4096,
                    'RELEASE_SYNC_PROCESS_ORIGINAL_LIFECYCLE_FILE')
            return closed_json(path.read_bytes())
        result=validate_lifecycle(phase,original('original-parent'),original('ready'),original('loss-ready'),
            original('lifecycle-intent'),original('lifecycle-result'),original('lifecycle-owner'),
            original('original-process-absence'),original('final-original-process-absence'),self.identity,hold['operation_id'])
        record(self.output/'process-original-lifecycle-correlated.json',result)
        return result

    def run(self):
        self.process_stage='prepare';self.process_control=None;self.process_raw_killed_preserved=False
        return run_guarded(self,self.run_original)

    def run_original(self):
        self.prepare();self.process_stage='original-note-Save'
        try:
            self.enable_test_ime();ReleaseUpgrade.phase(self,'markdown','process-seed-note')
        finally:self.restore_test_ime()
        seeded,body=collect_original(self,'seeded',stage='seeded')
        require(body==BODY.encode(),'RELEASE_SYNC_PROCESS_SEEDED_NOTE_BYTES')
        self.process_stage='original-login'
        self.phase('login-a','process-login');auth=self.snapshot('process-login')
        require(len(auth['intents'])==1,'RELEASE_SYNC_PROCESS_ONE_LOGIN')
        login=auth['intents'][0];active_intent(auth,'login',login['device_id'])
        sealed=self.observer.ciphertext_fingerprints();record(self.output/'process-original-cipher-fingerprints.json',sealed)
        self.process_stage='independent-setup-and-association'
        setup=Setup(self.owner);prepared=setup.run();record(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_PROCESS_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']);self.phase('associate','process-association');frozen=self.original_association()
        associated,source=collect_original(self,'associated',stage='associated');preserve_initial_outbox(seeded,associated)
        validate_association(frozen,associated['native'],prepared,login)
        self.process_auth_provider=self.observe('process-associated')
        validate_auth_provider(self.process_auth_provider,login['device_id'],prepared['device_id'])
        provider_before=self.sync_provider('associated')
        self.process_stage='original-hold-control'
        self.process_control=OriginalProcessControl(self.owner,self.output.absolute(),prepared['vault']['vault_id'])
        hold=self.process_control.arm()
        self.process_stage='original-process-loss-GUI'
        self.phase(LOSS,LABEL)
        # This is the FIRST observation after the host returns. No auth copy,
        # status/open, stop, provider query or generic failure hook precedes it.
        self.process_stage='killed-original-projection'
        killed,source=collect_original(self,'killed',stage='killed')
        lifecycle=self.original_lifecycle(killed,hold)
        held=self.process_control.held
        require(held.marker is not None and held.release_control is not None,'RELEASE_SYNC_PROCESS_ORIGINAL_RELEASE_MISSING')
        released=held.read_released();require(released is not None,'RELEASE_SYNC_PROCESS_ORIGINAL_RELEASE_MARKER_MISSING')
        record(self.output/'process-original-held-response.json',held.marker)
        record(self.output/'process-original-release-response.json',released)
        provider_killed=self.sync_provider('killed')
        receipt=validate_killed(associated,killed,provider_before,provider_killed,login['device_id'],hold,held.marker,held.release_control,released)
        remote=read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token'])
        require(remote==body==source,'RELEASE_SYNC_PROCESS_ORIGINAL_S3_BYTES')
        exclusive(self.output/('remote-original-'+NOTE),remote)
        self.preserve_auth('process-killed-auth',auth,sealed)
        self.process_stage='killed-new-PID-read'
        previous_pid=self.phases[-1]['pid'];self.phase('status','process-killed-read')
        require(self.phases[-1]['pid']!=previous_pid,'RELEASE_SYNC_PROCESS_NEW_READ_PID')
        opened,source=collect_original(self,'killed-read',stage='killed-read')
        transition=validate_open_read(killed,opened,provider_killed,self.sync_provider('killed-read'))
        record(self.output/'process-original-open-transition.json',transition)
        self.preserve_auth('process-killed-read-auth',auth,sealed)
        self.process_stage='explicit-original-changes-reconcile'
        record(self.output/'process-explicit-original-continue-intent.json',dict(operation_id=receipt['operation_id'],file_id=receipt['file_id'],
            original_hold_operation_id=hold['operation_id'],original_lifecycle_id=lifecycle['original_lifecycle_id'],phase=RESUME,
            raw_killed_and_new_PID_read_checked=True,automatic_retry=False))
        self.phase(RESUME,'process-original-reconciled')
        stop_completed_read(self)
        reconciled,source=collect_original(self,'reconciled',stage='reconciled');provider_after=self.sync_provider('reconciled')
        validate_reconciled(opened,reconciled,provider_killed,provider_after,receipt)
        require(source==body,'RELEASE_SYNC_PROCESS_RECONCILE_CHANGED_BYTES')
        self.preserve_auth('process-reconciled-auth',auth,sealed)
        self.process_stage='final-new-PID-cold-read'
        previous_pid=self.phases[-1]['pid'];self.phase('status','process-final-cold')
        require(self.phases[-1]['pid']!=previous_pid,'RELEASE_SYNC_PROCESS_NEW_FINAL_COLD_PID')
        cold,source=collect_original(self,'cold',stage='cold');preserve_read(reconciled,cold)
        require(source==body and self.sync_provider('cold')==provider_after,'RELEASE_SYNC_PROCESS_FINAL_COLD_CHANGED')
        self.preserve_auth('process-final-auth',auth,sealed)
        result=dict(schema=1,passed=True,kind='android-signed-release-original-process-loss-read-recovery',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_Save_id=seeded['all_Save_ids'][0],original_association_upload_id=associated['original_association_upload_id'],
            original_Save_outbox_archived_and_preserved=True,original_login_id=login['operation_id'],Android_device=login['device_id'],
            independent_setup_device=prepared['device_id'],original_association=frozen,original_commit_receipt=receipt,
            original_hold_control=hold,original_release_control=held.release_control,original_lifecycle=lifecycle,original_open_transition=transition,
            raw_killed_originals_preserved_before_product_reopen=True,original_process_absence_and_live_driver_release_observed=True,
            service_original_revision_POST_once_and_held_response_explicitly_released=True,client_receipt_known=False,
            original_attempt_count_and_NULL_error_preserved=True,original_job_outbox_head_inbox_reconciled_by_explicit_changes_read=True,
            provider_revision_object_upload_state_unchanged_during_recovery=True,actual_S3_and_original_note_bytes_verified=True,
            private_auth_cipher_UI_or_finish_bytes_exported=False,decoded_image_verified=False,Native_RAM_invocation_id_observed=False,
            physical_phone_verified=False,actual_desktop_product_pair_verified=False,full_native_worker_recovery_verified=False,
            offline_timeout_conflict_verified=False,whole_Sync_A4_complete=False,published=False)
        record(self.output/'sync-process-runtime-proof.json',result);return result


if __name__=='__main__':raise SystemExit(main(ReleaseSyncProcess))
