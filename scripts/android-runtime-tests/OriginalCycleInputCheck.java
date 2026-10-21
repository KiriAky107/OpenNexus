package cc.kronecker.opennexus.testdriver;

import java.util.concurrent.atomic.AtomicInteger;

/** Real local counter/once-only execution, never a device/Native delivery claim. */
public final class OriginalCycleInputCheck {
    private static void need(boolean value) { if(!value)throw new AssertionError("original cycle input evidence changed"); }
    public static void main(String[] ignored) throws Exception {
        OriginalCycleInput none=new OriginalCycleInput();
        need(none.closeAndSummary().contains("dispatch_attempted=false dispatch_returned=false owned_events=0"));
        OriginalCycleInput foreign=new OriginalCycleInput();foreign.event(false,true,true,true);
        need(foreign.closeAndSummary().contains("owned_events=0 exact_view_clicks=0 content_changes=0"));
        OriginalCycleInput other=new OriginalCycleInput();other.event(true,true,false,false);
        need(other.closeAndSummary().contains("owned_events=1 exact_view_clicks=0"));
        OriginalCycleInput original=new OriginalCycleInput();AtomicInteger actions=new AtomicInteger();
        original.dispatch(()->{actions.incrementAndGet();original.event(true,true,true,true);});
        boolean refused=false;try { original.dispatch(actions::incrementAndGet); } catch(IllegalStateException expected) { refused=true; }
        String summary=original.closeAndSummary();
        need(refused && actions.get()==1 && summary.contains("dispatch_attempted=true dispatch_returned=true")
            && summary.contains("exact_view_clicks=1") && summary.contains("native_cycle_acknowledged=false raw_event_fields_recorded=false"));
        original.event(true,true,true,true);need(original.closeAndSummary().equals(summary));
        OriginalCycleInput unknown=new OriginalCycleInput();AtomicInteger attempts=new AtomicInteger();
        try { unknown.dispatch(()->{attempts.incrementAndGet();throw new Exception("unknown original dispatch");}); }
        catch(Exception expected) {}
        refused=false;try { unknown.dispatch(attempts::incrementAndGet); } catch(IllegalStateException expected) { refused=true; }
        need(refused && attempts.get()==1 && unknown.closeAndSummary().contains("dispatch_attempted=true dispatch_returned=false"));
        OriginalCycleInput bounded=new OriginalCycleInput();for(int i=0;i<4096;i++)bounded.event(true,true,true,true);
        need(bounded.closeAndSummary().contains("owned_events=1024 exact_view_clicks=1024 content_changes=1024"));
        System.out.println("ORIGINAL_CYCLE_INPUT_OK checks=7 actual_Android_or_Native_cycle=0");
    }
}
