package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Separate fixed stable read entry; neither general release nor debug scope grows. */
public final class ReleaseSyncReadRuntimeProbe extends RuntimeProbe {
    private String phase;
    public ReleaseSyncReadRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseSyncReadScene.allowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_SYNC_READ_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        phase=args.getString("phase", "");
        super.onCreate(args);
    }
    @Override public void onStart() {
        Bundle result=new Bundle();
        try {
            result.putString("stream", readSignedSyncPeer(phase)
                + "OPENNEXUS_RUNTIME_PHASE_OK " + phase + "\n");
            finish(0,result);
        } catch (Throwable error) {
            // Never export an exception message, stack, AX tree or screenshot.
            result.putString("stream", "OPENNEXUS_RELEASE_SYNC_READ_FAILED stage=" + signedSyncReadStage()
                + " no_save=1 decoded_image_verified=0\n");
            finish(1,result);
        }
    }
}
