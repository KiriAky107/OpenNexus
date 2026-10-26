"""Signed original Stop under held commit delivery and explicit changes read."""
import os

from android_auth_runtime import active_intent
from android_release_auth import main
from android_release_observation import APP, StoppedReleaseObserver, exclusive
from android_release_sync import ReleaseSync, validate_association, read_original_object
# Only the existing two-device/two-session provider validator is reused.
from android_release_sync_recovery import validate_auth_provider
from android_release_sync_stop_snapshot import NOTE, project_stop, preserve_initial_outbox, preserve_read
from android_release_sync_stop_validation import validate_stopped, validate_reconciled
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import BODY, require
from android_sync_setup import Setup
from android_sync_stop_cleanup import OriginalStopControl, LABEL
from android_sync_stop_host import ENTRY, STOP, RESUME, record, run_hosted_stop_cycle
from release_plan import digest

PHASES = (STOP, RESUME)


class ReleaseSyncStop(ReleaseSync):
    allowed_phases = ReleaseSync.allowed_phases+PHASES

    def phase(self, phase, label):
        if phase not in PHASES: return super().phase(phase,label)
        require(self.driver_entry == 'ReleaseSyncRuntimeProbe', 'RELEASE_SYNC_STOP_ENTRY_CHANGED')
        held = self.stop_control.held if phase == STOP else None
        self.driver_entry = ENTRY
        try: return run_hosted_stop_cycle(self,phase,label,held=held)
        finally: self.driver_entry = 'ReleaseSyncRuntimeProbe'

    def stop_snapshot(self, label, stage):
        observer = StoppedReleaseObserver(self.device,self.args.serial,self.output/'content-originals'/label,[NOTE])
        observer.identity = self.observer.identity
        require(observer.observe_identity() == self.identity,'RELEASE_SYNC_STOP_OWNER_CHANGED')
        root = observer.discover_data_root(); observer.copy_stopped(root,'snapshot')
        raw = observer.output/'snapshot'
        value = project_stop(raw,self.tools/('stop-working-'+label),stage=stage)
        record(self.output/(label+'-content-projection.json'),value)
        body = (raw/'vault'/NOTE).read_bytes()
        require(body == BODY.encode(),'RELEASE_SYNC_STOP_ORIGINAL_RAW_BYTES')
        return value,body

    def preserve_auth(self, label, auth, sealed):
        actual = self.snapshot(label); current = self.observer.ciphertext_fingerprints()
        record(self.output/(label+'-cipher-fingerprints.json'),current)
        require(actual == auth and current == sealed and self.observe(label) == self.stop_auth_provider,
                'RELEASE_SYNC_STOP_ORIGINAL_AUTH_CHANGED')

    def run(self):
        self.stop_control = None; self.stop_stage = 'prepare'
        try: return self.run_original()
        except Exception as error:
            record(self.output/'stop-component-failure.json',dict(passed=False,stage=self.stop_stage,
                error_type=type(error).__name__,automatic_retry=False,whole_Stop_component_passed=False))
            if self.stop_control is not None:
                try: self.stop_control.cleanup()
                except Exception as cleanup_error:
                    record(self.output/'stop-failure-control-cleanup-unavailable.json',dict(error_type=type(cleanup_error).__name__,
                        automatic_retry=False,whole_Stop_component_passed=False,client_receipt_known=False))
            raise

    def run_original(self):
        self.prepare(); self.stop_stage = 'original-note-Save'
        try:
            self.enable_test_ime(); ReleaseUpgrade.phase(self,'markdown','stop-seed-note')
        finally: self.restore_test_ime()
        seeded,body = self.stop_snapshot('seeded','seeded')
        self.stop_stage = 'original-login'
        self.phase('login-a','stop-login'); auth = self.snapshot('stop-login')
        require(len(auth['intents']) == 1,'RELEASE_SYNC_STOP_ONE_LOGIN')
        login = auth['intents'][0]; active_intent(auth,'login',login['device_id'])
        sealed = self.observer.ciphertext_fingerprints()
        record(self.output/'stop-original-cipher-fingerprints.json',sealed)
        self.stop_stage = 'independent-setup-and-association'
        setup = Setup(self.owner); prepared = setup.run(); record(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_STOP_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']); self.phase('associate','stop-association'); frozen = self.original_association()
        associated,source = self.stop_snapshot('associated','associated'); preserve_initial_outbox(seeded,associated)
        validate_association(frozen,associated['native'],prepared,login)
        self.stop_auth_provider = self.observe('stop-associated')
        validate_auth_provider(self.stop_auth_provider,login['device_id'],prepared['device_id'])
        provider_before = self.sync_provider('associated')
        self.stop_stage = 'original-hold-control'
        self.stop_control = OriginalStopControl(self.owner,self.output.absolute(),prepared['vault']['vault_id'])
        control = self.stop_control.arm()
        self.stop_stage = 'original-Start-Stop-held-GUI'
        self.phase(STOP,LABEL)
        held = self.stop_control.held
        require(held.marker is not None and held.release_control is not None,'RELEASE_SYNC_STOP_ORIGINAL_RELEASE_MISSING')
        released = held.read_released(); require(released is not None,'RELEASE_SYNC_STOP_ORIGINAL_RELEASE_MARKER_MISSING')
        record(self.output/'stop-original-held-response.json',held.marker)
        record(self.output/'stop-original-release-response.json',released)
        self.stop_stage = 'stopped-original-projection'
        stopped,source = self.stop_snapshot('stopped','stopped'); provider_stopped = self.sync_provider('stopped')
        receipt = validate_stopped(associated,stopped,provider_before,provider_stopped,login['device_id'],
            control,held.marker,held.release_control,released)
        self.preserve_auth('stop-stopped-auth',auth,sealed)
        remote = read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token'])
        require(remote == body == source,'RELEASE_SYNC_STOP_ORIGINAL_S3_BYTES')
        exclusive(self.output/('remote-original-'+NOTE),remote)
        self.stop_stage = 'stopped-new-PID-read'
        previous_pid = self.phases[-1]['pid']; self.phase('status','stop-stopped-read')
        require(self.phases[-1]['pid'] != previous_pid,'RELEASE_SYNC_STOP_NEW_STOPPED_READ_PID')
        readonly,source = self.stop_snapshot('stopped-read','stopped-read'); preserve_read(stopped,readonly)
        require(self.sync_provider('stopped-read') == provider_stopped,'RELEASE_SYNC_STOP_READ_CHANGED_PROVIDER')
        self.preserve_auth('stop-stopped-read-auth',auth,sealed)
        self.stop_stage = 'explicit-original-changes-reconcile'
        record(self.output/'stop-explicit-original-continue-intent.json',dict(operation_id=receipt['operation_id'],file_id=receipt['file_id'],
            original_hold_operation_id=control['operation_id'],phase=RESUME,original_read_checked=True,automatic_retry=False))
        self.phase(RESUME,'stop-original-reconciled')
        reconciled,source = self.stop_snapshot('reconciled','reconciled'); provider_after = self.sync_provider('reconciled')
        validate_reconciled(readonly,reconciled,provider_stopped,provider_after,receipt)
        require(source == body,'RELEASE_SYNC_STOP_RECONCILE_CHANGED_BYTES')
        self.preserve_auth('stop-reconciled-auth',auth,sealed)
        self.stop_stage = 'final-new-PID-cold-read'
        previous_pid = self.phases[-1]['pid']; self.phase('status','stop-final-cold')
        require(self.phases[-1]['pid'] != previous_pid,'RELEASE_SYNC_STOP_NEW_FINAL_COLD_PID')
        cold,source = self.stop_snapshot('cold','cold'); preserve_read(reconciled,cold)
        require(source == body and self.sync_provider('cold') == provider_after,'RELEASE_SYNC_STOP_FINAL_COLD_CHANGED')
        self.preserve_auth('stop-final-auth',auth,sealed)
        result = dict(schema=1,passed=True,kind='android-signed-release-original-Stop-held-read-recovery',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_Save_id=seeded['all_Save_ids'][0],original_association_upload_id=associated['original_association_upload_id'],
            original_Save_outbox_archived_and_preserved=True,original_login_id=login['operation_id'],Android_device=login['device_id'],
            independent_setup_device=prepared['device_id'],original_association=frozen,original_commit_receipt=receipt,
            original_hold_control=control,original_release_control=held.release_control,
            same_live_instrumentation_through_two_original_inputs_Stop_release_and_final_guard=True,
            service_original_revision_POST_once_and_held_response_explicitly_released=True,client_receipt_known=False,
            original_interrupted_attempt_and_count_preserved=True,original_job_outbox_head_inbox_reconciled_by_explicit_changes_read=True,
            provider_revision_object_upload_state_unchanged_during_recovery=True,actual_S3_and_original_note_bytes_verified=True,
            private_auth_cipher_UI_or_finish_bytes_exported=False,decoded_image_verified=False,Native_RAM_invocation_id_observed=False,
            physical_phone_verified=False,actual_desktop_product_pair_verified=False,full_native_worker_recovery_verified=False,
            offline_timeout_conflict_verified=False,whole_Sync_A4_complete=False,published=False)
        record(self.output/'sync-stop-runtime-proof.json',result); return result


if __name__ == '__main__': raise SystemExit(main(ReleaseSyncStop))
