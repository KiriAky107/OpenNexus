package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

public final class ReleaseSyncMutationEntryCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("closed mutation input changed");}
    private static Bundle phase(String value){Bundle args=new Bundle();args.putString("phase",value);return args;}
    private static Bundle extra(String key){Bundle args=phase("cycle-rename");args.putString(key,"forbidden");return args;}
    public static void main(String[] ignored) {
        int accepted=0,refused=0,policies=0;
        for(String name:new String[]{"cycle-rename","cycle-delete"}) {
            ReleaseSyncMutationRuntimeProbe entry=new ReleaseSyncMutationRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus")&&entry.namespace().equals("RELEASE_SYNC"));
            entry.onCreate(phase(name));need(entry.starts==1&&entry.finishes==0);accepted++;
            ReleaseSyncRuntimeProbe old=new ReleaseSyncRuntimeProbe();old.onCreate(phase(name));
            need(old.starts==0&&old.finishes==1);policies++;
        }
        for(String name:new String[]{"login-a","associate","association-original","status","cycle","cycle-incoming","cycle-media","cycle-stop","cycle-offline","conflict-copy",null,"","cycle-delete\n","../cycle-rename","CYCLE-DELETE","delete"}) {
            ReleaseSyncMutationRuntimeProbe entry=new ReleaseSyncMutationRuntimeProbe();entry.onCreate(phase(name));
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1&&entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(Bundle args:new Bundle[]{null,new Bundle(),extra("package"),extra("password"),extra("path"),extra("endpoint"),extra("cycle_input"),extra("cycle_dispatch_id"),extra("association_preview"),extra("operation")}) {
            ReleaseSyncMutationRuntimeProbe entry=new ReleaseSyncMutationRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1);refused++;
        }
        for(String name:new String[]{"cycle","cycle-incoming","cycle-rename","cycle-delete","cycle-media","cycle-stop","",null}) {
            need(new SyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncMutationRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-rename".equals(name)||"cycle-delete".equals(name)));policies+=3;
        }
        for(String name:new String[]{"Sync one cycle now","Stop this cycle","Confirm this association","Sign in","",null}) {
            ReleaseSyncMutationRuntimeProbe entry=new ReleaseSyncMutationRuntimeProbe();
            need(entry.hostedCycleTouch(name)=="Sync one cycle now".equals(name));
            need(!entry.nativeAccessibilityClick(name));policies+=2;
        }
        need(new ReleaseSyncMutationRuntimeProbe().touchSettleMillis()==600);
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        need(accepted==2&&refused==26&&policies==38);
        System.out.println("RELEASE_SYNC_MUTATION_ENTRY_OK accepted="+accepted+" refused="+refused+" policies="+policies+" actual_Android_mutations=0");
    }
}
