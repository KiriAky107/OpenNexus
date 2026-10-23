package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;

/** Only the two explicit peer-mutation cycles; no product file-operation bridge. */
public final class ReleaseSyncMutationRuntimeProbe extends SyncRuntimeProbe {
    public ReleaseSyncMutationRuntimeProbe() { super(true); }
    private static boolean allowed(String phase) {
        return "cycle-rename".equals(phase) || "cycle-delete".equals(phase);
    }
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !allowed(args.getString("phase",""))) {
            Bundle result=new Bundle();
            result.putString("stream","OPENNEXUS_RELEASE_SYNC_MUTATION_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace(){return "RELEASE_SYNC";}
    @Override protected boolean hostedCycleTouch(String name){return "Sync one cycle now".equals(name);}
    @Override protected boolean hostedCyclePhaseAllowed(String phase){return allowed(phase);}
}
