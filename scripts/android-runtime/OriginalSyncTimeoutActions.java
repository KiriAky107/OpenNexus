package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;

/** One original input, exact failed UI, then one same-parent release signal. */
final class OriginalSyncTimeoutActions {
    static final String TIMEOUT="cycle-timeout-original",RESUME="cycle-after-timeout-original";
    static final long FAILURE_WAIT_MS=36000,INPUT_WAIT_MS=30000,RELEASE_WAIT_MS=5000;
    interface Guard {void check() throws Exception;}
    interface Check {boolean observed() throws Exception;}
    final String phase,parent,input;
    private enum State {NEW,INPUT_PENDING,INPUT_FINISHED,FAILURE_OBSERVED,RELEASED,COMPLETE,UNKNOWN}
    private State state=State.NEW;
    private long readyAt=-1;
    private boolean inputFinished,failed,released;
    private int[] observedScene;
    private long observedAt=-1;

    OriginalSyncTimeoutActions(String phase,String parent,String input) {
        need(allowed(phase) && OriginalSyncStopActions.canonical(parent)
            && OriginalSyncStopActions.canonical(input) && !parent.equals(input));
        this.phase=phase;this.parent=parent;this.input=input;
    }
    static boolean allowed(String phase) {return TIMEOUT.equals(phase) || RESUME.equals(phase);}
    String expectedAction() {return TIMEOUT.equals(phase)?"cycle-start":"cycle-resume";}
    String begin(String action) {
        need(state==State.NEW && expectedAction().equals(action));state=State.INPUT_PENDING;return input;
    }
    void ready(long elapsed) {
        need(state==State.INPUT_PENDING && readyAt==-1 && elapsed>=0 && elapsed<=Long.MAX_VALUE-FAILURE_WAIT_MS);
        readyAt=elapsed;
    }
    void finished(String action) {
        need(state==State.INPUT_PENDING && readyAt>=0 && expectedAction().equals(action));
        state=State.INPUT_FINISHED;inputFinished=true;
    }
    void observeFailure(Guard owner,Check exactState,LongSupplier clock,LongConsumer pause) throws Exception {
        need(TIMEOUT.equals(phase) && state==State.INPUT_FINISHED);
        try {
            awaitUntil(owner,exactState,clock,pause,readyAt,FAILURE_WAIT_MS);
            state=State.FAILURE_OBSERVED;failed=true;
        } catch(Exception error) {state=State.UNKNOWN;throw error;}
    }
    void awaitRelease(File signal,Guard owner,LongSupplier clock,LongConsumer pause) throws Exception {
        need(TIMEOUT.equals(phase) && state==State.FAILURE_OBSERVED);
        try {
            awaitSignal(signal,owner,clock,pause,RELEASE_WAIT_MS);state=State.RELEASED;released=true;
        } catch(Exception error) {state=State.UNKNOWN;throw error;}
    }
    void complete() {
        need(TIMEOUT.equals(phase)?state==State.RELEASED:state==State.INPUT_FINISHED);state=State.COMPLETE;
    }
    void beforeProductUi() {need(state!=State.UNKNOWN && state!=State.COMPLETE);}
    void recordScene(int[] counts,long elapsed) {
        need(TIMEOUT.equals(phase) && inputFinished && state!=State.UNKNOWN && state!=State.COMPLETE
            && readyAt>=0 && elapsed>=readyAt && elapsed>=observedAt && counts!=null && counts.length==8);
        int[] bounded=new int[8];
        for(int i=0;i<8;i++) {need(counts[i]>=0);bounded[i]=Math.min(2,counts[i]);}
        observedScene=bounded;observedAt=elapsed;
    }
    int[] lastScene() {return observedScene==null?null:observedScene.clone();}
    long lastSceneAt() {return observedAt;}
    long originalReadyAt() {return readyAt;}
    boolean originalInputFinished() {return inputFinished;}
    void close() {if(state!=State.COMPLETE)state=State.UNKNOWN;}
    String summary() {
        return "input_finished="+inputFinished+" failed_cycle_UI_observed="+failed+" network_error_UI_observed="+failed
            +" host_release_signal_observed="+released+" native_timeout_verified=false native_invocation_id_observed=false"
            +" native_cycle_acknowledged=false raw_UI_recorded=false";
    }
    static boolean failedScene(int failures,int disabled,int unknown,int network,int enabledStop,int completed,int stopped,int enabledCycle) {
        return failures==1 && disabled==1 && unknown==1 && network==1 && enabledStop==0 && completed==0 && stopped==0 && enabledCycle==0;
    }
    static void awaitSignal(File signal,Guard owner,LongSupplier clock,LongConsumer pause,long bound) throws Exception {
        need(bound==INPUT_WAIT_MS || bound==RELEASE_WAIT_MS);
        awaitUntil(owner,()->OriginalAuthSignal.complete(signal),clock,pause,clock.getAsLong(),bound);
    }
    private static void awaitUntil(Guard owner,Check state,LongSupplier clock,LongConsumer pause,long start,long bound) throws Exception {
        need(start>=0 && start<=Long.MAX_VALUE-bound);long last=start,until=start+bound;
        while(true) {
            long now=clock.getAsLong();need(now>=last && now<=until);last=now;
            owner.check();now=clock.getAsLong();need(now>=last && now<=until);last=now;
            boolean observed=state.observed();now=clock.getAsLong();need(now>=last && now<=until);last=now;
            if(observed) {
                owner.check();now=clock.getAsLong();need(now>=last && now<=until);return;
            }
            need(now<until);pause.accept(50);
        }
    }
    private static void need(boolean value) {
        if(!value)throw new IllegalStateException("OWNED_SYNC_TIMEOUT_ORIGINAL_SEQUENCE");
    }
}
