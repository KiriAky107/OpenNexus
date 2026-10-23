package cc.kronecker.opennexus.testdriver;

/** Closed static guard observations only: no AX strings, errors or coordinates. */
final class OriginalSyncImageDiagnostic {
    enum Guard { UNSAMPLED, ACTIVE_ROOT, KEYBOARD, WINDOW, PASSWORD, TITLE_IMAGE, CANVAS,
        IMAGE_IN_WINDOW, FILE_PANEL, CONTENT_OVERLAP, WINDOW_OVERLAP, DISPLAY, REGION, FRAME_CANDIDATE, UNAVAILABLE }
    enum Overlap { UNOBSERVED, IMAGE_DESCENDANT, IMAGE_REFERENCE, PEER_TEXT, CONNECTIONS, CANVAS_LABEL,
        CANVAS_TITLE, OTHER_IMAGE, OTHER_TEXT, OTHER_BUTTON, OTHER_EDITABLE, OTHER_CONTAINER, OTHER_OWNED, OTHER_PACKAGE }
    enum ContainerRole { UNOBSERVED, VIEW, VIEW_GROUP, WEB_VIEW, OTHER }
    enum Anchor { UNOBSERVED, NONE, CONNECTIONS, CANVAS, IMAGE_REFERENCE, PEER_TEXT, TITLE, UNVERIFIED }
    interface Ancestor {
        boolean sameOwnerWindow();
        boolean password();
        Anchor fixedAnchor();
        Ancestor parent();
    }
    static final class Context {
        final Anchor anchor;final int depth;
        private Context(Anchor anchor,int depth){this.anchor=anchor;this.depth=depth;}
    }
    static Context context(Ancestor node) {
        int depth=0;
        for(;depth<64 && node!=null;depth++,node=node.parent()) {
            // Never request labels across an owner/window or password boundary.
            if(!node.sameOwnerWindow() || node.password())return new Context(Anchor.UNVERIFIED,depth);
            Anchor anchor=node.fixedAnchor();
            if(anchor==null || anchor==Anchor.UNOBSERVED || anchor==Anchor.UNVERIFIED)
                throw new IllegalStateException("OWNED_IMAGE_DIAGNOSTIC_REFUSED");
            if(anchor!=Anchor.NONE)return new Context(anchor,depth);
        }
        return new Context(Anchor.UNVERIFIED,depth);
    }
    private Guard last=Guard.UNSAMPLED;
    private int titles=-1,images=-1,samples,emitted;
    private Overlap overlap=Overlap.UNOBSERVED;
    private int children=-1,overlapSamples;
    private boolean clickable,editable;
    private ContainerRole role=ContainerRole.UNOBSERVED;
    private Anchor anchor=Anchor.UNOBSERVED;
    private int depth=-1,contextSamples;
    String context(ContainerRole observedRole,Context observed) {
        if(observedRole==null || observedRole==ContainerRole.UNOBSERVED || observed==null)
            throw new IllegalStateException("OWNED_IMAGE_DIAGNOSTIC_REFUSED");
        contextSamples=Math.min(4096,contextSamples+1);
        boolean changed=role!=observedRole || anchor!=observed.anchor || depth!=observed.depth;
        role=observedRole;anchor=observed.anchor;depth=observed.depth;
        if(!changed || emitted>=32)return null;
        emitted++;return "OPENNEXUS_IMAGE_OVERLAP_CONTEXT role="+role.name()+" anchor="+anchor.name()+" depth_capped64="+depth;
    }
    String contextSummary() {
        return "OPENNEXUS_IMAGE_OVERLAP_CONTEXT_SUMMARY last_role="+role.name()+" last_anchor="+anchor.name()
            +" depth_capped64="+depth+" samples_capped4096="+contextSamples+" shared_transitions_emitted="+emitted
            +" raw_UI_exported=0 overlap_rejection_unchanged=1";
    }
    String overlap(Overlap kind,boolean isClickable,boolean isEditable,int childCount) {
        if(kind==null || kind==Overlap.UNOBSERVED || childCount<0)throw new IllegalStateException("OWNED_IMAGE_DIAGNOSTIC_REFUSED");
        overlapSamples=Math.min(4096,overlapSamples+1);
        int bounded=Math.min(64,childCount);
        boolean changed=kind!=overlap || isClickable!=clickable || isEditable!=editable || bounded!=children;
        overlap=kind;clickable=isClickable;editable=isEditable;children=bounded;
        if(!changed || emitted>=32)return null;
        emitted++;return "OPENNEXUS_IMAGE_OVERLAP_GUARD kind="+overlap.name()+" clickable="+(clickable?1:0)
            +" editable="+(editable?1:0)+" children_capped64="+children;
    }
    String overlapSummary() {
        return "OPENNEXUS_IMAGE_OVERLAP_SUMMARY last_kind="+overlap.name()+" clickable="+(clickable?1:0)
            +" editable="+(editable?1:0)+" children_capped64="+children+" samples_capped4096="+overlapSamples
            +" shared_transitions_emitted="+emitted+" raw_UI_exported=0 overlap_rejection_unchanged=1";
    }
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
