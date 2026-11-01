package cc.kronecker.opennexus.testdriver;

/** Synthetic existing samples only; no clock, device, file or Native call. */
public final class OriginalSyncTimeoutWaitFactsCheck {
    private static int checks;
    private static void need(boolean value) {checks++;if(!value)throw new AssertionError("wait facts");}
    private static OriginalSyncTimeoutWaitFacts fact(boolean passed,OriginalSyncTimeoutWaitFacts.Point point,
            long previous,long now,Boolean observed) {
        return OriginalSyncTimeoutWaitFacts.capture(passed,point,1000,previous,now,37000,observed);
    }
    public static void main(String[] args) {
        need(args.length==0);
        for(OriginalSyncTimeoutWaitFacts.Point point:OriginalSyncTimeoutWaitFacts.Point.values()) {
            Boolean observed=point==OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE?Boolean.FALSE:
                point==OriginalSyncTimeoutWaitFacts.Point.AFTER_FINAL_OWNER?Boolean.TRUE:
                point==OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE?Boolean.FALSE:null;
            long now=point==OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE?37000:37001;
            OriginalSyncTimeoutWaitFacts value=fact(false,point,36000,now,observed);
            need(value!=null && value.point==point && value.readyElapsedMs==1000 && value.previousElapsedMs==36000
                && value.sampledElapsedMs==now && value.deadlineElapsedMs==37000 && value.stateObserved==observed);
            need(fact(true,point,36000,now,observed)==null);
        }
        for(OriginalSyncTimeoutWaitFacts.Point point:new OriginalSyncTimeoutWaitFacts.Point[]{
                OriginalSyncTimeoutWaitFacts.Point.BEFORE_OWNER,OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,
                OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE,OriginalSyncTimeoutWaitFacts.Point.AFTER_FINAL_OWNER}) {
            Boolean observed=point==OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE?Boolean.FALSE:
                point==OriginalSyncTimeoutWaitFacts.Point.AFTER_FINAL_OWNER?Boolean.TRUE:null;
            need(fact(false,point,2000,1999,observed)!=null);
            need(fact(false,point,2000,2000,observed)==null);
            need(fact(false,point,2000,37000,observed)==null);
            need(fact(false,point,2000,30000,observed)==null);
        }
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.BEFORE_OWNER,1000,37001,Boolean.FALSE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,1000,37001,Boolean.TRUE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE,1000,37001,null)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_FINAL_OWNER,1000,37001,Boolean.FALSE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE,36000,37000,Boolean.TRUE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE,36000,36999,Boolean.FALSE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE,36000,37001,Boolean.FALSE)==null);
        need(fact(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE,1000,37001,Boolean.TRUE)!=null);
        for(long[] invalid:new long[][]{{-1,0,1,36000},{Long.MAX_VALUE,Long.MAX_VALUE,Long.MAX_VALUE,Long.MAX_VALUE},
                {1000,999,37001,37000},{1000,37001,37002,37000},{1000,1000,-1,37000},{1000,1000,37001,37001}})
            need(OriginalSyncTimeoutWaitFacts.capture(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,
                invalid[0],invalid[1],invalid[2],invalid[3],null)==null);
        need(fact(false,null,1000,37001,null)==null);
        OriginalSyncTimeoutWaitFacts value=OriginalSyncTimeoutWaitFacts.capture(false,OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,
            Long.MAX_VALUE-36000,Long.MAX_VALUE-1,Long.MAX_VALUE-2,Long.MAX_VALUE,null);
        need(value!=null && value.deadlineElapsedMs==Long.MAX_VALUE && value.sampledElapsedMs==Long.MAX_VALUE-2);
        System.out.println("ORIGINAL_SYNC_TIMEOUT_WAIT_FACTS_OK assertions="+checks+" synthetic_samples_only=true runtime_verified=false");
    }
}
