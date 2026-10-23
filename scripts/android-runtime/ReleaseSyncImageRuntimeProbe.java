package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** One fixed stable image observation; no capture, path or command arguments. */
public final class ReleaseSyncImageRuntimeProbe extends RuntimeProbe {
    public ReleaseSyncImageRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !OriginalSyncImageRegion.PHASE.equals(args.getString("phase",""))) {
            Bundle result=new Bundle();result.putString("stream","OPENNEXUS_RELEASE_SYNC_IMAGE_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override public void onStart() {
        Bundle result=new Bundle();
        try {
            holdSignedSyncImage();
            result.putString("stream","OPENNEXUS_RELEASE_SYNC_IMAGE_PHASE_OK release-sync-image gui_guard_only=1 decoded_image_verified=0\n");
            finish(0,result);
        } catch(Throwable error) {
            result.putString("stream","OPENNEXUS_RELEASE_SYNC_IMAGE_FAILED stage="+signedSyncReadStage()+" no_capture_by_driver=1\n");
            finish(1,result);
        }
    }
}
