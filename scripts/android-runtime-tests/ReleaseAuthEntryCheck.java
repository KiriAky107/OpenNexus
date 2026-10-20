package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Actual entry/constructor on a JVM harness; no Android UI or network execution. */
public final class ReleaseAuthEntryCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("release auth admission changed"); }
    private static Bundle phase(String value) { Bundle args=new Bundle();args.putString("phase",value);return args; }
    public static void main(String[] unused) {
        need(new AuthRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        ReleaseAuthRuntimeProbe entry=new ReleaseAuthRuntimeProbe();
        need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_AUTH"));
        int accepted=0,refused=0;
        for(String value:new String[]{"login-a","current","refresh","logout"}) {
            entry=new ReleaseAuthRuntimeProbe();entry.onCreate(phase(value));
            need(entry.starts==1 && entry.finishes==0);accepted++;
        }
        for(String value:new String[]{null,"","login-b-held","refresh-unknown","invalid-certificate","release-image-markdown","sync","../current","CURRENT","current\n"}) {
            entry=new ReleaseAuthRuntimeProbe();entry.onCreate(phase(value));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(Bundle value:new Bundle[]{null,new Bundle(),withExtra("package"),withExtra("password"),withExtra("endpoint")}) {
            entry=new ReleaseAuthRuntimeProbe();entry.onCreate(value);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        System.out.println("RELEASE_AUTH_ENTRY_OK accepted="+accepted+" refused="+refused+" actual_Android_TLS_or_auth=0");
    }
    private static Bundle withExtra(String name) { Bundle args=phase("login-a");args.putString(name,"forbidden");return args; }
}
