package cc.kronecker.opennexus.testdriver;

/** Closed static guard observations only: no AX strings, errors or coordinates. */
final class OriginalSyncImageDiagnostic {
    enum Guard { UNSAMPLED, ACTIVE_ROOT, KEYBOARD, WINDOW, PASSWORD, TITLE_IMAGE, CANVAS,
        IMAGE_IN_WINDOW, FILE_PANEL, CONTENT_OVERLAP, WINDOW_OVERLAP, DISPLAY, REGION, FRAME_CANDIDATE, UNAVAILABLE }
    private Guard last=Guard.UNSAMPLED;
    private int titles=-1,images=-1,samples,emitted;
    String observe(Guard guard,int titleCount,int imageCount) {
        if(guard==null || titleCount < -1 || imageCount < -1)throw new IllegalStateException("OWNED_IMAGE_DIAGNOSTIC_REFUSED");
        samples=Math.min(4096,samples+1);
        int t=Math.min(64,titleCount),i=Math.min(64,imageCount);
        boolean changed=guard!=last || t!=titles || i!=images;
        last=guard;titles=t;images=i;
        if(!changed || emitted>=32)return null;
        emitted++;return "OPENNEXUS_IMAGE_GEOMETRY_GUARD guard="+last.name()+" titles_capped64="+titles+" images_capped64="+images;
    }
    String summary() {
        return "OPENNEXUS_IMAGE_GEOMETRY_SUMMARY last_guard="+last.name()+" titles_capped64="+titles+" images_capped64="+images
            +" samples_capped4096="+samples+" transitions_emitted="+emitted+" raw_UI_exported=0 geometry_admission_unchanged=1";
    }
}
