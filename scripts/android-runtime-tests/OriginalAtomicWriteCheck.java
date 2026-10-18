package cc.kronecker.opennexus.testdriver;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public final class OriginalAtomicWriteCheck {
    public static void main(String[] ignored)throws Exception {
        if(!OriginalAtomicWrite.UPDATED.startsWith(OriginalAtomicWrite.ORIGINAL)
            ||OriginalAtomicWrite.UPDATED.equals(OriginalAtomicWrite.ORIGINAL)
            ||!OriginalAtomicWrite.PATH.equals("atomic-area/original.md"))throw new AssertionError();
        System.out.println("ORIGINAL_ATOMIC_WRITE_DATA_OK");
        for(String value:new String[]{OriginalAtomicWrite.PATH,OriginalAtomicWrite.ORIGINAL,OriginalAtomicWrite.UPDATED})
            System.out.println(java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))));
    }
}
