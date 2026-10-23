package cc.kronecker.opennexus.testdriver;

public final class OriginalSyncImageDiagnosticCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("static image observations changed");}
    public static void main(String[] ignored) {
        int cases=0;OriginalSyncImageDiagnostic d=new OriginalSyncImageDiagnostic();
        need(d.summary().contains("last_guard=UNSAMPLED")&&d.summary().contains("samples_capped4096=0"));cases++;
        String value=d.observe(OriginalSyncImageDiagnostic.Guard.TITLE_IMAGE,1,0);
        need(value.equals("OPENNEXUS_IMAGE_GEOMETRY_GUARD guard=TITLE_IMAGE titles_capped64=1 images_capped64=0"));cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.TITLE_IMAGE,1,0)==null);cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.TITLE_IMAGE,1,1).endsWith("images_capped64=1"));cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.CONTENT_OVERLAP,1,1).contains("guard=CONTENT_OVERLAP"));cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.UNAVAILABLE,-1,-1).contains("titles_capped64=-1"));cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.TITLE_IMAGE,Integer.MAX_VALUE,65).endsWith("titles_capped64=64 images_capped64=64"));cases++;
        for(int i=0;i<5000;i++)d.observe(i%2==0?OriginalSyncImageDiagnostic.Guard.PASSWORD:OriginalSyncImageDiagnostic.Guard.WINDOW,1,1);
        need(d.summary().contains("samples_capped4096=4096")&&d.summary().contains("transitions_emitted=32")&&d.summary().contains("last_guard=WINDOW"));cases++;
        need(d.observe(OriginalSyncImageDiagnostic.Guard.FRAME_CANDIDATE,1,1)==null);
        need(d.summary().contains("last_guard=FRAME_CANDIDATE")&&d.summary().contains("raw_UI_exported=0"));cases++;
        for(int mode=0;mode<3;mode++) {
            boolean refused=false;try{d.observe(mode==0?null:OriginalSyncImageDiagnostic.Guard.WINDOW,mode==1?-2:1,mode==2?-2:1);}
            catch(IllegalStateException safe){refused=true;need(!safe.toString().contains("SECRET_CONTEXT_UI_NEVER_EXPORT"));}
            need(refused);cases++;
        }
        for(OriginalSyncImageDiagnostic.Guard reason:OriginalSyncImageDiagnostic.Guard.values()) {
            OriginalSyncImageDiagnostic one=new OriginalSyncImageDiagnostic();String line=one.observe(reason,0,0);
            need(line.length()<160 && line.matches("OPENNEXUS_IMAGE_GEOMETRY_GUARD guard=[A-Z_]+ titles_capped64=0 images_capped64=0"));
        }cases++;
        System.out.println("OWNED_IMAGE_DIAGNOSTIC_OK cases="+cases+" reasons="+OriginalSyncImageDiagnostic.Guard.values().length+" raw_UI_or_capture_exported=0");
    }
}
