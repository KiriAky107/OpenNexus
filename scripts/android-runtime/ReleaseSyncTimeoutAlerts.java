package cc.kronecker.opennexus.testdriver;

/** Fixed-product full UI labels only. No UI reads, files, commands or Native claims. */
final class ReleaseSyncTimeoutAlerts {
    static final String CATALOG_SHA256="de56387b9166bc8a8fdd0e952397f1c6aee9acc2b225d366e7204aaafa231ec4";
    static final int NETWORK=2;
    private static final String[] CODES={
        "SYNC_CONNECTION_FAILED",
        "SYNC_RESPONSE_INVALID",
        "SYNC_NETWORK_ERROR",
        "SYNC_TLS_UNAVAILABLE",
        "SYNC_REDIRECT_BLOCKED",
        "SYNC_FOREGROUND_CHANGED",
        "SYNC_FOREGROUND_REQUIRED",
        "SYNC_CANCELLED",
        "SYNC_PAUSED",
        "SYNC_BINDING_CHANGED",
        "SYNC_NOT_BOUND",
        "SYNC_TARGET_INVALID",
        "SYNC_PAGE_INVALID",
        "SYNC_AUTH_SELECTION_CHANGED",
        "SYNC_AUTH_SELECTION_INVALID",
        "SYNC_AUTH_IDENTITY_CHANGED",
        "SYNC_AUTH_RECONCILIATION_REQUIRED",
        "SYNC_LOGIN_REQUIRED",
        "SYNC_SESSION_EXPIRED",
        "SESSION_EXPIRED",
        "SYNC_CREDENTIALS_UNAVAILABLE",
        "SYNC_CREDENTIALS_INVALID",
        "SYNC_ACTIVITY_BUSY",
        "SYNC_ACTIVITY_LIMIT",
        "SYNC_ACTIVITY_UNAVAILABLE",
        "SYNC_ACTIVITY_RECONCILIATION_REQUIRED",
        "SYNC_ACTIVITY_REQUEST_INVALID",
        "SYNC_ACTIVITY_ID_INVALID",
        "SYNC_ACTIVITY_OWNER_CHANGED",
        "SYNC_WORKER_INTERRUPTED",
        "SYNC_RESOLUTION_INVALID",
        "SYNC_RESOLUTION_CHANGED",
        "SYNC_CONFLICT_CHANGED",
        "SYNC_REVIEW_CHANGED",
        "SYNC_REVIEW_UNSAFE_FILE",
        "SYNC_RESPONSE_TOO_LARGE",
        "SYNC_TIME_INVALID",
        "SYNC_ENDPOINT_INVALID",
        "SYNC_LOGIN_INVALID",
        "MOBILE_HOST_REQUIRED",
        "SYNC_PLATFORM_UNSUPPORTED",
        "MOBILE_STORAGE_UNAVAILABLE",
        "HOST_BUSY",
        "VAULT_CHANGED",
        "QUOTA_EXCEEDED",
        "FILESYSTEM_ERROR",
        "DATABASE_ERROR",
        "SYNC_REMOTE_ERROR",
        "REVISION_CONFLICT",
        "RATE_LIMITED"
    };
    private static final String[] LABELS={
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_CONNECTION_FAILED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_RESPONSE_INVALID)",
        "Last recorded sync error · Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_TLS_UNAVAILABLE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_REDIRECT_BLOCKED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_FOREGROUND_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_FOREGROUND_REQUIRED)",
        "Last recorded sync error · This sync cycle stopped. Retry can resume confirmed upload offsets. (SYNC_CANCELLED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_PAUSED)",
        "Last recorded sync error · The vault or sync binding changed. Refresh the page. (SYNC_BINDING_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_NOT_BOUND)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_TARGET_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_PAGE_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_AUTH_SELECTION_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_AUTH_SELECTION_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_AUTH_IDENTITY_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_AUTH_RECONCILIATION_REQUIRED)",
        "Last recorded sync error · Sign-in or device authorization expired. Sign in again. (SYNC_LOGIN_REQUIRED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_SESSION_EXPIRED)",
        "Last recorded sync error · Sign-in or device authorization expired. Sign in again. (SESSION_EXPIRED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_CREDENTIALS_UNAVAILABLE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_CREDENTIALS_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_BUSY)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_LIMIT)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_UNAVAILABLE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_RECONCILIATION_REQUIRED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_REQUEST_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_ID_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_OWNER_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_WORKER_INTERRUPTED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_RESOLUTION_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_RESOLUTION_CHANGED)",
        "Last recorded sync error · The file revision changed. Refresh and review the conflict. (SYNC_CONFLICT_CHANGED)",
        "Last recorded sync error · The file revision changed. Refresh and review the conflict. (SYNC_REVIEW_CHANGED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_REVIEW_UNSAFE_FILE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_RESPONSE_TOO_LARGE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_TIME_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_ENDPOINT_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_LOGIN_INVALID)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (MOBILE_HOST_REQUIRED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_PLATFORM_UNSUPPORTED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (MOBILE_STORAGE_UNAVAILABLE)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (HOST_BUSY)",
        "Last recorded sync error · The vault or sync binding changed. Refresh the page. (VAULT_CHANGED)",
        "Last recorded sync error · Remote storage quota is insufficient. Adjust it or clean up remote history, then retry. (QUOTA_EXCEEDED)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (FILESYSTEM_ERROR)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (DATABASE_ERROR)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (SYNC_REMOTE_ERROR)",
        "Last recorded sync error · The file revision changed. Refresh and review the conflict. (REVISION_CONFLICT)",
        "Last recorded sync error · Sync did not complete. Check service status and retry. (RATE_LIMITED)"
    };
    static final String FRAGMENT_CATALOG_SHA256="48e3aa6bf5b911398eb73fe661284644d0e65e82e99f297225feef1a9dee1022";
    private static final String HEADER="Last recorded sync error",DETAIL="Sync details";
    private static final String[] BODIES={
        "Sync did not complete. Check service status and retry. (SYNC_CONNECTION_FAILED)",
        "Sync did not complete. Check service status and retry. (SYNC_RESPONSE_INVALID)",
        "Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)",
        "Sync did not complete. Check service status and retry. (SYNC_TLS_UNAVAILABLE)",
        "Sync did not complete. Check service status and retry. (SYNC_REDIRECT_BLOCKED)",
        "Sync did not complete. Check service status and retry. (SYNC_FOREGROUND_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_FOREGROUND_REQUIRED)",
        "This sync cycle stopped. Retry can resume confirmed upload offsets. (SYNC_CANCELLED)",
        "Sync did not complete. Check service status and retry. (SYNC_PAUSED)",
        "The vault or sync binding changed. Refresh the page. (SYNC_BINDING_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_NOT_BOUND)",
        "Sync did not complete. Check service status and retry. (SYNC_TARGET_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_PAGE_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_AUTH_SELECTION_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_AUTH_SELECTION_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_AUTH_IDENTITY_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_AUTH_RECONCILIATION_REQUIRED)",
        "Sign-in or device authorization expired. Sign in again. (SYNC_LOGIN_REQUIRED)",
        "Sync did not complete. Check service status and retry. (SYNC_SESSION_EXPIRED)",
        "Sign-in or device authorization expired. Sign in again. (SESSION_EXPIRED)",
        "Sync did not complete. Check service status and retry. (SYNC_CREDENTIALS_UNAVAILABLE)",
        "Sync did not complete. Check service status and retry. (SYNC_CREDENTIALS_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_BUSY)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_LIMIT)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_UNAVAILABLE)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_RECONCILIATION_REQUIRED)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_REQUEST_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_ID_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_ACTIVITY_OWNER_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_WORKER_INTERRUPTED)",
        "Sync did not complete. Check service status and retry. (SYNC_RESOLUTION_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_RESOLUTION_CHANGED)",
        "The file revision changed. Refresh and review the conflict. (SYNC_CONFLICT_CHANGED)",
        "The file revision changed. Refresh and review the conflict. (SYNC_REVIEW_CHANGED)",
        "Sync did not complete. Check service status and retry. (SYNC_REVIEW_UNSAFE_FILE)",
        "Sync did not complete. Check service status and retry. (SYNC_RESPONSE_TOO_LARGE)",
        "Sync did not complete. Check service status and retry. (SYNC_TIME_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_ENDPOINT_INVALID)",
        "Sync did not complete. Check service status and retry. (SYNC_LOGIN_INVALID)",
        "Sync did not complete. Check service status and retry. (MOBILE_HOST_REQUIRED)",
        "Sync did not complete. Check service status and retry. (SYNC_PLATFORM_UNSUPPORTED)",
        "Sync did not complete. Check service status and retry. (MOBILE_STORAGE_UNAVAILABLE)",
        "Sync did not complete. Check service status and retry. (HOST_BUSY)",
        "The vault or sync binding changed. Refresh the page. (VAULT_CHANGED)",
        "Remote storage quota is insufficient. Adjust it or clean up remote history, then retry. (QUOTA_EXCEEDED)",
        "Sync did not complete. Check service status and retry. (FILESYSTEM_ERROR)",
        "Sync did not complete. Check service status and retry. (DATABASE_ERROR)",
        "Sync did not complete. Check service status and retry. (SYNC_REMOTE_ERROR)",
        "The file revision changed. Refresh and review the conflict. (REVISION_CONFLICT)",
        "Sync did not complete. Check service status and retry. (RATE_LIMITED)"
    };
    static String bodyLabel(int index) {return BODIES[index];}
    static String headerLabel() {return HEADER;}
    static String detailLabel() {return DETAIL;}
    static int bodyKind(String value) {
        if(value==null || value.length()>512)return -1;
        for(int i=0;i<BODIES.length;i++)if(BODIES[i].equals(value))return i;
        return -1;
    }
    static int size() {return CODES.length;}
    static String[] codes() {return CODES.clone();}
    static String label(int index) {return LABELS[index];}
    static int kind(String value) {
        if(value==null || value.length()>512)return -1;
        for(int i=0;i<LABELS.length;i++)if(LABELS[i].equals(value))return i;
        return -1;
    }
    static int strictPair(String value,String description) {
        String a=value==null?"":value,b=description==null?"":description;
        int first=kind(a),second=kind(b);
        return first>=0 && (b.isEmpty() || a.equals(b))?first:
            second>=0 && a.isEmpty()?second:-1;
    }
    static final class Tally {
        private final int[] text=new int[size()],description=new int[size()],strict=new int[size()];
        private final int[] bodyText=new int[size()],bodyDescription=new int[size()],header=new int[2],detail=new int[2];
        private int conflicts;
        void record(String value,String label) {
            String a=value==null?"":value,b=label==null?"":label;
            int first=kind(a),second=kind(b),accepted=strictPair(a,b);
            if(first>=0)text[first]=Math.min(2,text[first]+1);
            if(second>=0)description[second]=Math.min(2,description[second]+1);
            if(accepted>=0)strict[accepted]=Math.min(2,strict[accepted]+1);
            if((first>=0 || second>=0) && !a.isEmpty() && !b.isEmpty() && !a.equals(b))
                conflicts=Math.min(2,conflicts+1);
            int bodyFirst=bodyKind(a),bodySecond=bodyKind(b);
            if(bodyFirst>=0)bodyText[bodyFirst]=Math.min(2,bodyText[bodyFirst]+1);
            if(bodySecond>=0)bodyDescription[bodySecond]=Math.min(2,bodyDescription[bodySecond]+1);
            if(HEADER.equals(a))header[0]=Math.min(2,header[0]+1);
            if(HEADER.equals(b))header[1]=Math.min(2,header[1]+1);
            if(DETAIL.equals(a))detail[0]=Math.min(2,detail[0]+1);
            if(DETAIL.equals(b))detail[1]=Math.min(2,detail[1]+1);
        }
        int[][] snapshot() {
            return new int[][]{text.clone(),description.clone(),strict.clone(),new int[]{conflicts},
                bodyText.clone(),bodyDescription.clone(),header.clone(),detail.clone()};
        }
    }
}
