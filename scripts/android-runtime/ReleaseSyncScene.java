package cc.kronecker.opennexus.testdriver;

/** First signed foreground association and exchange scope; no arbitrary Sync actions. */
final class ReleaseSyncScene {
    private ReleaseSyncScene() {}
    static boolean allowed(String phase) {
        return "login-a".equals(phase) || "associate".equals(phase)
            || "association-original".equals(phase) || "status".equals(phase)
            || "cycle".equals(phase) || "cycle-incoming".equals(phase);
    }
}
