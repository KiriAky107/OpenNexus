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
        "delete-preview", "delete-apply", "deleted-cold", "seed-renamed"};

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

    static boolean reviewedButton(boolean owned, String role, String text, String description,
                                  boolean enabled, boolean clickable, String wanted) {
        boolean allowed = "Review action".equals(wanted) || "Apply reviewed action".equals(wanted)
            || "Check original result".equals(wanted) || "Cancel".equals(wanted);
        return owned && allowed && enabled && clickable && "android.widget.Button".equals(role)
            && (wanted.equals(text) || wanted.equals(description));
    }

    /** Public AX errors may combine the translated sentence and final invariant code. */
    static boolean reviewedConflict(boolean owned, String role, String text, String description, boolean editable) {
        if (!owned || editable || !("android.view.View".equals(role) || "android.widget.TextView".equals(role))) return false;
        return conflictText(text) || conflictText(description);
    }
    private static boolean conflictText(String value) {
        return value != null && value.length() <= 512 && (value.equals("REVISION_CONFLICT")
            || value.endsWith(" REVISION_CONFLICT") || value.endsWith("\nREVISION_CONFLICT"));
    }

    static boolean documentAction(boolean owned, String role, String text, String description, String wanted) {
        return owned && ("Rename".equals(wanted) || "Delete".equals(wanted) || "Export saved content".equals(wanted))
            && "android.widget.Button".equals(role) && (wanted.equals(text) || wanted.equals(description));
    }
    /** Read again while an expanded menu's AX descendants are still arriving. */
    static int menuDecision(int actions,int expanded,int stableAbsent) {
        if(actions<0||actions>1||expanded< -1||expanded>1||stableAbsent<0||stableAbsent>32)
            throw new IllegalArgumentException("OWNED_MENU_OBSERVATION_REFUSED");
        if(actions==1)return 2;
        return expanded!=1&&stableAbsent>=3?1:0;
    }

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
