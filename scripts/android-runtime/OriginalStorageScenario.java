package cc.kronecker.opennexus.testdriver;

/** New owned file bytes; no product command, hook or filesystem write. */
final class OriginalStorageScenario {
    static final String PATH = "full-area/original.md";
    static final String ORIGINAL = "# 原始文件 😀\nA confirmed original before the owned filesystem is full.\n";
    static final String UPDATED = ORIGINAL + "空间不足时保留此草稿 / retry this exact original content\n";
}
