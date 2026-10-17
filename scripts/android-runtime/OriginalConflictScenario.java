package cc.kronecker.opennexus.testdriver;

/** Original synthetic content shared by the actual editing/review drivers. */
public final class OriginalConflictScenario {
    private OriginalConflictScenario() {}
    public static final String LOCAL="# Android 本机并发修改 😀\n\n已确认保存的本地版本。\n";
    public static final String DIRTY=LOCAL+"未保存的独立草稿，不能自动上传或丢弃。\n";
    public static final String REMOTE="# 独立客户端并发修改 😀\n\n明确审核后才采用的远端版本。\n";
    public static final String COPY="runtime-conflict-copy.md";
}
