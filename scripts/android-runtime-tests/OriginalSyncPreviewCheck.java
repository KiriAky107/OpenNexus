package cc.kronecker.opennexus.testdriver;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

/** Local deterministic waiting states only; no Android input or product preview claim. */
public final class OriginalSyncPreviewCheck {
    private static void need(boolean value) { if(!value)throw new AssertionError("preview observation admission changed"); }
    private static OriginalSyncPreview.State state(int sections,int confirmations,int enabled,int errors,boolean owned) {
        return new OriginalSyncPreview.State(sections,confirmations,enabled,errors,owned);
    }
    private static void reject(OriginalSyncPreview.State value,boolean timeout) {
        AtomicLong clock=new AtomicLong();AtomicInteger reads=new AtomicInteger();List<String> observed=new ArrayList<>();
        boolean rejected=false;
        try { OriginalSyncPreview.await(()->{reads.incrementAndGet();return value;},clock::get,clock::addAndGet,v->observed.add(v.summary())); }
        catch(IllegalStateException expected) { rejected=true; }
        need(rejected && (timeout ? clock.get()==30000 && reads.get()==200 : clock.get()==0 && reads.get()==1));
        need(observed.size()<2); // Never output raw UI, credentials or repeated identical states.
    }
    public static void main(String[] ignored) {
        AtomicLong clock=new AtomicLong();AtomicInteger reads=new AtomicInteger();List<String> observed=new ArrayList<>();
        Supplier<OriginalSyncPreview.State> delayed=()->{
            int n=reads.getAndIncrement();
            return n<2?state(0,0,0,0,true):n<4?state(1,1,0,0,true):state(1,1,1,0,true);
        };
        OriginalSyncPreview.await(delayed,clock::get,clock::addAndGet,v->observed.add(v.summary()));
        need(reads.get()==5 && clock.get()==600 && observed.size()==3);
        reject(state(0,0,0,0,true),true);
        reject(state(1,1,0,0,true),true);
        reject(state(0,1,1,0,true),true);
        reject(state(1,1,1,0,false),false);
        reject(state(-1,0,0,0,true),false);
        reject(state(1,1,1,1,true),false);
        System.out.println("ORIGINAL_SYNC_PREVIEW_OK checks=7 actual_Android_preview=0 input_replayed=0");
    }
}
