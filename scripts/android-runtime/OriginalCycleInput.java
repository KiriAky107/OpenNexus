package cc.kronecker.opennexus.testdriver;

/** Bounded static input/event facts. A view click never proves a Native cycle result. */
public final class OriginalCycleInput {
    public interface Action { void run() throws Exception; }
    private int ownedEvents, exactClicks, contentChanges;
    private boolean attempted, returned, closed;
    public synchronized void event(boolean owned,boolean click,boolean exactLabel,boolean contentChanged) {
        if(closed || !owned)return;
        ownedEvents=Math.min(1024,ownedEvents+1);
        if(click && exactLabel)exactClicks=Math.min(1024,exactClicks+1);
        if(contentChanged)contentChanges=Math.min(1024,contentChanges+1);
    }
    public void dispatch(Action action) throws Exception {
        synchronized(this) {
            if(attempted || closed)throw new IllegalStateException("Original cycle input already attempted; no retry");
            attempted=true;
        }
        action.run();
        synchronized(this) { returned=true; }
    }
    public synchronized String closeAndSummary() {
        closed=true;
        return "dispatch_attempted="+attempted+" dispatch_returned="+returned+" owned_events="+ownedEvents
            +" exact_view_clicks="+exactClicks+" content_changes="+contentChanges
            +" native_cycle_acknowledged=false raw_event_fields_recorded=false";
    }
}
