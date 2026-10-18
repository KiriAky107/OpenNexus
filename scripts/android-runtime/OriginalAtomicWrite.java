package cc.kronecker.opennexus.testdriver;

/** Exact fresh owned write bytes, not an injected product implementation. */
final class OriginalAtomicWrite {
    static final String PATH="atomic-area/original.md";
    static final String ORIGINAL="# 原始文件 😀\nAtomic replacement must retain this confirmed file.\n";
    static final String UPDATED=ORIGINAL+"写入过程中断 / recover this exact pending operation\n";
}
