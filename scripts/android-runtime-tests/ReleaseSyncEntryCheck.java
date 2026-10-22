package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Actual closed entry and package admission on a JVM harness, never a device or network. */
public final class ReleaseSyncEntryCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("signed Sync admission changed"); }
    private static Bundle phase(String value) { Bundle args=new Bundle();args.putString("phase",value);return args; }
    private static Bundle extra(String key) { Bundle args=phase("cycle");args.putString(key,"forbidden");return args; }
    public static void main(String[] ignored) {
        int accepted=0,refused=0,packages=0;
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        need(new SyncRuntimeProbe().namespace().equals("SYNC"));
        need(new AuthRuntimeProbe().touchSettleMillis()==150);
        need(new SyncRuntimeProbe().touchSettleMillis()==600);
        need(new ReleaseSyncRuntimeProbe().touchSettleMillis()==600);
        for(String name:new String[]{"Sync one cycle now","Sign in","Confirm this association","Stop this cycle","",null}) {
            need(!new AuthRuntimeProbe().nativeAccessibilityClick(name));
            need(!new SyncRuntimeProbe().nativeAccessibilityClick(name));
            need(!new ReleaseSyncRuntimeProbe().nativeAccessibilityClick(name));
            need(!new AuthRuntimeProbe().hostedCycleTouch(name));
            need(!new SyncRuntimeProbe().hostedCycleTouch(name));
            need(new ReleaseSyncRuntimeProbe().hostedCycleTouch(name)=="Sync one cycle now".equals(name));
        }
        for(String value:new String[]{"login-a","associate","association-original","status","cycle","cycle-incoming"}) {
            ReleaseSyncRuntimeProbe entry=new ReleaseSyncRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_SYNC"));
            entry.onCreate(phase(value));need(entry.starts==1 && entry.finishes==0);accepted++;
        }
        for(String value:new String[]{"current","refresh","logout","refresh-unknown","login-b-held","invalid-certificate","cycle-media","cycle-offline","cycle-stop","conflict-copy",null,"","CYCLE","cycle\n","../associate","cycle-extra"}) {
            ReleaseSyncRuntimeProbe entry=new ReleaseSyncRuntimeProbe();entry.onCreate(phase(value));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(Bundle value:new Bundle[]{null,new Bundle(),extra("package"),extra("password"),extra("endpoint"),extra("cycle_input"),extra("association_preview"),extra("cycle_dispatch_id")}) {
            ReleaseSyncRuntimeProbe entry=new ReleaseSyncRuntimeProbe();entry.onCreate(value);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        for(SyncRuntimeProbe entry:new SyncRuntimeProbe[]{new SyncRuntimeProbe(),new ReleaseSyncRuntimeProbe()}) {
            entry.admitReviewPackage(entry.APP,false);packages++;
            String other=entry.APP.endsWith(".dev")?"cc.kronecker.opennexus":"cc.kronecker.opennexus.dev";
            for(String name:new String[]{other,"cc.kronecker.opennexus.evil",null,""}) {
                boolean rejected=false;
                try { entry.admitReviewPackage(name,false); } catch(IllegalStateException expected) { rejected=true; }
                need(rejected);packages++;
            }
            boolean rejected=false;
            try { entry.admitReviewPackage(entry.APP,true); } catch(IllegalStateException expected) { rejected=true; }
            need(rejected);packages++;
        }
        need(accepted==6 && refused==24 && packages==12);
        System.out.println("RELEASE_SYNC_ENTRY_OK accepted="+accepted+" refused="+refused+" package_checks="+packages+" actual_Android_sync=0");
    }
}
