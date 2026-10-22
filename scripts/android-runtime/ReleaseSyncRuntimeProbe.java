package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Closed signed foreground entry; no caller-selected identity or alternate input mode. */
public final class ReleaseSyncRuntimeProbe extends SyncRuntimeProbe {
    public ReleaseSyncRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseSyncScene.allowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_SYNC_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() { return "RELEASE_SYNC"; }
    @Override protected boolean nativeAccessibilityClick(String name) {
        return "Sync one cycle now".equals(name);
    }
}
