package cc.kronecker.opennexus.testdriver;

/** Closed normal and lost-response auth sequences, not arbitrary auth or Sync phases. */
final class ReleaseAuthScene {
    private ReleaseAuthScene() {}
    static boolean allowed(String phase) {
        return "login-a".equals(phase) || "current".equals(phase)
            || "refresh".equals(phase) || "logout".equals(phase);
    }
    static boolean recoveryAllowed(String phase) {
        return "login-a".equals(phase) || "refresh-unknown".equals(phase) || "current-unusable".equals(phase);
    }
}
