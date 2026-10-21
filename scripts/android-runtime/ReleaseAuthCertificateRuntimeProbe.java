package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Closed untrusted TLS and readonly cold recovery in the separate driver. */
public final class ReleaseAuthCertificateRuntimeProbe extends AuthRuntimeProbe {
    public ReleaseAuthCertificateRuntimeProbe() { super(true); }
    @Override public void onCreate(Bundle args) {
        if (args == null || args.size() != 1 || !ReleaseAuthScene.certificateAllowed(args.getString("phase", ""))) {
            Bundle result=new Bundle();
            result.putString("stream", "OPENNEXUS_RELEASE_AUTH_CERTIFICATE_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() { return "RELEASE_AUTH_CERTIFICATE"; }
    @Override protected boolean viewportAdmission() { return true; }
}
