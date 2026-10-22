package cc.kronecker.opennexus.testdriver;

/** Read-only pre-tap observations; never retries an already dispatched input. */
final class TargetStability {
    private int left, top, right, bottom, samples;
    private long since, last;
    private final long minimumMillis;

    TargetStability() { this(150); }
    TargetStability(long minimumMillis) {
        if(minimumMillis<150 || minimumMillis>1000)throw new IllegalArgumentException("Unbounded pre-input stability policy");
        this.minimumMillis=minimumMillis;
    }
    long stableForMillis() { return samples==0 ? 0 : Math.max(0,last-since); }

    boolean observe(int l, int t, int r, int b, long now) {
        if (r <= l || b <= t || now < 0) { samples = 0; return false; }
        if (samples == 0 || now < last || l != left || t != top || r != right || b != bottom) {
            left = l; top = t; right = r; bottom = b; samples = 1; since = now; last = now;
            return false;
        }
        last = now; samples++;
        return samples >= 3 && now - since >= minimumMillis;
    }
}
