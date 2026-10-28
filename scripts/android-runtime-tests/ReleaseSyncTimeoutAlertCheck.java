package cc.kronecker.opennexus.testdriver;

/** Exact fixed-product alert text, no accessibility tree or Android result. */
public final class ReleaseSyncTimeoutAlertCheck {
    public static void main(String[] args) {
        if(args.length!=1)throw new AssertionError("One source-bound alert required");
        String actual=args[0];
        if(!ReleaseSyncTimeoutRuntimeProbe.networkAlert(actual))throw new AssertionError("Fixed SyncActivity alert rejected");
        String[] refused={null,"","SYNC_NETWORK_ERROR","(SYNC_NETWORK_ERROR)",
            "Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)",
            actual+" extra","prefix "+actual,actual+"\n",actual.replace("SYNC_NETWORK_ERROR","SYNC_REMOTE_ERROR")};
        for(String text:refused)
            if(ReleaseSyncTimeoutRuntimeProbe.networkAlert(text))throw new AssertionError("Unapproved alert accepted");
        if(!OriginalSyncTimeoutActions.failedScene(1,1,1,1,0,0,0,0))throw new AssertionError("Exact failed scene changed");
        if(OriginalSyncTimeoutActions.failedScene(1,1,1,2,0,0,0,0))throw new AssertionError("Duplicate alert accepted");
        if(OriginalSyncTimeoutActions.failedScene(1,1,1,1,1,0,0,0))throw new AssertionError("Enabled Stop accepted");
        System.out.println("RELEASE_SYNC_TIMEOUT_FIXED_ALERT_OK allowed=1 refused=9 scene_boundaries=3 actual_Android_UI=0");
    }
}
