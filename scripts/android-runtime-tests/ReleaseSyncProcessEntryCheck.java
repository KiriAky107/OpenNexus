package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Entry admission only. No Android window, process command or service is invoked. */
public final class ReleaseSyncProcessEntryCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("closed process entry changed");}
    private static Bundle phase(String value){Bundle args=new Bundle();args.putString("phase",value);return args;}
    public static void main(String[] ignored) {
        int accepted=0,refused=0,policies=0;
        for(String name:new String[]{OriginalSyncProcessActions.LOSS,OriginalSyncProcessActions.RESUME}) {
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus") && entry.namespace().equals("RELEASE_SYNC_PROCESS"));
            entry.onCreate(phase(name));need(entry.starts==1 && entry.finishes==0);accepted++;
            ReleaseSyncRuntimeProbe normal=new ReleaseSyncRuntimeProbe();normal.onCreate(phase(name));
            ReleaseSyncMutationRuntimeProbe mutation=new ReleaseSyncMutationRuntimeProbe();mutation.onCreate(phase(name));
            ReleaseSyncRecoveryRuntimeProbe recovery=new ReleaseSyncRecoveryRuntimeProbe();recovery.onCreate(phase(name));
            ReleaseSyncStopRuntimeProbe stop=new ReleaseSyncStopRuntimeProbe();stop.onCreate(phase(name));
            need(normal.starts==0 && normal.finishes==1 && mutation.starts==0 && mutation.finishes==1
                && recovery.starts==0 && recovery.finishes==1 && stop.starts==0 && stop.finishes==1);policies+=4;
        }
        for(String name:new String[]{"login-a","associate","association-original","status","cycle","cycle-incoming",
            "cycle-rename","cycle-delete","cycle-drop-original","cycle-reconcile-original","cycle-stop-original",
            "cycle-after-stop-original","cycle-stop","cycle-after-stop","cycle-offline","conflict-copy",null,"",
            "CYCLE-PROCESS-LOSS-ORIGINAL","cycle-process-loss-original\n","../cycle-process-loss-original"}) {
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();entry.onCreate(phase(name));
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                && entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(String name:new String[]{OriginalSyncProcessActions.LOSS,OriginalSyncProcessActions.RESUME})
            for(String key:new String[]{"package","path","content","password","endpoint","cycle_input","cycle_dispatch_id",
                "association_preview","control_operation_id","native_invocation_id","driver_intent_id","parent_intent_id",
                "expected_result","action","x","command","force_stop"}) {
                Bundle args=phase(name);args.putString(key,"private-sentinel-never-echoed");
                ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();entry.onCreate(args);
                need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1
                    && !entry.result.getString("stream","").contains("private-sentinel"));refused++;
            }
        for(Bundle args:new Bundle[]{null,new Bundle()}) {
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);refused++;
        }
        for(String name:new String[]{"cycle","cycle-incoming","cycle-rename","cycle-delete","cycle-drop-original",
            "cycle-reconcile-original","cycle-stop-original","cycle-after-stop-original",OriginalSyncProcessActions.LOSS,
            OriginalSyncProcessActions.RESUME,null}) {
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();
            need(entry.hostedCyclePhaseAllowed(name)==OriginalSyncProcessActions.allowed(name));
            need(!entry.expectsUnknownOriginalCycle(name));
            need(new ReleaseSyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncMutationRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-rename".equals(name)||"cycle-delete".equals(name)));
            need(new ReleaseSyncRecoveryRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-drop-original".equals(name)||"cycle-reconcile-original".equals(name)));
            need(new ReleaseSyncStopRuntimeProbe().hostedCyclePhaseAllowed(name)=="cycle-after-stop-original".equals(name));policies+=6;
        }
        for(String name:new String[]{"Sync one cycle now","Stop this cycle","Confirm this association","Sign in","",null}) {
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();
            need(entry.hostedCycleTouch(name)=="Sync one cycle now".equals(name));
            need(!entry.nativeAccessibilityClick(name));policies+=2;
        }
        for(AuthRuntimeProbe entry:new AuthRuntimeProbe[]{new AuthRuntimeProbe(),new SyncRuntimeProbe(),new ReleaseSyncRuntimeProbe(),
            new ReleaseSyncMutationRuntimeProbe(),new ReleaseSyncRecoveryRuntimeProbe(),new ReleaseSyncStopRuntimeProbe(),new ReleaseSyncProcessRuntimeProbe()}) {
            need(!entry.productUiObservationClosed());policies++;
        }
        ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();
        need(entry.touchSettleMillis()==600 && entry.viewportAdmission());policies++;
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));policies++;
        need(accepted==2 && refused==57 && policies==95);
        System.out.println("RELEASE_SYNC_PROCESS_ENTRY_OK accepted="+accepted+" refused="+refused+" policies="+policies+" actual_Android_process_loss=0");
    }
}
