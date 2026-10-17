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
        System.out.println("TARGET_STABILITY_OK moving/empty/backward-clock/repeated-time/final-original-bounds");
    }
}
