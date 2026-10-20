package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Original same-process background delivery only, in the separate driver and fixed stable app. */
public final class ReleaseAuthBackgroundRuntimeProbe extends AuthRuntimeProbe {
    public ReleaseAuthBackgroundRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseAuthScene.backgroundAllowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_AUTH_BACKGROUND_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() { return "RELEASE_AUTH_BACKGROUND"; }
    @Override protected boolean viewportAdmission() { return true; }
}
