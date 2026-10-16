package cc.kronecker.opennexus.testdriver;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;

/** Separate test app drives the installed APK through Android accessibility and real touch events. */
public final class RuntimeProbe extends Instrumentation {
    private static final String APP = "cc.kronecker.opennexus.dev";
    private static final String NOTE = "runtime-note.md";
    private static final String CANVAS = "runtime.canvas";
    private static final String BODY = "# 原始 Markdown 😀\n\n离线正文 / original content\n";
    private static final String DRAFT = BODY + "未保存的恢复草稿 / recovered draft\n";
    private static final String NODE = "中文画布节点 😀 / original canvas text";
    private Bundle arguments;
    private UiAutomation ui;

    @Override public void onCreate(Bundle args) { super.onCreate(args); arguments = args; start(); }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            ui = getUiAutomation();
            AccessibilityServiceInfo info = ui.getServiceInfo();
            info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
            ui.setServiceInfo(info);
            String phase = arguments.getString("phase", "");
            switch (phase) {
                case "markdown":
                    create("New note", NOTE);
                    setText(editable(null), BODY);
                    save();
                    break;
                case "draft":
                    open(NOTE);
                    require(string(editable(null).getText()).contains("离线正文"), "Original note text missing");
                    setText(editable(null), DRAFT);
                    waitLabel("Draft kept on this device");
                    break;
                case "recover":
                    click("Recover drafts", true);
                    click(NOTE, true);
                    require(string(editable(null).getText()).contains("未保存的恢复草稿"), "Recovered draft text missing");
                    save();
                    break;
                case "canvas":
                    create("New canvas", CANVAS);
                    click("Text", false);
                    setText(editable("New text"), NODE);
                    click("Close", false);
                    save();
                    break;
                case "canvas-drag":
                case "canvas-cancel":
                    open(CANVAS);
                    Rect box = new Rect();
                    waitNode(node -> label(node, NODE, false)).getBoundsInScreen(box);
                    drag(box.centerX(), box.centerY(), box.centerX() + 70, box.centerY() + 45,
                        phase.equals("canvas-cancel"));
                    if (phase.equals("canvas-drag")) save();
                    else { SystemClock.sleep(600); waitLabel("Saved"); }
                    break;
                case "export":
                    click("Export saved content", false);
                    click("Prepare content snapshot", false);
                    click("Choose save location", false);
                    // DocumentsUI is Android's real ACTION_CREATE_DOCUMENT destination picker.
                    AccessibilityNodeInfo save = waitNode(node -> !APP.equals(string(node.getPackageName()))
                        && string(node.getPackageName()).contains("documentsui")
                        && string(node.getText()).equalsIgnoreCase("save") && node.isEnabled());
                    activate(save);
                    waitLabel("Saved file bytes verified");
                    break;
                default: throw new IllegalStateException("Unknown runtime phase: " + phase);
            }
            result.putString("stream", "OPENNEXUS_RUNTIME_PHASE_OK " + phase + "\n");
            result.putString("phase", phase);
            finish(0, result);
        } catch (Throwable error) {
            result.putString("stream", "OPENNEXUS_RUNTIME_FAILED " + error + "\n" + tree());
            finish(1, result);
        }
    }

    private void create(String button, String path) {
        click(button, false);
        waitLabel("Create and edit");
        setText(waitNode(node -> node.isEditable() && node.isFocused()), path);
        click("Create and edit", false);
        waitLabel("Back");
    }

    private void open(String path) { click(path, true); waitLabel("Back"); }
    private void save() { click("Save", false); waitLabel("Saved"); }
    private void waitLabel(String text) { waitNode(node -> label(node, text, false)); }
    private void click(String text, boolean prefix) { activate(waitNode(node -> label(node, text, prefix) && node.isEnabled())); }
    private static String string(CharSequence value) { return value == null ? "" : value.toString(); }
    private static boolean label(AccessibilityNodeInfo node, String text, boolean prefix) {
        String actual = string(node.getText()), description = string(node.getContentDescription());
        return actual.equals(text) || description.equals(text)
            || prefix && (actual.startsWith(text + " ") || description.startsWith(text + " "));
    }

    private AccessibilityNodeInfo editable(String original) {
        return waitNode(node -> node.isEditable() && (original == null || string(node.getText()).equals(original)));
    }

    private void setText(AccessibilityNodeInfo node, String value) {
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        Bundle text = new Bundle();
        text.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text), "Accessibility text input rejected");
        SystemClock.sleep(250);
    }

    private void activate(AccessibilityNodeInfo node) {
        AccessibilityNodeInfo clickable = node;
        for (int depth = 0; depth < 5 && clickable != null && !clickable.isClickable(); depth++) clickable = clickable.getParent();
        if (clickable != null && clickable.isEnabled() && clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return;
        Rect bounds = new Rect(); node.getBoundsInScreen(bounds);
        require(!bounds.isEmpty(), "UI target has no visible bounds");
        long time = SystemClock.uptimeMillis();
        touch(time, time, MotionEvent.ACTION_DOWN, bounds.centerX(), bounds.centerY());
        SystemClock.sleep(60);
        touch(time, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, bounds.centerX(), bounds.centerY());
    }

    private void drag(float x, float y, float endX, float endY, boolean cancel) {
        long start = SystemClock.uptimeMillis();
        touch(start, start, MotionEvent.ACTION_DOWN, x, y);
        for (int step = 1; step <= 8; step++) {
            SystemClock.sleep(35);
            touch(start, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE,
                x + (endX - x) * step / 8, y + (endY - y) * step / 8);
        }
        touch(start, SystemClock.uptimeMillis(), cancel ? MotionEvent.ACTION_CANCEL : MotionEvent.ACTION_UP, endX, endY);
    }

    private void touch(long start, long when, int action, float x, float y) {
        MotionEvent event = MotionEvent.obtain(start, when, action, x, y, 0);
        event.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        try { require(ui.injectInputEvent(event, true), "Touch injection rejected"); } finally { event.recycle(); }
    }

    private AccessibilityNodeInfo waitNode(Predicate<AccessibilityNodeInfo> predicate) {
        long until = SystemClock.uptimeMillis() + 25_000;
        do {
            for (AccessibilityNodeInfo node : nodes()) if (node.isVisibleToUser() && predicate.test(node)) return node;
            SystemClock.sleep(200);
        } while (SystemClock.uptimeMillis() < until);
        throw new IllegalStateException("UI target timed out\n" + tree());
    }

    private List<AccessibilityNodeInfo> nodes() {
        List<AccessibilityNodeInfo> found = new ArrayList<>();
        AccessibilityNodeInfo root = ui.getRootInActiveWindow();
        if (root != null) found.add(root);
        for (int index = 0; index < found.size() && found.size() < 3000; index++) {
            AccessibilityNodeInfo node = found.get(index);
            for (int child = 0; child < node.getChildCount(); child++) {
                AccessibilityNodeInfo value = node.getChild(child); if (value != null) found.add(value);
            }
        }
        return found;
    }

    private String tree() {
        StringBuilder text = new StringBuilder();
        if (ui != null) for (AccessibilityNodeInfo node : nodes()) text.append(node.getClassName()).append(" | ")
            .append(node.getText()).append(" | ").append(node.getContentDescription()).append('\n');
        return text.toString();
    }
    private static void require(boolean condition, String message) { if (!condition) throw new IllegalStateException(message); }
}
