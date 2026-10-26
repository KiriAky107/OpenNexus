package cc.kronecker.opennexus.testdriver;

/** One GUI input, then an explicit boundary beyond which product UI is unavailable. */
final class OriginalSyncProcessActions {
    static final String LOSS="cycle-process-loss-original", RESUME="cycle-after-process-loss-original";
    final String phase,parent,input;
    private enum State { NEW, INPUT_PENDING, INPUT_FINISHED, AUTHORIZED, UI_CLOSED, RELEASED, COMPLETE, UNKNOWN }
    private State state=State.NEW;
    private boolean inputFinished,authorized,uiClosed,released;

    OriginalSyncProcessActions(String phase,String parent,String input) {
        need(allowed(phase) && OriginalSyncStopActions.canonical(parent)
            && OriginalSyncStopActions.canonical(input) && !parent.equals(input));
        this.phase=phase;this.parent=parent;this.input=input;
    }
    static boolean allowed(String phase) {return LOSS.equals(phase) || RESUME.equals(phase);}
    String begin(String action) {
        need(state==State.NEW && expectedAction().equals(action));state=State.INPUT_PENDING;return input;
    }
    void finished(String action) {
        need(state==State.INPUT_PENDING && expectedAction().equals(action));
        state=State.INPUT_FINISHED;inputFinished=true;
    }
    void authorized() {
        need(LOSS.equals(phase) && state==State.INPUT_FINISHED);state=State.AUTHORIZED;authorized=true;
    }
    void closeProductUi() {
        need(state==State.AUTHORIZED);state=State.UI_CLOSED;uiClosed=true;
    }
    void released() {
        need(state==State.UI_CLOSED);state=State.RELEASED;released=true;
    }
    void complete() {
        need(LOSS.equals(phase)?state==State.RELEASED:state==State.INPUT_FINISHED);state=State.COMPLETE;
    }
    void beforeProductUi() {need(!uiClosed && state!=State.UNKNOWN && state!=State.COMPLETE);}
    boolean productUiClosed() {return uiClosed;}
    void close() {if(state!=State.COMPLETE)state=State.UNKNOWN;}
    String expectedAction() {return LOSS.equals(phase)?"cycle-start":"cycle-resume";}
    String summary() {
        return "input_finished="+inputFinished+" expected_loss_signal_observed="+authorized
            +" product_UI_observation_closed="+uiClosed+" host_release_signal_observed="+released
            +" process_absence_verified=false native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false";
    }
    private static void need(boolean value) {
        if(!value)throw new IllegalStateException("OWNED_SYNC_PROCESS_ORIGINAL_SEQUENCE");
    }
}
