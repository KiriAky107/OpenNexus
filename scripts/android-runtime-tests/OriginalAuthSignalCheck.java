package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicInteger;

/** Real local file states and the actual reader; no Android, auth or network action. */
public final class OriginalAuthSignalCheck {
    private static void need(boolean value) { if (!value) throw new AssertionError("original signal guard differs"); }
    private static void write(File file, String text) {
        try { Files.write(file.toPath(),text.getBytes(StandardCharsets.UTF_8)); }
        catch (Exception error) { throw new AssertionError(error); }
    }
    public static void main(String[] ignored) throws Exception {
        Path root=Files.createTempDirectory("owned-auth-signal-");
        File file=root.resolve("signal").toFile();int checked=0;
        try {
            need(!OriginalAuthSignal.complete(file));checked++;
            write(file,"");need(!OriginalAuthSignal.complete(file));checked++;
            write(file,"f");need(!OriginalAuthSignal.complete(file));checked++;
            AtomicLong clock=new AtomicLong();AtomicInteger pauses=new AtomicInteger();
            OriginalAuthSignal.await(file,clock::get,delay->{
                need(delay==100);int count=pauses.incrementAndGet();clock.addAndGet(delay);
                write(file,count==1?"finish":"finish\n");
            });
            need(pauses.get()==2 && OriginalAuthSignal.complete(file));checked++;
            for(String invalid:new String[]{"finishx","finish\r","finish\nX"}) {
                write(file,invalid);boolean refused=false;
                try { OriginalAuthSignal.complete(file); } catch(IllegalStateException expected) { refused=true; }
                need(refused);checked++;
            }
            Files.delete(file.toPath());Files.createDirectory(file.toPath());boolean refused=false;
            try { OriginalAuthSignal.complete(file); } catch(IllegalStateException expected) { refused=true; }
            need(refused);Files.delete(file.toPath());checked++;
            clock.set(0);pauses.set(0);refused=false;
            try { OriginalAuthSignal.await(file,clock::get,delay->{pauses.incrementAndGet();clock.addAndGet(30000);}); }
            catch(IllegalStateException expected) { refused=true; }
            need(refused && pauses.get()==3);checked++;
            System.out.println("ORIGINAL_AUTH_SIGNAL_OK checks="+checked+" actual_Android_or_auth=0");
        } finally { Files.deleteIfExists(file.toPath());Files.delete(root); }
    }
}
