package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

public final class ReleaseSyncRecoveryEntryCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("closed recovery input changed");}
    private static Bundle phase(String value){Bundle args=new Bundle();args.putString("phase",value);return args;}
    public static void main(String[] ignored) {
        int accepted=0,refused=0,policies=0;
        for(String name:new String[]{"cycle-drop-original","cycle-reconcile-original"}) {
            ReleaseSyncRecoveryRuntimeProbe entry=new ReleaseSyncRecoveryRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus")&&entry.namespace().equals("RELEASE_SYNC"));
            entry.onCreate(phase(name));need(entry.starts==1&&entry.finishes==0);accepted++;
            ReleaseSyncRuntimeProbe old=new ReleaseSyncRuntimeProbe();old.onCreate(phase(name));
            ReleaseSyncMutationRuntimeProbe mutation=new ReleaseSyncMutationRuntimeProbe();mutation.onCreate(phase(name));
            need(old.starts==0&&old.finishes==1&&mutation.starts==0&&mutation.finishes==1);policies+=2;
        }
        String[] denied={"login-a","associate","association-original","status","cycle","cycle-incoming","cycle-rename","cycle-delete",
            "cycle-dropped-upload","cycle-offline","cycle-stop","cycle-media","conflict-copy","refresh-revoked",null,"","CYCLE-DROP-ORIGINAL",
            "cycle-drop-original\n","../cycle-reconcile-original","reconcile"};
        for(String name:denied) {
            ReleaseSyncRecoveryRuntimeProbe entry=new ReleaseSyncRecoveryRuntimeProbe();entry.onCreate(phase(name));
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1&&entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(String key:new String[]{"package","path","content","password","endpoint","cycle_input","cycle_dispatch_id","association_preview","operation","control_operation_id","driver_intent_id","expected_result"}) {
            Bundle args=phase("cycle-drop-original");args.putString(key,"secret-sentinel-not-echoed");
            ReleaseSyncRecoveryRuntimeProbe entry=new ReleaseSyncRecoveryRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1);
            need(!entry.result.getString("stream","").contains("secret-sentinel"));refused++;
        }
        for(Bundle args:new Bundle[]{null,new Bundle()}) {
            ReleaseSyncRecoveryRuntimeProbe entry=new ReleaseSyncRecoveryRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1);refused++;
        }
        for(String name:new String[]{"cycle","cycle-incoming","cycle-rename","cycle-delete","cycle-drop-original","cycle-reconcile-original","cycle-stop","",null}) {
            ReleaseSyncRecoveryRuntimeProbe recovery=new ReleaseSyncRecoveryRuntimeProbe();
            need(recovery.hostedCyclePhaseAllowed(name)==("cycle-drop-original".equals(name)||"cycle-reconcile-original".equals(name)));
            need(recovery.expectsUnknownOriginalCycle(name)=="cycle-drop-original".equals(name));
            need(!new SyncRuntimeProbe().expectsUnknownOriginalCycle(name));
            need(!new ReleaseSyncRuntimeProbe().expectsUnknownOriginalCycle(name));
            need(!new ReleaseSyncMutationRuntimeProbe().expectsUnknownOriginalCycle(name));
            need(new ReleaseSyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncMutationRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-rename".equals(name)||"cycle-delete".equals(name)));policies+=7;
        }
        for(String name:new String[]{"Sync one cycle now","Stop this cycle","Confirm this association","Sign in","",null}) {
            ReleaseSyncRecoveryRuntimeProbe entry=new ReleaseSyncRecoveryRuntimeProbe();
            need(entry.hostedCycleTouch(name)=="Sync one cycle now".equals(name));
            need(!entry.nativeAccessibilityClick(name));policies+=2;
        }
        need(new ReleaseSyncRecoveryRuntimeProbe().touchSettleMillis()==600);
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));
        need(accepted==2&&refused==34&&policies==79);
        System.out.println("RELEASE_SYNC_RECOVERY_ENTRY_OK accepted="+accepted+" refused="+refused+" policies="+policies+" actual_Android_recovery=0");
    }
}
