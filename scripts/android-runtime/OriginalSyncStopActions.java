package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.io.FileOutputStream;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;

/** Closed original GUI input order. These IDs are never Native invocations. */
final class OriginalSyncStopActions {
    static final String STOP="cycle-stop-original", RESUME="cycle-after-stop-original";
    interface Guard { void check() throws Exception; }
    final String phase,parent,first,second;
    private int completed;
    private String pending;
    private boolean stopped,released,closed;

    OriginalSyncStopActions(String phase,String parent,String first,String second) {
        need(allowed(phase) && canonical(parent) && canonical(first) && !parent.equals(first));
        need(STOP.equals(phase) ? canonical(second) && !second.equals(parent) && !second.equals(first) : second==null);
        this.phase=phase;this.parent=parent;this.first=first;this.second=second;
    }
    static boolean allowed(String phase) { return STOP.equals(phase) || RESUME.equals(phase); }
    static boolean canonical(String id) {
        return id!=null && id.matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}");
    }
    String begin(String action) {
        need(!closed && pending==null && !stopped && !released);
        String expected=RESUME.equals(phase) ? "cycle-resume" : completed==0 ? "cycle-start" : "cycle-stop";
        need(completed<(STOP.equals(phase)?2:1) && expected.equals(action));
        pending=action;return completed==0?first:second;
    }
    void finished(String action) {
        need(!closed && pending!=null && pending.equals(action));pending=null;completed++;
    }
    void stopped() {
        need(!closed && STOP.equals(phase) && completed==2 && pending==null && !stopped);stopped=true;
    }
    void released() {
        need(!closed && stopped && !released && pending==null);released=true;
    }
    void complete() {
        need(!closed && pending==null && (STOP.equals(phase) ? completed==2 && stopped && released : completed==1));
        closed=true;
    }
    void close() { closed=true; }
    String summary() {
        return "inputs_finished="+completed+" stopped_UI_observed="+stopped+" release_signal_observed="+released
            +" native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false";
    }
    static void writeOnce(File file,byte[] bytes) throws Exception {
        need(bytes!=null && bytes.length>0 && bytes.length<=8192 && file.createNewFile());
        try(FileOutputStream out=new FileOutputStream(file)) {out.write(bytes);out.getFD().sync();}
    }
    static void await(File signal,Guard guard,LongSupplier clock,LongConsumer pause,long bound) throws Exception {
        long start=clock.getAsLong(),last=start;
        need(start>=0 && bound>0 && bound<=30000 && start<=Long.MAX_VALUE-bound);
        long until=start+bound;
        while(true) {
            long now=clock.getAsLong();need(now>=last && now<=until);last=now;
            guard.check();
            if(OriginalAuthSignal.complete(signal)) {guard.check();return;}
            need(now<until);pause.accept(50);
        }
    }
    private static void need(boolean value) {
        if(!value)throw new IllegalStateException("OWNED_SYNC_STOP_ORIGINAL_SEQUENCE");
    }
}
