package cc.kronecker.opennexus.testdriver;

/** Plain JVM check of real driver geometry admission, independent of Android stubs. */
public final class TargetStabilityCheck {
    private static void require(boolean value) { if (!value) throw new AssertionError("Unstable target admitted"); }
    public static void main(String[] args) {
        TargetStability moving=new TargetStability();
        require(!moving.observe(10,10,50,50,0));
        require(!moving.observe(10,10,50,50,80));
        require(!moving.observe(10,30,50,70,160));
        require(!moving.observe(10,30,50,70,240));
        require(moving.observe(10,30,50,70,320));
        require(!moving.observe(10,31,50,71,400));
        require(!moving.observe(10,31,50,71,400));
        require(!moving.observe(10,31,50,71,400));
        require(moving.observe(10,31,50,71,560));
        require(!moving.observe(0,0,0,0,640));
        require(!moving.observe(10,31,50,71,720));
        require(!moving.observe(10,31,50,71,700));
        require(!moving.observe(10,31,50,71,780));
        require(moving.observe(10,31,50,71,860));
        int[] clipped=TouchViewport.intersect(new int[]{10,90,90,130},new int[]{0,0,100,100});
        require(java.util.Arrays.equals(clipped,new int[]{10,90,90,100}));
        require(TouchViewport.intersect(new int[]{10,110,90,130},new int[]{0,0,100,100})==null);
        require(TouchViewport.intersect(null,new int[]{0,0,100,100})==null);
        require(java.util.Arrays.equals(TouchViewport.avoid(new int[]{10,50,90,100},new int[]{0,80,100,150}),new int[]{10,50,90,80}));
        require(TouchViewport.avoid(new int[]{10,50,90,100},new int[]{0,0,100,150})==null);
        require(java.util.Arrays.equals(TouchViewport.avoid(new int[]{10,50,90,100},new int[]{10,50,30,100}),new int[]{30,50,90,100}));
        TouchViewport.RevealBudget reveal=new TouchViewport.RevealBudget();
        require(!reveal.scrollBeforeInput(false,100));
        require(!reveal.scrollBeforeInput(false,599));
        require(reveal.scrollBeforeInput(false,600));
        require(!reveal.scrollBeforeInput(false,700));
        require(!reveal.scrollBeforeInput(true,1100));
        require(reveal.scrollBeforeInput(false,1100));
        require(!reveal.scrollBeforeInput(false,999999));
        TouchViewport.RevealBudget clock=new TouchViewport.RevealBudget();
        require(!clock.scrollBeforeInput(false,-1));
        require(!clock.scrollBeforeInput(false,800));
        require(!clock.scrollBeforeInput(false,700));
        require(!clock.scrollBeforeInput(false,1199));
        require(clock.scrollBeforeInput(false,1200));
        // Synthetic clipping viewport; only the original target rectangle was
        // exported by the failed run. Do not claim its ancestor bounds known.
        require(TouchViewport.verticalExposure(new int[]{89,136,971,159},new int[]{0,160,1080,2200})==-1);
        require(TouchViewport.verticalExposure(new int[]{10,2100,90,2300},new int[]{0,160,1080,2200})==1);
        require(TouchViewport.verticalExposure(new int[]{10,200,90,260},new int[]{0,160,1080,2200})==0);
        require(TouchViewport.verticalExposure(new int[]{10,200,10,260},new int[]{0,160,1080,2200})==0);
        require(TouchViewport.verticalExposure(new int[]{10,200,90,260},null)==0);
        System.out.println("TARGET_STABILITY_OK moving/empty/backward-clock/repeated-time/final-original-bounds/clipped/covered/keyboard/bounded-pre-input-reveal/directional-ancestor");
    }
}
