package cc.kronecker.opennexus.testdriver;

/** Fixed test identities and a narrow production-GUI release scope, never arbitrary package input. */
final class ReleaseScene {
    private ReleaseScene() {}
    static String application(boolean release) { return release ? "cc.kronecker.opennexus" : "cc.kronecker.opennexus.dev"; }
    static String note(boolean release) { return release ? "release-owned-note.md" : "runtime-note.md"; }
    static String canvas(boolean release) { return release ? "release-owned-original.canvas" : "runtime.canvas"; }
    static boolean allowed(String phase) {
        return "markdown".equals(phase) || "canvas".equals(phase) || "draft".equals(phase)
            || "canvas-cancel".equals(phase) || "canvas-drag".equals(phase)
            || "auth-read".equals(phase) || "release-upgrade-read".equals(phase);
    }
}
