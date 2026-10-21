package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** JVM admission only; no Android, TLS, credentials or device interaction. */
public final class ReleaseAuthCertificateEntryCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("certificate admission changed"); }
    private static Bundle phase(String value) { Bundle args=new Bundle();args.putString("phase",value);return args; }
    private static Bundle extra(String key) { Bundle args=phase("invalid-certificate");args.putString(key,"forbidden");return args; }
    public static void main(String[] ignored) {
        int accepted=0,refused=0;
        for(String value:new String[]{"invalid-certificate","certificate-cold-unusable"}) {
            ReleaseAuthCertificateRuntimeProbe entry=new ReleaseAuthCertificateRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_AUTH_CERTIFICATE"));
            entry.onCreate(phase(value));need(entry.starts==1 && entry.finishes==0);accepted++;
        }
        for(String value:new String[]{null,"login-a","refresh","logout","current","refresh-unknown","login-b-held","current-unusable","invalid-certificate-extra","certificate-cold-unusable\n"}) {
            ReleaseAuthCertificateRuntimeProbe entry=new ReleaseAuthCertificateRuntimeProbe();entry.onCreate(phase(value));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(Bundle value:new Bundle[]{null,new Bundle(),extra("package"),extra("password"),extra("endpoint")}) {
            ReleaseAuthCertificateRuntimeProbe entry=new ReleaseAuthCertificateRuntimeProbe();entry.onCreate(value);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        need(new AuthRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        System.out.println("RELEASE_AUTH_CERTIFICATE_ENTRY_OK accepted="+accepted+" refused="+refused+" actual_Android_TLS=0");
    }
}
