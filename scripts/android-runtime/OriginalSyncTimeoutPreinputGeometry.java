package cc.kronecker.opennexus.testdriver;

/** Plans only supplied current geometry before input; it cannot observe or reveal UI. */
final class OriginalSyncTimeoutPreinputGeometry {
    static final long BUDGET_MS=5000,STABLE_MS=600;
    enum Decision {WAIT_WITHIN_PREINPUT_BUDGET,REQUEST_ONE_SECTION_REVEAL,CURRENT_PAIR_STABLE,REFUSE}

    /** These are caller observations, not identity or ready/write acknowledgements. */
    static final class Scope {
        final Boolean timeoutOriginal,ownerVerified,readyCreated,inputDispatched;
        final int uid,window;
        Scope(Boolean timeout,Boolean owned,Boolean ready,Boolean input,int uid,int window) {
            timeoutOriginal=timeout;ownerVerified=owned;readyCreated=ready;inputDispatched=input;
            this.uid=uid;this.window=window;
        }
    }

    private final long start,deadline;
    private final int uid,window;
    private long last,since;
    private int samples;
    private int[][] geometry;
    private boolean refused,revealRequested,revealPending;

    OriginalSyncTimeoutPreinputGeometry(long startedUptimeMs,int expectedUid,int expectedWindow) {
        start=startedUptimeMs;last=start;uid=expectedUid;window=expectedWindow;
        refused=start<0 || start>Long.MAX_VALUE-BUDGET_MS || uid<10000 || uid>99999 || window<0;
        deadline=refused?0:start+BUDGET_MS;
    }

    Decision observe(Scope scope,long now,int[] ownWindow,int[] button,int[] buttonClip,
            int[] section,int[] sectionClip) {
        if(!advance(scope,now) || revealPending)return refuse();
        int[][] current={ownWindow,button,buttonClip,section,sectionClip};
        for(int[] rect:current)if(!rectangle(rect))return refuse();
        if(!contains(ownWindow,buttonClip) || !contains(ownWindow,sectionClip))return refuse();
        // Revealing a section cannot certify an unexposed original button.
        if(!contains(buttonClip,button))return refuse();
        if(!contains(sectionClip,section)) {
            reset();
            if(revealRequested || now==deadline)return refuse();
            revealRequested=true;revealPending=true;
            return Decision.REQUEST_ONE_SECTION_REVEAL;
        }
        if(!same(current)) {
            geometry=new int[current.length][];
            for(int i=0;i<current.length;i++)geometry[i]=current[i].clone();
            samples=1;since=now;
        } else if(samples<3)samples++;
        if(samples>=3 && now-since>=STABLE_MS)return Decision.CURRENT_PAIR_STABLE;
        return now==deadline?refuse():Decision.WAIT_WITHIN_PREINPUT_BUDGET;
    }

    /** True permits fresh observations only; it is neither fit nor an action ACK. */
    Decision revealReturned(Scope scope,Boolean returned,long now) {
        if(!advance(scope,now) || !revealPending || !Boolean.TRUE.equals(returned) || now==deadline)
            return refuse();
        revealPending=false;reset();
        return Decision.WAIT_WITHIN_PREINPUT_BUDGET;
    }

    private boolean advance(Scope scope,long now) {
        if(refused || scope==null || !Boolean.TRUE.equals(scope.timeoutOriginal)
                || !Boolean.TRUE.equals(scope.ownerVerified) || !Boolean.FALSE.equals(scope.readyCreated)
                || !Boolean.FALSE.equals(scope.inputDispatched) || scope.uid!=uid || scope.window!=window
                || now<start || now<last || now>deadline)return false;
        last=now;return true;
    }
    private static boolean rectangle(int[] rect) {
        return rect!=null && rect.length==4 && rect[0]>=0 && rect[1]>=0
            && rect[2]>rect[0] && rect[3]>rect[1];
    }
    private static boolean contains(int[] outer,int[] inner) {
        return inner[0]>=outer[0] && inner[1]>=outer[1] && inner[2]<=outer[2] && inner[3]<=outer[3];
    }
    private boolean same(int[][] current) {
        if(geometry==null)return false;
        for(int i=0;i<current.length;i++)for(int j=0;j<4;j++)
            if(current[i][j]!=geometry[i][j])return false;
        return true;
    }
    private void reset() {samples=0;geometry=null;}
    private Decision refuse() {
        refused=true;revealPending=false;reset();
        return Decision.REFUSE;
    }
}
