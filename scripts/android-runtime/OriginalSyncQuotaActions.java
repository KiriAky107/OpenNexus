package cc.kronecker.opennexus.testdriver;

import java.util.function.LongConsumer;
import java.util.function.LongSupplier;

/** Pure new-quota sequence. Supplied reader/owner/clock results are reports;
 * this class performs no file, Android, clock, input, stop or network operation.
 */
final class OriginalSyncQuotaActions {
    static final String PHASE="cycle-quota-signed-original";
    static final long CONTEXT_WAIT_MS=10000,INPUT_WAIT_MS=30000,FAILURE_WAIT_MS=36000;
    static final int MAX_OBSERVATIONS=1001;
    interface Guard {void check() throws Exception;}
    interface SignalRead {SignalSample read() throws Exception;}
    interface SceneRead {SceneSample read() throws Exception;}
    private enum State {NEW,CONTEXT_WAIT,CONTEXT_READY,INPUT_PENDING,READY,INPUT_FINISHED,FAILURE_OBSERVED,COMPLETE,UNKNOWN}
    private enum Channel {CONTEXT,INPUT_FINISH}
    static final class SignalSample {
        private final OriginalSyncQuotaActions owner;
        private final Channel channel;
        private final boolean complete;
        private boolean consumed;
        private SignalSample(OriginalSyncQuotaActions owner,Channel channel,boolean complete) {
            this.owner=owner;this.channel=channel;this.complete=complete;
        }
    }
    static final class SceneSample {
        private final OriginalSyncQuotaActions owner;
        private final int[] counts;
        private final long elapsed;
        private boolean consumed;
        private SceneSample(OriginalSyncQuotaActions owner,int[] counts,long elapsed) {
            this.owner=owner;this.counts=counts;this.elapsed=elapsed;
        }
    }
    final String parent,input;
    private State state=State.NEW;
    private long readyAt=-1,lastElapsed=-1,observedAt=-1,failureAt=-1;
    private boolean inputFinished;
    private int[] observedScene;

    OriginalSyncQuotaActions(String phase,String parent,String input) {
        if(!PHASE.equals(phase) || !canonical(parent) || !canonical(input) || parent.equals(input))
            throw new IllegalStateException("OWNED_SYNC_QUOTA_ORIGINAL_SEQUENCE");
        this.parent=parent;this.input=input;
    }
    private static boolean canonical(String value) {
        return value!=null && value.length()==36 && value.matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}");
    }
    private void need(boolean value) {
        if(!value) {state=State.UNKNOWN;throw new IllegalStateException("OWNED_SYNC_QUOTA_ORIGINAL_SEQUENCE");}
    }
    String contextName() {return "owned-sync-quota-context-"+parent+".finish";}
    String inputFinishName() {return "owned-sync-quota-touch-"+input+".finish";}

    // Capture the Boolean from the same original static7B reader call. Known
    // false includes its absent/short-write wait; null never becomes false.
    SignalSample contextSample(String originalName,Boolean complete) {
        need(state==State.CONTEXT_WAIT && contextName().equals(originalName) && complete!=null);
        return new SignalSample(this,Channel.CONTEXT,complete.booleanValue());
    }
    SignalSample inputFinishSample(String originalName,Boolean complete) {
        need(state==State.READY && inputFinishName().equals(originalName) && complete!=null);
        return new SignalSample(this,Channel.INPUT_FINISH,complete.booleanValue());
    }
    private boolean consume(SignalSample sample,Channel channel) {
        need(sample!=null && sample.owner==this && sample.channel==channel && !sample.consumed);
        sample.consumed=true;return sample.complete;
    }
    private long tick(LongSupplier clock,long previous,long deadline,State expected) {
        need(state==expected);
        long now=clock.getAsLong();
        need(state==expected && now>=previous && now<=deadline);
        return now;
    }
    private long waitSignal(Guard owner,SignalRead signal,LongSupplier clock,LongConsumer pause,
                            long start,long deadline,State expected,Channel channel) throws Exception {
        long last=start;
        for(int i=0;i<MAX_OBSERVATIONS;i++) {
            last=tick(clock,last,deadline,expected);
            owner.check();last=tick(clock,last,deadline,expected);
            SignalSample sample=signal.read();need(state==expected);
            boolean complete=consume(sample,channel);
            last=tick(clock,last,deadline,expected);
            if(complete) {
                owner.check();return tick(clock,last,deadline,expected);
            }
            need(last<deadline && i+1<MAX_OBSERVATIONS);
            pause.accept(50);
        }
        need(false);return -1;
    }
    void awaitContext(Guard owner,SignalRead signal,LongSupplier uptime,LongConsumer pause) throws Exception {
        try {
            need(state==State.NEW && owner!=null && signal!=null && uptime!=null && pause!=null);
            long start=uptime.getAsLong();need(state==State.NEW && start>=0 && start<=Long.MAX_VALUE-CONTEXT_WAIT_MS);
            state=State.CONTEXT_WAIT;
            waitSignal(owner,signal,uptime,pause,start,start+CONTEXT_WAIT_MS,State.CONTEXT_WAIT,Channel.CONTEXT);
            state=State.CONTEXT_READY;
        } catch(Exception | Error error) {state=State.UNKNOWN;throw error;}
    }
    String begin(String action) {
        need(state==State.CONTEXT_READY && "cycle-start".equals(action));
        state=State.INPUT_PENDING;return input;
    }
    void ready(long elapsed) {
        need(state==State.INPUT_PENDING && readyAt==-1 && elapsed>=0 && elapsed<=Long.MAX_VALUE-FAILURE_WAIT_MS);
        readyAt=elapsed;lastElapsed=elapsed;state=State.READY;
    }
    void awaitInputFinish(Guard owner,SignalRead signal,LongSupplier elapsed,LongConsumer pause) throws Exception {
        try {
            need(state==State.READY && owner!=null && signal!=null && elapsed!=null && pause!=null);
            lastElapsed=waitSignal(owner,signal,elapsed,pause,lastElapsed,readyAt+INPUT_WAIT_MS,State.READY,Channel.INPUT_FINISH);
            inputFinished=true;state=State.INPUT_FINISHED;
        } catch(Exception | Error error) {state=State.UNKNOWN;throw error;}
    }
    SceneSample sceneSample(int[] counts,long elapsed) {
        need(state==State.INPUT_FINISHED && counts!=null && counts.length==8
            && elapsed>=readyAt && elapsed>=observedAt && elapsed<=readyAt+FAILURE_WAIT_MS);
        int[] copy=new int[8];
        for(int i=0;i<8;i++) {need(counts[i]>=0);copy[i]=Math.min(2,counts[i]);}
        return new SceneSample(this,copy,elapsed);
    }
    void awaitFailure(Guard owner,SceneRead scene,LongSupplier elapsed,LongConsumer pause) throws Exception {
        try {
            need(state==State.INPUT_FINISHED && owner!=null && scene!=null && elapsed!=null && pause!=null);
            long last=lastElapsed,deadline=readyAt+FAILURE_WAIT_MS;
            for(int i=0;i<MAX_OBSERVATIONS;i++) {
                last=tick(elapsed,last,deadline,State.INPUT_FINISHED);
                owner.check();last=tick(elapsed,last,deadline,State.INPUT_FINISHED);
                SceneSample sample=scene.read();
                need(state==State.INPUT_FINISHED && sample!=null && sample.owner==this && !sample.consumed && sample.elapsed>=last);
                sample.consumed=true;observedScene=sample.counts.clone();observedAt=sample.elapsed;
                last=tick(elapsed,last,deadline,State.INPUT_FINISHED);need(last>=sample.elapsed);
                if(ReleaseSyncQuotaScene.failed(observedScene)) {
                    owner.check();last=tick(elapsed,last,deadline,State.INPUT_FINISHED);
                    lastElapsed=last;failureAt=sample.elapsed;state=State.FAILURE_OBSERVED;return;
                }
                need(last<deadline && i+1<MAX_OBSERVATIONS);
                pause.accept(50);
            }
            need(false);
        } catch(Exception | Error error) {state=State.UNKNOWN;throw error;}
    }
    void complete() {need(state==State.FAILURE_OBSERVED);state=State.COMPLETE;}
    void beforeProductUi() {need(state!=State.UNKNOWN && state!=State.COMPLETE);}
    long originalReadyAt() {return readyAt;}
    long lastSceneAt() {return observedAt;}
    long failureAt() {return failureAt;}
    boolean originalInputFinished() {return inputFinished;}
    int[] lastScene() {return observedScene==null?null:observedScene.clone();}
    String uiSuccessLine() {
        need(state==State.COMPLETE);
        return "OPENNEXUS_SIGNED_QUOTA_UI input_finished=true quota_error_UI_observed=true raw_UI_recorded=false native_original_state_verified=false";
    }
    String phaseSuccessLine() {
        need(state==State.COMPLETE);return "OPENNEXUS_SIGNED_QUOTA_PHASE_OK "+PHASE;
    }
    void close() {if(state!=State.COMPLETE)state=State.UNKNOWN;}
}
