package cc.kronecker.opennexus.testdriver;

/** Pure optional facts from existing wait samples; never a wait admission. */
final class OriginalSyncTimeoutWaitFacts {
    static final long BOUND_MS=36000;
    enum Point {BEFORE_OWNER,AFTER_OWNER,AFTER_SCENE,AFTER_FINAL_OWNER,SCENE_FALSE_AT_DEADLINE}
    final Point point;
    final long readyElapsedMs,previousElapsedMs,sampledElapsedMs,deadlineElapsedMs;
    final Boolean stateObserved;

    private OriginalSyncTimeoutWaitFacts(Point point,long ready,long previous,long sampled,long deadline,Boolean observed) {
        this.point=point;readyElapsedMs=ready;previousElapsedMs=previous;
        sampledElapsedMs=sampled;deadlineElapsedMs=deadline;stateObserved=observed;
    }
    static OriginalSyncTimeoutWaitFacts capture(boolean passed,Point point,long ready,long previous,
            long sampled,long deadline,Boolean observed) {
        // Invalid or unobserved input makes metadata unavailable. This method
        // cannot throw a replacement guard or turn a successful check false.
        if(passed || point==null || ready<0 || ready>Long.MAX_VALUE-BOUND_MS || deadline!=ready+BOUND_MS
                || previous<ready || previous>deadline || sampled<0)return null;
        switch(point) {
            case BEFORE_OWNER:
            case AFTER_OWNER:
                if(observed!=null)return null;
                break;
            case AFTER_SCENE:
                if(observed==null)return null;
                break;
            case AFTER_FINAL_OWNER:
                if(!Boolean.TRUE.equals(observed))return null;
                break;
            case SCENE_FALSE_AT_DEADLINE:
                if(!Boolean.FALSE.equals(observed) || sampled!=deadline)return null;
                return new OriginalSyncTimeoutWaitFacts(point,ready,previous,sampled,deadline,observed);
            default:return null;
        }
        if(sampled>=previous && sampled<=deadline)return null;
        return new OriginalSyncTimeoutWaitFacts(point,ready,previous,sampled,deadline,observed);
    }
}
