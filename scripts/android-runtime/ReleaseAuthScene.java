package cc.kronecker.opennexus.testdriver;

/** Only the normal persisted-session GUI sequence, not arbitrary auth or Sync phases. */
final class ReleaseAuthScene {
    private ReleaseAuthScene() {}
    static boolean allowed(String phase) {
        return "login-a".equals(phase) || "current".equals(phase)
            || "refresh".equals(phase) || "logout".equals(phase);
    }
}
