package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Admission/constructor only on a JVM lifecycle harness, no actual Android/TLS operation. */
public final class ReleaseAuthRecoveryEntryCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("release recovery admission changed"); }
    private static Bundle phase(String value) { Bundle args=new Bundle();args.putString("phase",value);return args; }
    private static Bundle extra(String key) { Bundle args=phase("refresh-unknown");args.putString(key,"forbidden");return args; }
    public static void main(String[] ignored) {
        int accepted=0,refused=0;
        for(String value:new String[]{"login-a","refresh-unknown","current-unusable"}) {
            ReleaseAuthRecoveryRuntimeProbe entry=new ReleaseAuthRecoveryRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_AUTH_RECOVERY"));
            entry.onCreate(phase(value));need(entry.starts==1 && entry.finishes==0);accepted++;
        }
        for(String value:new String[]{null,"","current","refresh","logout","login-b-held","invalid-certificate","refresh-unknown-extra","current-unusable\n","../refresh-unknown"}) {
            ReleaseAuthRecoveryRuntimeProbe entry=new ReleaseAuthRecoveryRuntimeProbe();entry.onCreate(phase(value));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(Bundle value:new Bundle[]{null,new Bundle(),extra("package"),extra("password"),extra("endpoint")}) {
            ReleaseAuthRecoveryRuntimeProbe entry=new ReleaseAuthRecoveryRuntimeProbe();entry.onCreate(value);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        need(new AuthRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        System.out.println("RELEASE_AUTH_RECOVERY_ENTRY_OK accepted="+accepted+" refused="+refused+" actual_Android_TLS_or_recovery=0");
    }
}
