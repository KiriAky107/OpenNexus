package cc.kronecker.opennexus.testdriver;

/** Supplied complete public labels/counts only; no node reads or ownership proof.
 * A caller must reject unknown reads and apply the original owned-window,
 * visible, password and editable gates before reading either label channel.
 */
final class ReleaseSyncQuotaScene {
    static final String RULE="complete-public-quota-alert-exact-v1";
    static final String QUOTA="Last recorded sync error \u00b7 Remote storage quota is insufficient. Adjust it or clean up remote history, then retry. (QUOTA_EXCEEDED)";
    private static final int[] FAILED={1,1,1,1,0,0,0,0};
    private ReleaseSyncQuotaScene() {}

    // Null is an already-known absent Android label channel, not a read error.
    static boolean strictPair(String text,String description) {
        boolean a=QUOTA.equals(text),b=QUOTA.equals(description);
        return (a || b) && (text==null || text.isEmpty() || a)
            && (description==null || description.isEmpty() || b);
    }
    static boolean failed(int[] counts) {
        if(counts==null || counts.length!=FAILED.length)return false;
        for(int i=0;i<FAILED.length;i++)if(counts[i]!=FAILED[i])return false;
        return true;
    }
}
