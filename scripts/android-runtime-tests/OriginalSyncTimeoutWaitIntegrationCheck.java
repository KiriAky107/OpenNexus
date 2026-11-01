package cc.kronecker.opennexus.testdriver;

/** Real action code with supplied clocks/owner/scene; no Android/Native result. */
public final class OriginalSyncTimeoutWaitIntegrationCheck {
    private static final String P="40000000-0000-4000-8000-000000000001",A="40000000-0000-4000-8000-000000000002";
    private static int assertions;
    private static void need(boolean value) {assertions++;if(!value)throw new AssertionError("wait integration");}
    private static OriginalSyncTimeoutActions finished() {
        OriginalSyncTimeoutActions v=new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.TIMEOUT,P,A);
        v.begin("cycle-start");v.ready(1000);v.finished("cycle-start");return v;
    }
    private static void rejected(String mode,OriginalSyncTimeoutWaitFacts.Point point,int owners,int scenes,int clocks,Boolean state) throws Exception {
        OriginalSyncTimeoutActions v=finished();long[] now={mode.equals("before")?37001:mode.equals("deadline")?37000:36999};
        int[] calls={0,0,0,0};
        try {
            v.observeFailure(()->{calls[0]++;if(mode.equals("owner") || mode.equals("final") && calls[0]==2)now[0]=37001;
                if(mode.equals("rollback"))now[0]=36998;},
                ()->{calls[1]++;if(mode.equals("scene"))now[0]=37001;return mode.equals("final");},
                ()->{calls[2]++;return now[0];},delay->{calls[3]++;now[0]+=delay;});
            throw new AssertionError("rejected original guard passed");
        } catch(IllegalStateException error) {
            need(error.getClass()==IllegalStateException.class && "OWNED_SYNC_TIMEOUT_ORIGINAL_SEQUENCE".equals(error.getMessage()));
        }
        OriginalSyncTimeoutWaitFacts f=v.lastWaitFailure();need(f!=null && f.point==point && f.readyElapsedMs==1000
            && f.deadlineElapsedMs==37000 && f.stateObserved==state);
        need(calls[0]==owners && calls[1]==scenes && calls[2]==clocks && calls[3]==0);
        need(v.lastWaitFailure()==f && calls[2]==clocks);
        try {v.observeFailure(()->{},()->true,()->1000L,delay->{});throw new AssertionError();}
        catch(IllegalStateException expected) {need(v.lastWaitFailure()==null);}
        need(f.point==point && f.deadlineElapsedMs==37000);
    }
    public static void main(String[] args) throws Exception {
        need(args.length==0);
        rejected("before",OriginalSyncTimeoutWaitFacts.Point.BEFORE_OWNER,0,0,1,null);
        rejected("owner",OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,1,0,2,null);
        rejected("scene",OriginalSyncTimeoutWaitFacts.Point.AFTER_SCENE,1,1,3,Boolean.FALSE);
        rejected("final",OriginalSyncTimeoutWaitFacts.Point.AFTER_FINAL_OWNER,2,1,4,Boolean.TRUE);
        rejected("deadline",OriginalSyncTimeoutWaitFacts.Point.SCENE_FALSE_AT_DEADLINE,1,1,3,Boolean.FALSE);
        rejected("rollback",OriginalSyncTimeoutWaitFacts.Point.AFTER_OWNER,1,0,2,null);
        for(boolean inScene:new boolean[]{false,true}) {
            OriginalSyncTimeoutActions v=finished();Exception original=new Exception("private fixture");int[] calls={0,0,0};
            try {v.observeFailure(()->{calls[0]++;if(!inScene)throw original;},
                ()->{calls[1]++;throw original;},()->{calls[2]++;return 1000L;},delay->{});throw new AssertionError();}
            catch(Exception error) {need(error==original && v.lastWaitFailure()==null);}
            need(calls[0]==1 && calls[1]==(inScene?1:0) && calls[2]==(inScene?2:1));
        }
        OriginalSyncTimeoutActions success=finished();int[] calls={0,0,0};
        success.observeFailure(()->calls[0]++,()->{calls[1]++;return true;},()->{calls[2]++;return 31000L;},delay->{});
        need(success.lastWaitFailure()==null && calls[0]==2 && calls[1]==1 && calls[2]==4);
        need(success.summary().contains("failed_cycle_UI_observed=true") && success.summary().contains("native_timeout_verified=false"));
        System.out.println("ORIGINAL_SYNC_TIMEOUT_WAIT_INTEGRATION_OK assertions="+assertions+" actual_Android_Native=0");
    }
}
