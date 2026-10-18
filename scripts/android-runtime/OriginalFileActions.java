package cc.kronecker.opennexus.testdriver;

import java.util.LinkedHashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Data contract for a new owned local file lifecycle; no product invocation. */
final class OriginalFileActions {
    static final String BODY = "# 原始本地笔记 😀\nLocal original before a reviewed file action.\n";
    static final String UPDATED = BODY.replace("\n", "\r\n") + "并发更改必须经新审阅 / preserve CRLF 😀\r\n";
    static final String NODE = "本地画布原节点 😀 / retained node";
    static final String DIRECTORY = "fileops";
    static final String[] STEPS = {"seed", "rename-old", "reject-old", "cancel-old", "reopen",
        "rename-new", "rename-apply", "renamed-cold", "delete-cancel-preview", "delete-cancel",
        "delete-preview", "delete-apply", "deleted-cold"};

    static String kind(String phase) {
        for (String kind : new String[]{"md", "canvas"}) {
            String prefix = "files-" + kind + "-";
            if (phase.startsWith(prefix)) {
                String step = phase.substring(prefix.length());
                for (String allowed : STEPS) if (allowed.equals(step)) return kind;
            }
        }
        throw new IllegalArgumentException("OWNED_FILE_PHASE_REFUSED");
    }

    static String original(String kind) {
        if (!kind.equals("md") && !kind.equals("canvas")) throw new IllegalArgumentException("OWNED_FILE_KIND_REFUSED");
        return "fileops-original." + kind;
    }
    static String destination(String kind) { original(kind); return DIRECTORY + "/资料😀." + kind; }

    static String unique(String text, String regex) {
        if (text == null || text.length() > 32768) throw new IllegalArgumentException("OWNED_FILE_FREEZE_REFUSED");
        Matcher match = Pattern.compile(regex).matcher(text);
        Set<String> values = new LinkedHashSet<>();
        while (match.find()) values.add(match.group());
        if (values.size() != 1) throw new IllegalArgumentException("OWNED_FILE_FREEZE_REFUSED");
        return values.iterator().next();
    }
    static String operation(String text) {
        return unique(text, "(?<![0-9a-f-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f-])");
    }
    static String expected(String text) {
        return unique(text, "(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])");
    }
}
