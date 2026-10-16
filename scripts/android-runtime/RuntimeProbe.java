package cc.kronecker.opennexus.testdriver;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
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
    private static final String BACK_DRAFT = "# 系统返回键草稿 😀\nBack keeps the native draft before leaving.\n";
    private Bundle arguments;
    private UiAutomation ui;
    private final StringBuilder progress = new StringBuilder();
    private String waitingFor = "initialization";

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
                case "theme-dark": selectTheme("System", "Dark"); break;
                case "theme-light": selectTheme("Dark", "Light"); break;
                case "theme-sepia": selectTheme("Light", "Sepia"); break;
                case "theme-system": selectTheme("Sepia", "System"); break;
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
                case "back-draft":
                    create("New note", "runtime-back.md");
                    setText(editable(null), BACK_DRAFT);
                    hideKeyboard();
                    pressBack();
                    waitLabel("New note");
                    break;
                case "back-recover":
                    click("Recover drafts", true);
                    click("runtime-back.md", true);
                    require(string(editable(null).getText()).contains("系统返回键草稿"), "Back draft did not survive process termination");
                    hideKeyboard();
                    pressBack();
                    waitLabel("New note");
                    pressBack();
                    waitNode(node -> !APP.equals(string(node.getPackageName())));
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
            result.putString("stream", progress + "OPENNEXUS_RUNTIME_PHASE_OK " + phase + "\n");
            result.putString("phase", phase);
            finish(0, result);
        } catch (Throwable error) {
            result.putString("stream", progress + "OPENNEXUS_RUNTIME_FAILED while " + waitingFor
                + "\n" + Log.getStackTraceString(error) + "\n" + tree());
            finish(1, result);
        }
    }

    private void create(String button, String path) {
        click(button, false);
        waitLabel("Create and edit");
        setText(waitNode(node -> node.isEditable() && node.isFocused(), "focused note path"), path);
        click("Create and edit", false);
        waitLabel("Back");
    }

    private void selectTheme(String current, String next) {
        click(current, false);
        click(next, false);
        waitLabel(next);
        waitNode(node -> APP.equals(string(node.getPackageName())) && label(node, "New note", false), "theme returned to the owned vault");
    }

    private void open(String path) { click(path, true); waitLabel("Back"); }
    private void save() { click("Save", false); waitLabel("Saved"); }
    private void waitLabel(String text) { waitNode(node -> label(node, text, false), "label: " + text); }
    private void click(String text, boolean prefix) {
        AccessibilityNodeInfo node = waitNode(value -> label(value, text, prefix) && value.isEnabled(), "enabled action: " + text);
        progress.append("tap: ").append(text).append('\n');
        activate(node);
    }
    private static String string(CharSequence value) { return value == null ? "" : value.toString(); }
    private static boolean label(AccessibilityNodeInfo node, String text, boolean prefix) {
        String actual = string(node.getText()), description = string(node.getContentDescription());
        return actual.equals(text) || description.equals(text)
            || prefix && (actual.startsWith(text + " ") || description.startsWith(text + " "));
    }

    private AccessibilityNodeInfo editable(String original) {
        return waitNode(node -> node.isEditable() && (original == null || string(node.getText()).equals(original)), "editable text");
    }

    private void setText(AccessibilityNodeInfo node, String value) {
        progress.append("set text: ").append(value.length()).append(" UTF-16 units\n");
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        Bundle text = new Bundle();
        text.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text), "Accessibility text input rejected");
        SystemClock.sleep(250);
    }

    private void activate(AccessibilityNodeInfo node) {
        // WebView accepting ACTION_CLICK does not prove that a user tap reached its handler.
        // Send exactly one real tap to the freshly observed target; never replay a write.
        Rect bounds = new Rect(); node.getBoundsInScreen(bounds);
        require(node.isVisibleToUser() && node.isEnabled() && !bounds.isEmpty(), "UI target has no visible enabled bounds");
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

    private boolean keyboardVisible() {
        for (AccessibilityWindowInfo window : ui.getWindows())
            if (window.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) return true;
        return false;
    }

    private void hideKeyboard() {
        if (!keyboardVisible()) return;
        pressBack();
        long until = SystemClock.uptimeMillis() + 3000;
        while (keyboardVisible() && SystemClock.uptimeMillis() < until) SystemClock.sleep(100);
        require(!keyboardVisible(), "IME did not release the Back key");
    }

    private void pressBack() {
        long start = SystemClock.uptimeMillis();
        require(ui.injectInputEvent(new KeyEvent(start, start, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_BACK, 0), true), "Back key down rejected");
        SystemClock.sleep(60);
        require(ui.injectInputEvent(new KeyEvent(start, SystemClock.uptimeMillis(), KeyEvent.ACTION_UP, KeyEvent.KEYCODE_BACK, 0), true), "Back key up rejected");
    }

    private AccessibilityNodeInfo waitNode(Predicate<AccessibilityNodeInfo> predicate) {
        return waitNode(predicate, "observed UI state");
    }

    private AccessibilityNodeInfo waitNode(Predicate<AccessibilityNodeInfo> predicate, String description) {
        waitingFor = description;
        progress.append("wait: ").append(description).append('\n');
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
