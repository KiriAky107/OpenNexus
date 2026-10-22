package cc.kronecker.opennexus.testdriver;

/** Deterministic geometry observations; no device, delayed replay or product failure diagnosis. */
public final class OriginalSyncSettleCheck {
    private static void need(boolean value) { if(!value)throw new AssertionError("pre-input stability changed"); }
    public static void main(String[] ignored) {
        TargetStability original=new TargetStability();
        need(!original.observe(10,10,80,50,0));need(!original.observe(10,10,80,50,80));
        need(original.observe(10,10,80,50,160)); // Existing policy, not a runner trace.
        TargetStability sync=new TargetStability(600);
        need(!sync.observe(10,10,80,50,0));need(!sync.observe(10,10,80,50,80));need(!sync.observe(10,10,80,50,160));
        need(!sync.observe(10,40,80,80,400));need(!sync.observe(10,40,80,80,560));
        need(!sync.observe(10,40,80,80,999));need(sync.observe(10,40,80,80,1000));need(sync.stableForMillis()==600);
        need(!sync.observe(0,0,0,0,1100));need(sync.stableForMillis()==0);
        need(!sync.observe(10,40,80,80,1200));need(!sync.observe(10,40,80,80,1190));
        need(!sync.observe(10,40,80,80,1200));need(!sync.observe(10,40,80,80,1789));need(sync.observe(10,40,80,80,1790));
        for(long invalid:new long[]{-1,0,149,1001,Long.MAX_VALUE}) {
            boolean refused=false;try { new TargetStability(invalid); }catch(IllegalArgumentException expected){refused=true;}need(refused);
        }
        System.out.println("ORIGINAL_SYNC_SETTLE_OK checks=6 default_ms=150 sync_ms=600 actual_Android_cause_confirmed=0");
    }
}
