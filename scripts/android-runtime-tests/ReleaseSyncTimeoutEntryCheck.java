package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Actual entry lifecycle with Bundle/Instrumentation fixture, no Android input. */
public final class ReleaseSyncTimeoutEntryCheck {
    private static void need(boolean value) {if(!value)throw new AssertionError("closed timeout entry changed");}
    private static Bundle phase(String name) {Bundle value=new Bundle();value.putString("phase",name);return value;}
    public static void main(String[] ignored) {
        int accepted=0,refused=0,policies=0;
        for(String name:new String[]{OriginalSyncTimeoutActions.TIMEOUT,OriginalSyncTimeoutActions.RESUME}) {
            ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();entry.onCreate(phase(name));
            need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_SYNC_TIMEOUT")
                && entry.starts==1 && entry.finishes==0);accepted++;
            for(AuthRuntimeProbe old:new AuthRuntimeProbe[]{new ReleaseSyncRuntimeProbe(),new ReleaseSyncMutationRuntimeProbe(),
                new ReleaseSyncRecoveryRuntimeProbe(),new ReleaseSyncStopRuntimeProbe(),new ReleaseSyncProcessRuntimeProbe()}) {
                old.onCreate(phase(name));need(old.starts==0 && old.finishes==1);policies++;
            }
        }
        for(String name:new String[]{"login-a","associate","association-original","status","cycle","cycle-incoming","cycle-rename",
            "cycle-delete","cycle-drop-original","cycle-reconcile-original","cycle-stop-original","cycle-after-stop-original",
            "cycle-process-loss-original","cycle-after-process-loss-original","cycle-stop","cycle-after-stop","cycle-offline",
            "conflict-copy",null,"","CYCLE-TIMEOUT-ORIGINAL","cycle-timeout-original\n","../cycle-timeout-original"}) {
            ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();entry.onCreate(phase(name));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1 && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(String name:new String[]{OriginalSyncTimeoutActions.TIMEOUT,OriginalSyncTimeoutActions.RESUME})
            for(String key:new String[]{"package","path","content","password","endpoint","cycle_input","cycle_dispatch_id",
                "association_preview","control_operation_id","native_invocation_id","driver_intent_id","parent_intent_id",
                "expected_result","action","x","command","force_stop","timeout_ms","hold_ms","release_signal"}) {
                Bundle args=phase(name);args.putString(key,"private-sentinel-never-echoed");
                ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();entry.onCreate(args);
                need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1 && !entry.result.getString("stream","").contains("private-sentinel"));refused++;
            }
        for(Bundle args:new Bundle[]{null,new Bundle()}) {
            ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        for(String name:new String[]{"cycle","cycle-incoming","cycle-rename","cycle-delete","cycle-drop-original","cycle-reconcile-original",
            "cycle-stop-original","cycle-after-stop-original","cycle-process-loss-original","cycle-after-process-loss-original",
            OriginalSyncTimeoutActions.TIMEOUT,OriginalSyncTimeoutActions.RESUME,null}) {
            ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();
            need(entry.hostedCyclePhaseAllowed(name)==OriginalSyncTimeoutActions.allowed(name) && !entry.expectsUnknownOriginalCycle(name));
            need(new ReleaseSyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncMutationRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-rename".equals(name)||"cycle-delete".equals(name)));
            need(new ReleaseSyncRecoveryRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-drop-original".equals(name)||"cycle-reconcile-original".equals(name)));
            need(new ReleaseSyncStopRuntimeProbe().hostedCyclePhaseAllowed(name)=="cycle-after-stop-original".equals(name));
            need(new ReleaseSyncProcessRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-process-loss-original".equals(name)||"cycle-after-process-loss-original".equals(name)));policies+=7;
        }
        for(String name:new String[]{"Sync one cycle now","Stop this cycle","Confirm this association","Sign in","",null}) {
            ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();
            need(entry.hostedCycleTouch(name)=="Sync one cycle now".equals(name) && !entry.nativeAccessibilityClick(name));policies+=2;
        }
        ReleaseSyncTimeoutRuntimeProbe entry=new ReleaseSyncTimeoutRuntimeProbe();need(entry.viewportAdmission() && entry.touchSettleMillis()==600);policies++;
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));policies++;
        need(OriginalSyncTimeoutActions.FAILURE_WAIT_MS==36000 && OriginalSyncTimeoutActions.INPUT_WAIT_MS==30000
            && OriginalSyncTimeoutActions.RELEASE_WAIT_MS==5000);policies++;
        need(accepted==2 && refused==65 && policies==116);
        System.out.println("RELEASE_SYNC_TIMEOUT_ENTRY_OK accepted="+accepted+" refused="+refused+" policies="+policies+" actual_Android_timeout=0");
    }
}
