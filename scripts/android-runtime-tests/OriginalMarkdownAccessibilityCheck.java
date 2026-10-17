package cc.kronecker.opennexus.testdriver;

public final class OriginalMarkdownAccessibilityCheck {
    public static void main(String[] args) {
        String original="# 来自独立 HTTP 客户端 😀\n\n保持 Markdown 原文。\n";
        require(OriginalMarkdownAccessibility.matches(original, original));
        // Exact TextEdit observation from the owned actual cold-reopen artifact.
        String observed="# 来自独立 HTTP 客户端 😀\n保持 Markdown 原文。\n";
        require(OriginalMarkdownAccessibility.matches(observed, original));
        for (String changed : new String[] {null, "", observed.trim(), " "+observed,
                observed.replace("😀", ""), observed.replace("原文", "改动"),
                observed+"extra", observed.replace("\n", "\r\n"), observed.replace("\n", " ")}) {
            require(!OriginalMarkdownAccessibility.matches(changed, original));
        }
        System.out.println("ORIGINAL_MARKDOWN_AX_OK exact-source observed-empty-line-omission altered-content-rejected");
    }
    private static void require(boolean value) {
        if (!value) throw new AssertionError("Original source/AX identity changed");
    }
}
