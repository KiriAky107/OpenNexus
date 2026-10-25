package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Lifecycle and input admission only: the Android UI is never invoked. */
public final class ReleaseSyncStopEntryCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("closed Stop input changed");}
    private static Bundle phase(String value){Bundle args=new Bundle();args.putString("phase",value);return args;}
    public static void main(String[] ignored) {
        int accepted=0,refused=0,policies=0;
        for(String name:new String[]{"cycle-stop-original","cycle-after-stop-original"}) {
            ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();
            need(entry.APP.equals("cc.kronecker.opennexus")&&entry.namespace().equals("RELEASE_SYNC_STOP"));
            entry.onCreate(phase(name));need(entry.starts==1&&entry.finishes==0);accepted++;
            ReleaseSyncRuntimeProbe old=new ReleaseSyncRuntimeProbe();old.onCreate(phase(name));
            ReleaseSyncRecoveryRuntimeProbe recovery=new ReleaseSyncRecoveryRuntimeProbe();recovery.onCreate(phase(name));
            ReleaseSyncMutationRuntimeProbe mutation=new ReleaseSyncMutationRuntimeProbe();mutation.onCreate(phase(name));
            need(old.starts==0&&old.finishes==1&&recovery.starts==0&&recovery.finishes==1&&mutation.starts==0&&mutation.finishes==1);
            policies+=3;
        }
        for(String name:new String[]{"login-a","associate","association-original","status","cycle","cycle-incoming",
            "cycle-rename","cycle-delete","cycle-drop-original","cycle-reconcile-original","cycle-stop","cycle-after-stop",
            "cycle-offline","conflict-copy",null,"","CYCLE-STOP-ORIGINAL","cycle-stop-original\n","../cycle-stop-original"}) {
            ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();entry.onCreate(phase(name));
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1&&entry.result.getString("stream","").contains("no_product_input=1"));refused++;
        }
        for(String name:new String[]{"cycle-stop-original","cycle-after-stop-original"})
            for(String key:new String[]{"package","path","content","password","endpoint","cycle_input","cycle_dispatch_id",
                "association_preview","control_operation_id","native_invocation_id","driver_intent_id","expected_result","action","x","command"}) {
                Bundle args=phase(name);args.putString(key,"secret-sentinel-not-echoed");
                ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();entry.onCreate(args);
                need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1);
                need(!entry.result.getString("stream","").contains("secret-sentinel"));refused++;
            }
        for(Bundle args:new Bundle[]{null,new Bundle()}) {
            ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();entry.onCreate(args);
            need(entry.starts==0&&entry.finishes==1&&entry.exitCode==1);refused++;
        }
        for(String name:new String[]{"cycle","cycle-incoming","cycle-rename","cycle-delete","cycle-drop-original",
            "cycle-reconcile-original","cycle-stop-original","cycle-after-stop-original","cycle-stop",null}) {
            ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();
            need(entry.hostedCyclePhaseAllowed(name)=="cycle-after-stop-original".equals(name));
            need(!entry.expectsUnknownOriginalCycle(name));
            need(new ReleaseSyncRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle".equals(name)||"cycle-incoming".equals(name)));
            need(new ReleaseSyncRecoveryRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-drop-original".equals(name)||"cycle-reconcile-original".equals(name)));
            need(new ReleaseSyncMutationRuntimeProbe().hostedCyclePhaseAllowed(name)==("cycle-rename".equals(name)||"cycle-delete".equals(name)));policies+=5;
        }
        for(String name:new String[]{"Sync one cycle now","Stop this cycle","Confirm this association","Sign in","",null}) {
            ReleaseSyncStopRuntimeProbe entry=new ReleaseSyncStopRuntimeProbe();
            need(entry.hostedCycleTouch(name)==("Sync one cycle now".equals(name)||"Stop this cycle".equals(name)));
            need(!entry.nativeAccessibilityClick(name));policies+=2;
        }
        need(new ReleaseSyncStopRuntimeProbe().touchSettleMillis()==600);
        need(new ReleaseSyncStopRuntimeProbe().viewportAdmission());
        need(new SyncRuntimeProbe().APP.equals("cc.kronecker.opennexus.dev"));policies+=3;
        need(accepted==2&&refused==51&&policies==71);
        System.out.println("RELEASE_SYNC_STOP_ENTRY_OK accepted="+accepted+" refused="+refused+" policies="+policies+" actual_Android_Stop=0");
    }
}
