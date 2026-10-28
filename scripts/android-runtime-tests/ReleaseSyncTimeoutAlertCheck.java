package cc.kronecker.opennexus.testdriver;

import android.view.accessibility.AccessibilityNodeInfo;

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
        int channels=0;
        for(String[] pair:new String[][]{{actual,null},{null,actual},{"",actual},{actual,actual}}) {
            if(!ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(new AccessibilityNodeInfo(pair[0],pair[1],false)))
                throw new AssertionError("Exact source alert in an accessibility label channel rejected");
            channels++;
        }
        for(String text:refused) {
            if(ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(new AccessibilityNodeInfo(null,text,false)))
                throw new AssertionError("Unapproved description accepted");
            channels++;
        }
        for(String[] pair:new String[][]{{actual,null},{null,actual},{actual,actual}}) {
            if(ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(new AccessibilityNodeInfo(pair[0],pair[1],true)))
                throw new AssertionError("Editable label accepted as the fixed alert");
            channels++;
        }
        for(String[] pair:new String[][]{{actual,"unexpected"},{"unexpected",actual},{actual,actual+"\n"},{actual+"\n",actual}}) {
            if(ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(new AccessibilityNodeInfo(pair[0],pair[1],false)))
                throw new AssertionError("Unapproved occupied label channel accepted");
            channels++;
        }
        if(!OriginalSyncTimeoutActions.failedScene(1,1,1,1,0,0,0,0))throw new AssertionError("Exact failed scene changed");
        if(OriginalSyncTimeoutActions.failedScene(1,1,1,2,0,0,0,0))throw new AssertionError("Duplicate alert accepted");
        if(OriginalSyncTimeoutActions.failedScene(1,1,1,1,1,0,0,0))throw new AssertionError("Enabled Stop accepted");
        System.out.println("RELEASE_SYNC_TIMEOUT_FIXED_ALERT_OK allowed=1 refused=9 scene_boundaries=3 actual_Android_UI=0 node_channel_checks="+channels);
    }
}
