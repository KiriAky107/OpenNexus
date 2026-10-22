"""Independent empty-vault download and production GUI text/reference cold reads.

No local Save/upload replay. An AX image reference is not image decode proof;
that result deliberately remains false, as do real desktop/phone/full A4.
"""
import os
import re
import shlex

from android_auth_runtime import active_intent
from android_release_auth import ReleaseAuth, main
from android_release_observation import APP, DATABASES, StoppedReleaseObserver
from android_release_sync import ReleaseSync, ReleasePeer, PEER_NOTE, PEER_CANVAS, validate_association, validate_incoming
from android_release_sync_read_snapshot import project_download, preserve_editor_read
from android_release_sync_snapshot import PEER, ASSET
from android_runtime_probe import DRIVER, IMAGE_BYTES, require
from android_sync_setup import Setup
from release_plan import atomic_json, digest

READ_PHASES = ('release-sync-read-markdown', 'release-sync-read-canvas')


class ReleaseSyncRead(ReleaseSync):
    allowed_phases = ReleaseSync.allowed_phases + READ_PHASES

    def phase(self, phase, label):
        if phase not in READ_PHASES: return super().phase(phase, label)
        require(re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label), 'RELEASE_SYNC_READ_EXACT_LABEL')
        self.stop()
        self.original_command(label+'-launch', ('shell','-T',shlex.join(('am','start','-W','-n',self.activity))))
        pid = self.shell('pidof', APP).stdout.decode('ascii').strip()
        require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'RELEASE_SYNC_READ_OWN_PROCESS_MISSING')
        try:
            result = self.original_command(label+'-instrument', ('shell','-T',shlex.join(('am','instrument','-w','-e','phase',phase,
                DRIVER+'/.ReleaseSyncReadRuntimeProbe'))), timeout=180)
            expected = ('OPENNEXUS_RELEASE_SYNC_READ_OBSERVED phase='+phase+' title=1 markdown_body='
                + ('1 canvas_text_and_reference=0' if phase == READ_PHASES[0] else '0 canvas_text_and_reference=1')
                + ' no_save=1 no_text_input=1 no_image_import=1 decoded_image_verified=0').encode()
            require(expected in result.stdout and ('OPENNEXUS_RUNTIME_PHASE_OK '+phase).encode() in result.stdout
                    and b'_FAILED' not in result.stdout, 'RELEASE_SYNC_READ_ORIGINAL_GUI_FAILED')
            self.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True, decoded_image_verified=False))
        finally: self.stop()

    def download_snapshot(self, label, downloaded=False):
        observer = StoppedReleaseObserver(self.device, self.args.serial, self.output/'content-originals'/label,
            list(PEER), attachment=ASSET if downloaded else None)
        if not downloaded:
            # Exact empty stage: only the two approved indexes and WAL/SHM;
            # the closed host files query independently rejects nonempty vaults.
            observer.allowed = {db+s for db in DATABASES for s in ('','-wal','-shm')}
        observer.identity = self.observer.identity
        require(observer.observe_identity() == self.identity, 'RELEASE_SYNC_READ_OWNER_CHANGED')
        root = observer.discover_data_root(); observer.copy_stopped(root, 'snapshot')
        original = observer.output/'snapshot'
        value = project_download(original, self.tools/('download-working-'+label), downloaded=downloaded)
        atomic_json(self.output/(label+'-content-projection.json'), value)
        content = {path:(original/'vault'/path).read_bytes() for path in PEER+(ASSET,)} if downloaded else {}
        return value, content

    def failure_observations(self):
        # Auth keeps its private DB/UI boundary; no fallback full capture.
        ReleaseAuth.failure_observations(self)
        try: self.sync_provider('failure')
        except Exception as error:
            atomic_json(self.output/'failure-sync-provider-unavailable.json', dict(error_type=type(error).__name__, mutation_retried=False))
        if self.identity is not None:
            try:
                observer = StoppedReleaseObserver(self.device, self.args.serial, self.output/'failure-content-databases', list(PEER))
                observer.allowed = {db+s for db in DATABASES for s in ('','-wal','-shm')}
                observer.identity = self.observer.identity
                root=observer.discover_data_root();observer.copy_stopped(root,'snapshot')
            except Exception as error:
                atomic_json(self.output/'failure-content-unavailable.json', dict(error_type=type(error).__name__, mutation_retried=False))

    def run(self):
        self.prepare()  # Normal auth zero-device admission BEFORE setup.
        self.phase('login-a','download-login')
        auth=self.snapshot('download-login');require(len(auth['intents'])==1,'RELEASE_SYNC_READ_ONE_BOOTSTRAP_LOGIN')
        login=auth['intents'][0];active_intent(auth,'login',login['device_id'])
        empty,_=self.download_snapshot('empty')
        require(empty['native']['binding'] is None and not empty['all_clean_drafts'], 'RELEASE_SYNC_READ_FRESH_EMPTY_VAULT')
        setup=Setup(self.owner);prepared=setup.run();atomic_json(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_READ_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']);self.phase('associate','download-association');frozen=self.original_association()
        associated,_=self.download_snapshot('associated')
        validate_association(frozen,associated['native'],prepared,login)
        require(not associated['all_clean_drafts'] and not self.sync_provider('associated')['rows']['revisions'],
                'RELEASE_SYNC_READ_ASSOCIATION_CREATED_CONTENT')
        peer=ReleasePeer(self.owner);receipts=[]
        for label,path,body in (('read-peer-png',ASSET,IMAGE_BYTES),('read-peer-note',PEER[0],PEER_NOTE),('read-peer-canvas',PEER[1],PEER_CANVAS)):
            receipt=peer.publish(label,path,body);receipts.append(receipt);atomic_json(self.output/(label+'-receipt.json'),receipt)
        self.phase('cycle-incoming','download-cycle')
        incoming,content=self.download_snapshot('downloaded',True);provider=self.sync_provider('downloaded')
        validate_incoming(receipts,provider,associated['native'],incoming['native'],login['device_id'])
        require(content=={PEER[0]:PEER_NOTE,PEER[1]:PEER_CANVAS,ASSET:IMAGE_BYTES} and not incoming['all_clean_drafts'],
                'RELEASE_SYNC_READ_ACTUAL_DOWNLOAD_BYTES')
        before=incoming;read_pids=[]
        for phase,path,labels in ((READ_PHASES[0],PEER[0],('markdown-open','markdown-cold')),
                                  (READ_PHASES[1],PEER[1],('canvas-open','canvas-cold'))):
            for label in labels:
                self.phase(phase,label);read_pids.append(self.phases[-1]['pid'])
                after,body=self.download_snapshot(label,True)
                preserve_editor_read(before,after,path)
                require(body==content,'RELEASE_SYNC_READ_OPEN_CHANGED_BYTES');before=after
        require(len(set(read_pids))==4,'RELEASE_SYNC_READ_NEW_COLD_PIDS_REQUIRED')
        require(self.sync_provider('after-reads')==provider,'RELEASE_SYNC_READ_PROVIDER_CHANGED')
        require(self.snapshot('read-final')==auth,'RELEASE_SYNC_READ_AUTH_CHANGED')
        result=dict(schema=1,passed=True,kind='android-signed-release-sync-editor-read',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_login_id=login['operation_id'],Android_device=login['device_id'],independent_setup_device=prepared['device_id'],
            original_association=frozen,original_peer_receipts=receipts,read_PIDs=read_pids,
            actual_empty_vault_download_and_editor_text_reference_cold_reads=True,local_GUI_Save_or_upload_replayed=False,
            original_bytes_heads_inbox_and_transfer_records_preserved=True,clean_draft_initialization_accounted_for=True,
            decoded_image_verified=False,Native_RAM_invocation_id_observed=False,physical_phone_verified=False,
            actual_desktop_product_pair_verified=False,whole_Sync_A4_complete=False,published=False)
        atomic_json(self.output/'sync-read-runtime-proof.json',result);return result


if __name__ == '__main__': raise SystemExit(main(ReleaseSyncRead))
