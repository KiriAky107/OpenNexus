package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Separate driver-owned entry; no package, endpoint or credential arguments. */
public final class ReleaseAuthRuntimeProbe extends AuthRuntimeProbe {
    public ReleaseAuthRuntimeProbe() { super(true); }

    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseAuthScene.allowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_AUTH_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);
            return;
        }
        super.onCreate(args);
    }

    @Override protected String namespace() { return "RELEASE_AUTH"; }
    @Override protected boolean viewportAdmission() { return true; }
}
