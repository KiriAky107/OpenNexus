package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Original lost-response recovery only, in the separate driver and fixed stable app. */
public final class ReleaseAuthRecoveryRuntimeProbe extends AuthRuntimeProbe {
    public ReleaseAuthRecoveryRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseAuthScene.recoveryAllowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_AUTH_RECOVERY_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() { return "RELEASE_AUTH_RECOVERY"; }
    @Override protected boolean viewportAdmission() { return true; }
}
