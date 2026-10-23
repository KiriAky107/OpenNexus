"""New empty-vault download and four pre-buffer source crops of its owned PNG.

All product bytes remain the fixed signed successor. This does not replay the
completed text-reader/Save/upload components, or claim phone/desktop/full A4.
"""
import os

from android_auth_runtime import active_intent
from android_release_auth import main
from android_release_observation import APP
from android_release_sync import ReleaseSync, ReleasePeer, PEER_NOTE, PEER_CANVAS, validate_association, validate_incoming
from android_release_sync_read import ReleaseSyncRead
from android_release_sync_read_snapshot import preserve_editor_read
from android_release_sync_snapshot import PEER, ASSET
from android_runtime_probe import IMAGE_BYTES, require
from android_sync_image_host import PHASE, run_image
from android_sync_setup import Setup
from release_plan import atomic_json, digest


class ReleaseSyncImage(ReleaseSyncRead):
    allowed_phases = ReleaseSync.allowed_phases + (PHASE,)

    def phase(self, phase, label):
        if phase == PHASE: return run_image(self,label)
        require(phase in ReleaseSync.allowed_phases,'SYNC_IMAGE_CLOSED_SETUP_PHASE')
        return super().phase(phase,label)

    def run(self):
        self.prepare()  # Normal zero-device prepare precedes independent setup.
        self.phase('login-a','image-login')
        auth=self.snapshot('image-login');require(len(auth['intents'])==1,'SYNC_IMAGE_ONE_BOOTSTRAP_LOGIN')
        login=auth['intents'][0];active_intent(auth,'login',login['device_id'])
        empty,_=self.download_snapshot('empty')
        require(empty['native']['binding'] is None and not empty['all_clean_drafts'],'SYNC_IMAGE_EMPTY_LOCAL')
        prepared=Setup(self.owner).run();atomic_json(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'SYNC_IMAGE_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']);self.phase('associate','image-association');frozen=self.original_association()
        associated,_=self.download_snapshot('associated');validate_association(frozen,associated['native'],prepared,login)
        require(not associated['all_clean_drafts'] and not self.sync_provider('associated')['rows']['revisions'],
            'SYNC_IMAGE_ASSOCIATION_CREATED_CONTENT')
        # Three actual new originals match the existing closed download schema;
        # neither a legacy LOCAL/Save assumption nor a reduced file set is used.
        peer=ReleasePeer(self.owner);receipts=[]
        for label,path,body in (('image-peer-png',ASSET,IMAGE_BYTES),('image-peer-note',PEER[0],PEER_NOTE),('image-peer-canvas',PEER[1],PEER_CANVAS)):
            receipt=peer.publish(label,path,body);receipts.append(receipt);atomic_json(self.output/(label+'-receipt.json'),receipt)
        self.phase('cycle-incoming','image-download')
        incoming,content=self.download_snapshot('downloaded',True);provider=self.sync_provider('downloaded')
        validate_incoming(receipts,provider,associated['native'],incoming['native'],login['device_id'])
        require(content=={PEER[0]:PEER_NOTE,PEER[1]:PEER_CANVAS,ASSET:IMAGE_BYTES} and not incoming['all_clean_drafts'],
            'SYNC_IMAGE_ACTUAL_NEW_DOWNLOAD_BYTES')
        image=self.phase(PHASE,'canvas-image')
        after,after_bytes=self.download_snapshot('image-observed',True)
        preserve_editor_read(incoming,after,PEER[1])
        require(after_bytes==content,'SYNC_IMAGE_READ_CHANGED_BYTES')
        require(self.sync_provider('after-image')==provider,'SYNC_IMAGE_PROVIDER_CHANGED')
        require(self.snapshot('image-final')==auth,'SYNC_IMAGE_AUTH_CHANGED')
        result=dict(schema=1,passed=True,kind='android-signed-release-sync-image-source-ROI',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_login_id=login['operation_id'],Android_device=login['device_id'],independent_setup_device=prepared['device_id'],
            original_association=frozen,original_peer_receipts=receipts,original_image_observation=image,
            actual_four_pre_buffer_source_ROIs_and_scaled_source_pixels_verified=True,decoded_image_verified=True,
            retained_ROIs=4,ROI_dimensions=[8,8],whole_display_capture=False,full_capture_then_crop=False,
            no_Save_text_input_image_import_or_new_outbox=True,clean_Canvas_draft_initialization_accounted_for=True,
            local_GUI_Save_or_upload_replayed=False,Native_RAM_invocation_id_observed=False,physical_phone_verified=False,
            actual_desktop_product_pair_verified=False,whole_Sync_A4_complete=False,published=False)
        atomic_json(self.output/'sync-image-runtime-proof.json',result);return result


if __name__=='__main__':raise SystemExit(main(ReleaseSyncImage))
