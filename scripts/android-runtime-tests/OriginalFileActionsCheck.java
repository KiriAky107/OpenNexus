package cc.kronecker.opennexus.testdriver;

import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

public final class OriginalFileActionsCheck {
    private static int count;
    private static void need(boolean value) { if (!value) throw new AssertionError("OWNED_FILE_ACTION_DATA_FAILED"); count++; }
    private static String hash(String value) throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder out=new StringBuilder(); for(byte b:bytes) out.append(String.format("%02x", b&255)); return out.toString();
    }
    private static void refuses(Runnable action) {
        try { action.run(); } catch (IllegalArgumentException expected) { count++; return; }
        throw new AssertionError("OWNED_FILE_ACTION_DID_NOT_REFUSE");
    }
    public static void main(String[] args) throws Exception {
        for (String kind:new String[]{"md","canvas"}) {
            for (String step:OriginalFileActions.STEPS) need(OriginalFileActions.kind("files-"+kind+"-"+step).equals(kind));
            need(OriginalFileActions.original(kind).equals("fileops-original."+kind));
            need(OriginalFileActions.destination(kind).equals("fileops/资料😀."+kind));
        }
        for(String phase:new String[]{"files-md-retry","files-png-seed","files-md-../seed","files-canvas-","seed"})
            refuses(() -> OriginalFileActions.kind(phase));
        String id="22222222-2222-4222-8222-222222222222", other="11111111-1111-4111-8111-111111111111";
        String digest=hash(OriginalFileActions.UPDATED);
        need(OriginalFileActions.operation("Original operation\n"+id+"\n"+id).equals(id));
        need(OriginalFileActions.expected("Saved version\n"+digest+"\n"+digest).equals(digest));
        refuses(() -> OriginalFileActions.operation(id+"\n"+other));
        refuses(() -> OriginalFileActions.operation("x-"+id));
        refuses(() -> OriginalFileActions.expected(digest+"a"));
        refuses(() -> OriginalFileActions.expected(digest+"\n"+new String(new char[64]).replace('\0','a')));
        refuses(() -> OriginalFileActions.expected("missing"));
        need(!OriginalFileActions.UPDATED.replace("\r\n","").contains("\n"));
        System.out.println("ORIGINAL_FILE_ACTIONS_DATA_OK "+count);
        System.out.println(hash(OriginalFileActions.BODY)+" "+hash(OriginalFileActions.UPDATED)+" "+hash(OriginalFileActions.NODE));
    }
}
