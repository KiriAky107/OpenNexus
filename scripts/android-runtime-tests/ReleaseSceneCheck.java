package cc.kronecker.opennexus.testdriver;

/** Pure JVM boundaries only; does not instantiate Android or claim a GUI execution. */
public final class ReleaseSceneCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("release scope changed"); }
    public static void main(String[] args) {
        need(ReleaseScene.application(false).equals("cc.kronecker.opennexus.dev"));
        need(ReleaseScene.application(true).equals("cc.kronecker.opennexus"));
        need(ReleaseScene.note(false).equals("runtime-note.md") && ReleaseScene.canvas(false).equals("runtime.canvas"));
        need(ReleaseScene.note(true).equals("release-owned-note.md") && ReleaseScene.canvas(true).equals("release-owned-original.canvas"));
        int admitted = 0;
        for (String phase : new String[] {"markdown", "canvas", "draft", "canvas-cancel", "canvas-drag", "auth-read", "release-upgrade-read"}) {
            need(ReleaseScene.allowed(phase)); admitted++;
        }
        for (String phase : new String[] {null, "", "recover", "export", "auth", "keystore", "sync", "external-md-open", "../markdown", "markdown\n", "release-upgrade-read-extra"}) {
            need(!ReleaseScene.allowed(phase));
        }
        System.out.println("RELEASE_SCENE_CONTRACT_OK phases=" + admitted + " actual_Android_GUI_or_upgrade_verified=0");
    }
}
