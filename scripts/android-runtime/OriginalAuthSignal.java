package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;

/** One bounded static test-driver acknowledgement; never a product data file. */
final class OriginalAuthSignal {
    private static final byte[] EXPECTED = "finish\n".getBytes(StandardCharsets.UTF_8);
    private OriginalAuthSignal() {}

    static boolean complete(File signal) throws Exception {
        if (!signal.exists()) return false;
        if (!signal.isFile() || Files.isSymbolicLink(signal.toPath()) || signal.length() > EXPECTED.length)
            throw new IllegalStateException("Original background signal shape changed");
        // ADB may create the file before its stdin bytes arrive. Existence or
        // a short prefix is not completion and must not abort this same wait.
        if (signal.length() < EXPECTED.length) return false;
        if (!Arrays.equals(Files.readAllBytes(signal.toPath()), EXPECTED))
            throw new IllegalStateException("Original background signal changed");
        return true;
    }

    static void await(File signal, LongSupplier clock, LongConsumer pause) throws Exception {
        long until = clock.getAsLong() + 90000;
        while (clock.getAsLong() < until) {
            if (complete(signal)) return;
            pause.accept(100);
        }
        throw new IllegalStateException("Original background signal incomplete");
    }
}
