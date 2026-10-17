package cc.kronecker.opennexus.testdriver;

/** WebView AX may omit empty source lines; native original bytes are checked separately. */
final class OriginalMarkdownAccessibility {
    static boolean matches(String actual, String original) {
        return actual != null && original != null
            && (actual.equals(original) || actual.equals(original.replace("\n\n", "\n")));
    }
}
