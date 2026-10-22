package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import java.lang.reflect.Field;

/** Admission, immutable fixture comparison and failure privacy on the actual driver classes. */
public final class ReleaseSyncReadEntryCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("closed peer read contract changed"); }
    private static Bundle phase(String value) { Bundle result=new Bundle();result.putString("phase",value);return result; }
    private static void refused(Bundle args) {
        ReleaseSyncReadRuntimeProbe entry=new ReleaseSyncReadRuntimeProbe();entry.onCreate(args);
        need(entry.starts==0 && entry.finishes==1 && entry.exitCode==1);
        need(entry.result.getString("stream","").equals("OPENNEXUS_RELEASE_SYNC_READ_SCOPE_REFUSED no_product_input=1\n"));
    }
    public static void main(String[] ignored) throws Exception {
        int accepted=0,refused=0,bodyChecks=0;
        Field app=RuntimeProbe.class.getDeclaredField("APP");app.setAccessible(true);
        need(app.get(new RuntimeProbe()).equals("cc.kronecker.opennexus.dev"));
        need(app.get(new ReleaseRuntimeProbe()).equals("cc.kronecker.opennexus"));
        for(String name:new String[]{"release-sync-read-markdown","release-sync-read-canvas"}) {
            ReleaseSyncReadRuntimeProbe entry=new ReleaseSyncReadRuntimeProbe();
            need(app.get(entry).equals("cc.kronecker.opennexus"));
            entry.onCreate(phase(name));need(entry.starts==1 && entry.finishes==0);accepted++;
            ReleaseRuntimeProbe original=new ReleaseRuntimeProbe();original.onCreate(phase(name));
            need(original.starts==0 && original.finishes==1 && original.exitCode==1);
            need(!ReleaseScene.identityMatches(false,name));
        }
        for(String name:new String[]{null,"","markdown","canvas","cycle","release-upgrade-read","release-image-canvas-reopen",
                "release-sync-read-markdown\n","RELEASE-SYNC-READ-CANVAS","../release-sync-read-markdown","release-sync-read-canvas-extra"}) {
            refused(phase(name));refused++;
        }
        refused(null);refused++;refused(new Bundle());refused++;
        for(String name:new String[]{"package","path","body","command","endpoint","password","phase2"}) {
            Bundle args=phase("release-sync-read-markdown");args.putString(name,"forbidden");refused(args);refused++;
        }
        need(ReleaseSyncReadScene.path("release-sync-read-markdown").equals("release-owned-peer-note.md"));
        need(ReleaseSyncReadScene.path("release-sync-read-canvas").equals("release-owned-peer-original.canvas"));
        boolean badPath=false;try { ReleaseSyncReadScene.path("cycle"); } catch(IllegalStateException expected) { badPath=true; }
        need(badPath);
        need(ReleaseSyncReadScene.MARKDOWN.equals(ReleaseSyncReadFixtures.MARKDOWN));
        need(ReleaseSyncReadScene.CANVAS_TEXT.equals(ReleaseSyncReadFixtures.CANVAS_TEXT));
        need(ReleaseSyncReadScene.ASSET.equals(ReleaseSyncReadFixtures.ASSET));
        String original=ReleaseSyncReadFixtures.MARKDOWN;
        for(String body:new String[]{original,original.replace("\r\n","\n"),original.replace("\r\n","\n").replace("\n\n","\n")}) {
            need(ReleaseSyncReadScene.markdownMatches(body));bodyChecks++;
        }
        for(String body:new String[]{null,"",original.substring(1),original+"unexpected",original.replace("😀","😐"),
                original.replace("\r\n","\r"),original.replace("Markdown","Changed")}) {
            need(!ReleaseSyncReadScene.markdownMatches(body));bodyChecks++;
        }
        // The JVM UI stub deliberately throws with a secret sentinel. The real
        // release entry must finish with only a fixed stage, never raw error/UI.
        ReleaseSyncReadRuntimeProbe fail=new ReleaseSyncReadRuntimeProbe();fail.onCreate(phase("release-sync-read-markdown"));fail.onStart();
        need(fail.finishes==1 && fail.exitCode==1);
        need(fail.result.getString("stream","").equals("OPENNEXUS_RELEASE_SYNC_READ_FAILED stage=initialize no_save=1 decoded_image_verified=0\n"));
        need(accepted==2 && refused==20 && bodyChecks==10);
        System.out.println("RELEASE_SYNC_READ_ENTRY_OK accepted=2 refused=20 body_checks=10 old_scope_preserved=1 failure_redacted=1 actual_Android_editor=0 decoded_image_verified=0");
    }
}
