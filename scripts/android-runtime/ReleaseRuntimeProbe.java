package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Separate instrumentation in the ephemeral driver, never in either product APK. */
public final class ReleaseRuntimeProbe extends RuntimeProbe {
    public ReleaseRuntimeProbe() { super(true); }

    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseScene.allowed(args.getString("phase", ""))) {
            Bundle result = new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_SCOPE_REFUSED no_product_input=1\n");
            finish(1, result);
            return;
        }
        super.onCreate(args);
    }
}
