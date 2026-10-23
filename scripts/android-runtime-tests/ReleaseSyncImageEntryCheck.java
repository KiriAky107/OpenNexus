package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import java.lang.reflect.Field;

/** Real new/old entry lifecycle admission with no device or Android UI calls. */
public final class ReleaseSyncImageEntryCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("closed image entry changed");}
    private static Bundle phase(String value){Bundle out=new Bundle();out.putString("phase",value);return out;}
    private static void refused(Bundle args){
        ReleaseSyncImageRuntimeProbe probe=new ReleaseSyncImageRuntimeProbe();probe.onCreate(args);
        need(probe.starts==0 && probe.finishes==1 && probe.exitCode==1);
        need(probe.result.getString("stream","").equals("OPENNEXUS_RELEASE_SYNC_IMAGE_SCOPE_REFUSED no_product_input=1\n"));
    }
    public static void main(String[] ignored) throws Exception {
        Field app=RuntimeProbe.class.getDeclaredField("APP");app.setAccessible(true);
        ReleaseSyncImageRuntimeProbe image=new ReleaseSyncImageRuntimeProbe();
        need(app.get(image).equals("cc.kronecker.opennexus"));
        image.onCreate(phase("release-sync-image"));need(image.starts==1 && image.finishes==0);
        int rejected=0;
        for(String name:new String[]{null,"","release-sync-image\n","RELEASE-SYNC-IMAGE","release-sync-image-cold",
            "release-sync-read-canvas","release-sync-read-markdown","cycle","markdown","canvas","../release-sync-image"}) {
            refused(phase(name));rejected++;
        }
        refused(null);rejected++;refused(new Bundle());rejected++;
        for(String key:new String[]{"package","path","body","crop","coordinates","command","endpoint","nonce","pid"}) {
            Bundle args=phase("release-sync-image");args.putString(key,"forbidden");refused(args);rejected++;
        }
        ReleaseRuntimeProbe old=new ReleaseRuntimeProbe();old.onCreate(phase("release-sync-image"));
        need(old.starts==0 && old.finishes==1 && old.exitCode==1);
        ReleaseSyncReadRuntimeProbe read=new ReleaseSyncReadRuntimeProbe();read.onCreate(phase("release-sync-image"));
        need(read.starts==0 && read.finishes==1 && read.exitCode==1);
        need(!ReleaseScene.identityMatches(false,"release-sync-image"));
        // Stub Context access throws a secret sentinel: only a static stage may escape.
        image.onStart();need(image.finishes==1 && image.exitCode==1);
        need(image.result.getString("stream","").equals("OPENNEXUS_RELEASE_SYNC_IMAGE_FAILED stage=image-intent no_capture_by_driver=1\n"));
        need(rejected==22);
        System.out.println("RELEASE_SYNC_IMAGE_ENTRY_OK accepted=1 refused=22 old_scope_preserved=1 failure_redacted=1 actual_GUI_or_pixels=0");
    }
}
