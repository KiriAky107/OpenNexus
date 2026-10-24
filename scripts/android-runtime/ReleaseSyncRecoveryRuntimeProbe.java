package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Only one dropped-original check and one separately intended read reconciliation. */
public final class ReleaseSyncRecoveryRuntimeProbe extends SyncRuntimeProbe {
    public ReleaseSyncRecoveryRuntimeProbe() { super(true); }
    private static boolean allowed(String phase) {
        return "cycle-drop-original".equals(phase) || "cycle-reconcile-original".equals(phase);
    }
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !allowed(args.getString("phase",""))) {
            Bundle result=new Bundle();
            result.putString("stream","OPENNEXUS_RELEASE_SYNC_RECOVERY_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace(){return "RELEASE_SYNC";}
    @Override protected boolean hostedCycleTouch(String name){return "Sync one cycle now".equals(name);}
    @Override protected boolean hostedCyclePhaseAllowed(String phase){return allowed(phase);}
    @Override protected boolean expectsUnknownOriginalCycle(String phase){return "cycle-drop-original".equals(phase);}
    @Override protected boolean additionalPhase(String phase) throws Exception {
        if(!allowed(phase))return false;
        cycle(phase);return true;
    }
}
