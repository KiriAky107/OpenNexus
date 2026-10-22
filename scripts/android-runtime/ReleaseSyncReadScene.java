package cc.kronecker.opennexus.testdriver;

/** Two immutable incoming documents; no caller-controlled path or body. */
final class ReleaseSyncReadScene {
    private ReleaseSyncReadScene() {}
    static final String MARKDOWN = "# 独立协议客户端 😀\r\n\r\n保留原始 Markdown / signed foreground.\r\n";
    static final String CANVAS_TEXT = "独立客户端画布 😀";
    static final String ASSET = "attachments/14/14be779d5555c7061cacf2f4979b63fb54737c221c723540b6b4dd9e8d13bd29.png";
    static boolean allowed(String phase) {
        return "release-sync-read-markdown".equals(phase) || "release-sync-read-canvas".equals(phase);
    }
    static String path(String phase) {
        if ("release-sync-read-markdown".equals(phase)) return "release-owned-peer-note.md";
        if ("release-sync-read-canvas".equals(phase)) return "release-owned-peer-original.canvas";
        throw new IllegalStateException("Closed peer read phase required");
    }
    static boolean markdownMatches(String actual) {
        // Only the display is normalized. The orchestrator must independently
        // verify original CRLF file bytes, heads, inbox and transfer records.
        return actual != null && OriginalMarkdownAccessibility.matches(actual.replace("\r\n", "\n"), MARKDOWN.replace("\r\n", "\n"));
    }
}
