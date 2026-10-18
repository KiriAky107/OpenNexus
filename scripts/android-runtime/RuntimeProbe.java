package cc.kronecker.opennexus.testdriver;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Build;
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
import java.io.File;
import java.io.FileOutputStream;
import android.content.ComponentName;
import android.content.Intent;
import org.json.JSONArray;
import org.json.JSONObject;

/** Separate test app drives the installed APK through Android accessibility and real touch events. */
public final class RuntimeProbe extends Instrumentation {
    private static final String APP = "cc.kronecker.opennexus.dev";
    private static final String NOTE = "runtime-note.md";
    private static final String CANVAS = "runtime.canvas";
    private static final String BODY = "# 原始 Markdown 😀\n\n离线正文 / original content\n";
    private static final String DRAFT = BODY + "未保存的恢复草稿 / recovered draft\n";
    private static final String NODE = "中文画布节点 😀 / original canvas text";
    private static final String RICH_TEXT = "共享富文本 😀 / native rich writing";
    private static final String RICH_IMAGE_TEXT = "Original rich picker image";
    private static final String IME_TEXT = "中文组合输入 😀";
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
            CompositionIme.attach(getContext());
            String phase = arguments.getString("phase", "");
            switch (phase) {
                case "motion-seed": case "motion-open": case "motion-move": case "motion-resize":
                case "motion-pan": case "motion-pinch": case "motion-cancel": case "motion-takeover":
                case "motion-background": case "motion-cold": case "motion-inspector":
                    originalCanvasMotion(phase); break;
                case "view-seed": case "view-open": case "view-pan": case "view-pinch":
                case "view-cancel": case "view-takeover": case "view-background": case "view-cold": case "view-inspector":
                    originalCanvasMotion(phase); break;
                case "files-md-seed":
                case "files-md-seed-renamed":
                case "files-md-rename-old":
                case "files-md-reject-old":
                case "files-md-cancel-old":
                case "files-md-reopen":
                case "files-md-rename-new":
                case "files-md-rename-apply":
                case "files-md-renamed-cold":
                case "files-md-delete-cancel-preview":
                case "files-md-delete-cancel":
                case "files-md-delete-preview":
                case "files-md-delete-apply":
                case "files-md-deleted-cold":
                case "files-canvas-seed":
                case "files-canvas-seed-renamed":
                case "files-canvas-rename-old":
                case "files-canvas-reject-old":
                case "files-canvas-cancel-old":
                case "files-canvas-reopen":
                case "files-canvas-rename-new":
                case "files-canvas-rename-apply":
                case "files-canvas-renamed-cold":
                case "files-canvas-delete-cancel-preview":
                case "files-canvas-delete-cancel":
                case "files-canvas-delete-preview":
                case "files-canvas-delete-apply":
                case "files-canvas-deleted-cold":
                    localFileActions(phase); break;
                case "search-seed-original":
                case "search-seed-control":
                case "search-browse":
                case "search-query-0":
                case "search-query-1":
                case "search-query-2":
                case "search-query-3":
                case "search-query-4":
                case "search-query-5":
                case "search-open":
                    originalNoteSearch(phase); break;
                case "graph-seed": case "graph-open": case "graph-add-text":
                case "graph-add-file": case "graph-add-link": case "graph-add-group":
                case "graph-connect": case "graph-edge-label": case "graph-copy":
                case "graph-duplicate": case "graph-undo-duplicate": case "graph-paste":
                case "graph-undo": case "graph-redo": case "graph-delete":
                case "graph-undo-delete": case "graph-redo-delete": case "graph-fit": case "graph-cold":
                    originalCanvasGraph(phase); break;
                case "limits-seed": case "limits-list": case "limits-md": case "limits-md-over":
                case "limits-canvas-over-md": case "limits-canvas": case "limits-canvas-over": case "limits-cold":
                    originalDocumentLimits(phase); break;
                case "storage-init": case "atomic-init": waitLabel("Start writing"); break;
                case "canvas-storage-init": waitLabel("Start writing"); break;
                case "canvas-storage-seed": case "canvas-storage-edit": case "canvas-storage-fail":
                case "canvas-storage-check": case "canvas-storage-retry": case "canvas-storage-cold":
                    canvasStorageRecovery(phase); break;
                case "atomic-seed": case "atomic-edit": case "atomic-save": case "atomic-cold":
                    originalAtomicWrite(phase); break;
                case "storage-seed":
                case "storage-fail":
                case "storage-check":
                case "storage-retry":
                case "storage-cold":
                    storageRecovery(phase); break;
                case "performance-create": longNote(false); break;
                case "performance-reopen": longNote(true); break;
                case "theme-dark": selectTheme("System", "Dark"); break;
                case "theme-light": selectTheme("Dark", "Light"); break;
                case "theme-sepia": selectTheme("Light", "Sepia"); break;
                case "theme-system": selectTheme("Sepia", "System"); break;
                case "theme-diagnostic-dark": selectTheme("System", "Dark", false); break;
                case "theme-diagnostic-reset": selectTheme("Dark", "System"); break;
                case "auth-read":
                    clickTitleBar("Settings");
                    click("Sync account", false);
                    waitLabel("Server connection and sign-in");
                    waitLabel("No device connection records yet.");
                    waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
                        && node.isPassword() && string(node.getText()).isEmpty(), "empty password in the shared connection form");
                    progress.append("auth: native current/profile read, no sign-in or credentials entered\n");
                    click("Close", false);
                    waitLabel("Start writing");
                    break;
                case "pair-note-reopen":
                case "pair-note-return":
                case "pair-canvas-reopen":
                case "pair-canvas-return":
                    originalProductPairPhase(phase);
                    break;
                case "markdown":
                    create("New note", NOTE);
                    inputText(noteBody(), BODY);
                    save();
                    break;
                case "rich-markdown":
                    create("New note", "runtime-rich.md");
                    click("Writing", false);
                    AccessibilityNodeInfo rich = richBody();
                    for (AccessibilityNodeInfo node : nodes())
                        require(!node.isVisibleToUser() || !APP.equals(string(node.getPackageName()))
                            || !label(node, "Markdown formatting toolbar", false), "Phone formatting must start collapsed");
                    inputText(rich, RICH_TEXT);
                    rich = richBody();
                    setSelection(rich, 0, RICH_TEXT.length());
                    click("Format", false);
                    clickFormatting("Bold");
                    waitLabel("Draft kept on this device");
                    save();
                    break;
                case "rich-reopen":
                    open("runtime-rich.md");
                    require(string(noteBody().getText()).contains(RICH_TEXT), "Original rich Markdown missing after cold launch");
                    break;
                case "tablet-portrait":
                    open("runtime-rich.md");
                    click("Writing", false);
                    waitNode(node -> APP.equals(string(node.getPackageName())) && label(node,"Markdown note body",false)
                        && string(node.getText()).contains(RICH_TEXT), "original rendered tablet document");
                    click("Format", false);
                    waitLabel("Markdown formatting toolbar");
                    settleUi();
                    break;
                case "tablet-landscape":
                    // Same process, editor and expanded formatting panel after
                    // a real owned display configuration change; no text input.
                    waitNode(node -> APP.equals(string(node.getPackageName())) && label(node,"Markdown note body",false)
                        && string(node.getText()).contains(RICH_TEXT), "same original document after tablet rotation");
                    waitLabel("Markdown formatting toolbar");
                    settleUi();
                    break;
                case "tablet-seed":
                    // Independent layout fixture entered through the real
                    // source editor. This does not claim rich text input passed.
                    create("New note", "runtime-rich.md");
                    inputText(noteBody(), "**" + RICH_TEXT + "**\n");
                    save();
                    break;
                case "rich-image":
                    create("New note", "runtime-rich-image.md");
                    click("Writing", false);
                    if ("yes".equals(arguments.getString("image_checkpoint")))
                        awaitImageObservation("rich-image", "editor");
                    inputText(richBody(), RICH_IMAGE_TEXT);
                    setSelection(richBody(), RICH_IMAGE_TEXT.length(), RICH_IMAGE_TEXT.length());
                    hideKeyboard();
                    clickMenu("More actions");
                    click("Add image", false);
                    chooseOriginalImage();
                    waitLabel("Draft kept on this device");
                    beforeImageSave("rich-image");
                    save();
                    break;
                case "rich-image-reopen":
                    open("runtime-rich-image.md");
                    require(string(noteBody().getText()).contains("![](attachments/"), "Original rich image reference missing after cold launch");
                    break;
                case "draft":
                    open(NOTE);
                    require(string(noteBody().getText()).contains("离线正文"), "Original note text missing");
                    inputText(noteBody(), DRAFT);
                    waitLabel("Draft kept on this device");
                    break;
                case "recover":
                    clickMenu("Main navigation");
                    click("Recover drafts", false);
                    click(NOTE, true);
                    require(string(noteBody().getText()).contains("未保存的恢复草稿"), "Recovered draft text missing");
                    save();
                    break;
                case "ime-seed":
                    // Fresh narrow-scope fixture, not previous recovery acceptance.
                    create("New note", NOTE);
                    inputText(noteBody(), DRAFT);
                    save();
                    break;
                case "ime-compose":
                    open(NOTE);
                    activateNoteBody();
                    CompositionIme.awaitTarget(APP);
                    CompositionIme.selectEnd(APP, DRAFT.length());
                    progress.append("IME: original InputConnection, composing pinyin\n");
                    CompositionIme.compose(APP, "zhongwen");
                    waitLabel("Composing text…");
                    waitNode(node -> label(node, "Save", false) && !node.isEnabled(), "Save disabled during original IME composition");
                    CompositionIme.compose(APP, IME_TEXT);
                    // Android WebView can expose the current EditContext
                    // candidate as a visible child TextView while the editable
                    // root still reports only the committed document. Scope
                    // the observation to that actual named editor, never a
                    // global label, and keep Save disabled until confirmation.
                    waitNode(node -> editorContainsText(node, IME_TEXT), "unconfirmed Chinese composition inside the owned editor");
                    waitLabel("Composing text…");
                    progress.append("IME: confirm Chinese candidate\n");
                    CompositionIme.confirm(APP);
                    waitLabel("Draft kept on this device");
                    hideKeyboard();
                    save();
                    break;
                case "ime-recover":
                    open(NOTE);
                    require(string(noteBody().getText()).contains(IME_TEXT), "Confirmed IME text did not survive process termination");
                    break;
                case "peer-reopen":
                    open("peer-note.md");
                    waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
                        && label(node,"Markdown note body",false)
                        && OriginalMarkdownAccessibility.matches(string(node.getText()),
                            "# 来自独立 HTTP 客户端 😀\n\n保持 Markdown 原文。\n"),
                        "actual original Markdown source presentation (native bytes checked independently)");
                    break;
                case "peer-media-reopen":
                    open("peer.canvas");
                    waitNode(node -> APP.equals(string(node.getPackageName()))
                        && (string(node.getText()).contains("从独立客户端同步的画布 😀")
                            || string(node.getContentDescription()).contains("从独立客户端同步的画布 😀")),
                        "actual incoming original Canvas text after cold launch");
                    waitOriginalImageNode();
                    break;
                case "conflict-local-save":
                    open(NOTE);
                    require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()),DRAFT),"Original uploaded source changed before local edit");
                    inputText(noteBody(),OriginalConflictScenario.LOCAL);
                    save();
                    break;
                case "conflict-dirty-draft":
                    open(NOTE);
                    require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()),OriginalConflictScenario.LOCAL),"Saved local conflict source changed");
                    inputText(noteBody(),OriginalConflictScenario.DIRTY);
                    waitLabel("Draft kept on this device");hideKeyboard();pressBack();waitNotesLanding();
                    break;
                case "conflict-draft-reopen":
                    open(NOTE);
                    waitNode(node->node.isEditable() && label(node,"Markdown note body",false)
                        && OriginalMarkdownAccessibility.matches(string(node.getText()),OriginalConflictScenario.DIRTY),
                        "original dirty draft after reviewed incoming conflict and cold launch");
                    break;
                case "back-draft":
                    create("New note", "runtime-back.md");
                    inputText(noteBody(), BACK_DRAFT);
                    hideKeyboard();
                    pressBack();
                    waitNotesLanding();
                    break;
                case "back-recover":
                    clickMenu("Main navigation");
                    click("Recover drafts", false);
                    click("runtime-back.md", true);
                    require(string(noteBody().getText()).contains("系统返回键草稿"), "Back draft did not survive process termination");
                    hideKeyboard();
                    pressBack();
                    // Closing the recovered editor returns to its drafts tab.
                    // New note exists on both that tab and the notes landing;
                    // it cannot prove that the next Back backgrounds the app.
                    waitNode(node -> APP.equals(string(node.getPackageName())) && node.isClickable()
                        && label(node, "runtime-back.md", true), "original recovered draft row after closing its editor");
                    progress.append("Back transition: recovered editor -> original drafts list\n");
                    pressBack();
                    waitNotesLanding();
                    progress.append("Back transition: drafts list -> notes landing\n");
                    pressBack();
                    waitNode(node -> node.getParent() == null && !string(node.getPackageName()).isEmpty()
                        && !APP.equals(string(node.getPackageName()))
                        && !getContext().getPackageName().equals(string(node.getPackageName())),
                        "foreign active root after Back from the verified notes landing");
                    break;
                case "canvas":
                    create("New canvas", CANVAS);
                    click("Text", false);
                    setText(editable("New text"), NODE);
                    click("Collapse right sidebar", false);
                    save();
                    break;
                case "canvas-tools-seed":
                    create("New canvas", CANVAS);
                    click("Text", false);
                    setText(editable("New text"), NODE);
                    click("Collapse right sidebar", false);
                    save();
                    break;
                case "canvas-tools-phone-open":
                    open(CANVAS);
                    canvasToolsState(true, false);
                    showCanvasTools();
                    break;
                case "canvas-tools-phone-menu":
                    clickMenu("More canvas actions");
                    waitLabel("Copy");
                    break;
                case "canvas-tools-phone-back-menu":
                    pressBack();
                    canvasToolsState(true, true);
                    require(!visibleLabel("Copy"), "Back did not close only the nested Canvas menu");
                    break;
                case "canvas-tools-phone-back-panel":
                    pressBack();
                    canvasToolsState(true, false);
                    waitLabel("Free canvas");
                    break;
                case "canvas-tools-dark":
                    pressBack();
                    waitNotesLanding();
                    selectTheme("System", "Dark");
                    open(CANVAS);
                    showCanvasTools();
                    break;
                case "canvas-tools-light":
                    pressBack();
                    canvasToolsState(true, false);
                    pressBack();
                    waitNotesLanding();
                    selectTheme("Dark", "Light");
                    open(CANVAS);
                    showCanvasTools();
                    break;
                case "canvas-tools-tablet-open-800":
                    // Establish the original document after the initial owned
                    // density configuration and a deliberate cold launch.
                    open(CANVAS);
                    canvasToolsState(false, true);
                    waitLabel("Free canvas");
                    settleUi();
                    break;
                case "canvas-tools-tablet-800":
                case "canvas-tools-tablet-1280":
                    canvasToolsState(false, true);
                    waitLabel("Free canvas");
                    settleUi();
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
                case "markdown-image":
                    create("New note", "runtime-image.md");
                    inputText(noteBody(), "# Original picker image\n");
                    hideKeyboard();
                    clickMenu("More actions");
                    click("Add image", false);
                    chooseOriginalImage();
                    waitNode(node -> node.isEditable() && string(node.getText()).contains("![](attachments/"), "applied original Markdown image reference");
                    waitLabel("Draft kept on this device");
                    save();
                    break;
                case "markdown-image-reopen":
                    open("runtime-image.md");
                    require(string(noteBody().getText()).contains("![](attachments/"), "System-selected original image reference missing after cold launch");
                    hideKeyboard();
                    clickMenu("More actions");
                    click("Read preview", false);
                    break;
                case "canvas-image":
                    create("New canvas", "runtime-image.canvas");
                    clickMenu("More canvas actions");
                    click("Import original image", false);
                    chooseOriginalImage();
                    waitOriginalImageNode();
                    waitLabel("Draft kept on this device");
                    beforeImageSave("canvas-image");
                    save();
                    break;
                case "canvas-image-reopen":
                    open("runtime-image.canvas");
                    waitOriginalImageNode();
                    break;
                case "export":
                    localDocumentAction("Export saved content");
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
        // The real IME can cover the confirmation below the focused path field.
        // Fill that field first, then release the IME before locating the button.
        setText(waitNode(node -> node.isEditable() && node.isFocused(), "focused note path"), path);
        hideKeyboard();
        click("Create and edit", false);
        waitLabel("Back");
    }

    private AccessibilityNodeInfo richBody() {
        return waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
            && label(node, "Markdown note body", false), "shared rich writing body");
    }

    private void setSelection(AccessibilityNodeInfo node, int from, int to) {
        Bundle selection = new Bundle();
        selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, from);
        selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, to);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selection), "Original editor selection rejected");
    }

    private void selectTheme(String current, String next) {
        selectTheme(current, next, true);
    }

    private void selectTheme(String current, String next, boolean close) {
        clickMenu("Settings");
        waitNode(node -> themeCard(node, current) && descendantLabel(node, "Active"), "original active theme card: " + current);
        AccessibilityNodeInfo option = waitNode(node -> themeCard(node, next), "shared desktop theme card: " + next);
        option.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId());
        option = waitNode(node -> themeCard(node, next), "visible shared theme card: " + next);
        // Exactly one real tap; selected state alone does not prove rendered colors.
        activate(option);
        // WebView exposes aria-pressed as ToggleButton, not an Android checkable row.
        // The Active badge must belong to this exact card, not the previous selection.
        waitNode(node -> themeCard(node, next) && descendantLabel(node, "Active"), "selected theme card: " + next);
        retainThemeCards(next);
        if (!close) return;
        click("Close", false);
        waitLabel("New note");
    }

    private boolean themeCard(AccessibilityNodeInfo node, String name) {
        return APP.equals(string(node.getPackageName())) && label(node, name, false)
            && node.isEnabled() && node.isClickable()
            && "android.widget.ToggleButton".equals(string(node.getClassName()));
    }

    private boolean descendantLabel(AccessibilityNodeInfo root, String text) {
        List<AccessibilityNodeInfo> children = new ArrayList<>();
        children.add(root);
        for (int index = 0; index < children.size() && index < 200; index++) {
            AccessibilityNodeInfo node = children.get(index);
            if (index > 0 && node.isVisibleToUser() && label(node, text, false)) return true;
            for (int child = 0; child < node.getChildCount(); child++) {
                AccessibilityNodeInfo value = node.getChild(child);
                if (value != null) children.add(value);
            }
        }
        return false;
    }

    private void retainThemeCards(String current) {
        settleUi();
        File file = new File(getContext().getFilesDir(), "theme-cards-" + current + ".png");
        require(!file.exists(), "Theme card evidence already exists");
        Bitmap bitmap = ui.takeScreenshot();
        require(bitmap != null, "Theme card screenshot unavailable");
        try (FileOutputStream output = new FileOutputStream(file)) {
            require(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output), "Theme card screenshot failed");
            output.flush(); output.getFD().sync();
            progress.append("shared theme cards screenshot: ").append(file.getName()).append('\n');
        } catch (java.io.IOException error) { throw new IllegalStateException("Theme card evidence could not be retained", error); }
        finally { bitmap.recycle(); }
    }

    private boolean documentsUi(AccessibilityNodeInfo node) {
        return string(node.getPackageName()).endsWith(".documentsui");
    }

    private void longNote(boolean reopen) throws Exception {
        org.json.JSONObject report=new org.json.JSONObject();
        report.put("phase",reopen?"performance-reopen":"performance-create");
        report.put("physical_or_commercial_IME",false);
        report.put("input_backend","owned real InputConnection; ordered broadcast acknowledgement");
        long started=SystemClock.elapsedRealtimeNanos();
        if(reopen)open(OriginalLongNoteScenario.PATH);
        else create("New note",OriginalLongNoteScenario.PATH);
        AccessibilityNodeInfo body=noteBody();
        report.put("open_or_create_to_editable_ms",(SystemClock.elapsedRealtimeNanos()-started)/1_000_000.0);
        activateNoteBody(body);
        CompositionIme.awaitTarget(APP);
        if(reopen) {
            // Normal editor navigation, never replacing the whole long note.
            long now=SystemClock.uptimeMillis();
            for(int action:new int[]{KeyEvent.ACTION_DOWN,KeyEvent.ACTION_UP})
                require(ui.injectInputEvent(new KeyEvent(now,SystemClock.uptimeMillis(),action,
                    KeyEvent.KEYCODE_MOVE_END,0,KeyEvent.META_CTRL_ON),true),"Original long-note end navigation rejected");
        }
        org.json.JSONArray durations=new org.json.JSONArray();
        List<String> chunks=reopen?java.util.Collections.singletonList(OriginalLongNoteScenario.MARKER)
            :OriginalLongNoteScenario.chunks(OriginalLongNoteScenario.content());
        for(String chunk:chunks) {
            started=SystemClock.elapsedRealtimeNanos();
            CompositionIme.commit(APP,chunk); // Each original input exactly once; unknown is not retried.
            durations.put((SystemClock.elapsedRealtimeNanos()-started)/1_000_000.0);
        }
        report.put("original_commit_ack_ms",durations);
        started=SystemClock.elapsedRealtimeNanos();
        waitLabel("Draft kept on this device");
        report.put("after_input_to_draft_kept_label_ms",(SystemClock.elapsedRealtimeNanos()-started)/1_000_000.0);
        started=SystemClock.elapsedRealtimeNanos();save();
        report.put("save_to_saved_label_ms",(SystemClock.elapsedRealtimeNanos()-started)/1_000_000.0);
        report.put("timing_scope","UI readiness and original IME acknowledgements, not frame-paint or physical keystroke latency");
        File target=new File(getContext().getFilesDir(),report.getString("phase")+"-metrics.json");
        require(!target.exists(),"Original performance metrics already exist");
        try(FileOutputStream out=new FileOutputStream(target)) {
            out.write(report.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));out.flush();out.getFD().sync();
        }
    }

    private void canvasStorageRecovery(String phase) throws Exception {
        String path="full-area/original.canvas",text="Canvas 空间不足草稿 😀 / preserve this original content";
        switch(phase) {
            case "canvas-storage-seed":create("New canvas",path);save();break;
            case "canvas-storage-edit":
                waitLabel("Free canvas");graphAction("Text");graphValue("Node content",text);graphClosePanel();
                waitLabel("Draft kept on this device");break;
            case "canvas-storage-fail":
                waitLabel("Draft kept on this device");click("Save",false);
                waitLabel("FILESYSTEM_ERROR");waitLabel("Completion is not yet confirmed.");break;
            case "canvas-storage-check":
                waitLabel("Completion is not yet confirmed.");click("Check original result",false);
                waitLabel("Completion is not yet confirmed.");break;
            case "canvas-storage-retry":
                waitLabel("Completion is not yet confirmed.");click("Retry original content",false);waitLabel("Saved");break;
            case "canvas-storage-cold":
                clickTitleBar("Files");
                AccessibilityNodeInfo folder=waitNode(node->label(node,"full-area",false)&&node.isEnabled()&&fileDrawerRow(node),"original owned Canvas directory");
                activate(folder,this::fileDrawerRow);
                AccessibilityNodeInfo file=waitNode(node->label(node,"original.canvas",false)&&node.isEnabled()&&fileDrawerRow(node),"original recovered Canvas file");
                activate(file,this::fileDrawerRow);waitLabel("Free canvas");
                waitNode(node->APP.equals(string(node.getPackageName()))&&label(node,"text: "+text,false)&&!node.isEditable(),"cold original recovered Canvas node");
                waitNode(node->APP.equals(string(node.getPackageName()))&&label(node,"Save",false)&&!node.isEnabled(),"cold original Canvas remains saved");break;
            default:throw new IllegalStateException("Unknown owned Canvas storage phase");
        }
    }

    private void storageRecovery(String phase) throws Exception {
        if (phase.equals("storage-seed")) {
            create("New note", OriginalStorageScenario.PATH);
            inputText(noteBody(), OriginalStorageScenario.ORIGINAL);
            save();
        } else if (phase.equals("storage-fail")) {
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()), OriginalStorageScenario.ORIGINAL),
                "Original storage seed is not open; no input sent");
            inputText(noteBody(), OriginalStorageScenario.UPDATED);
            waitLabel("Draft kept on this device");
            click("Save", false); // Exactly one original Save, never retried here.
            waitLabel("FILESYSTEM_ERROR");
            waitLabel("Completion is not yet confirmed.");
        } else if (phase.equals("storage-check")) {
            waitLabel("Completion is not yet confirmed.");
            click("Check original result", false);
            waitLabel("Completion is not yet confirmed.");
        } else if (phase.equals("storage-retry")) {
            waitLabel("Completion is not yet confirmed.");
            click("Retry original content", false);
            waitLabel("Saved");
        } else {
            require(phase.equals("storage-cold"), "Unknown storage phase");
            clickTitleBar("Files");
            AccessibilityNodeInfo folder=waitNode(node -> label(node,"full-area",false)
                && node.isEnabled() && fileDrawerRow(node), "original owned full-area directory");
            activate(folder, this::fileDrawerRow);
            AccessibilityNodeInfo file=waitNode(node -> label(node,"original.md",false)
                && node.isEnabled() && fileDrawerRow(node), "original recovered note in its directory");
            activate(file, this::fileDrawerRow);
            waitNode(node -> APP.equals(string(node.getPackageName())) && !node.isClickable()
                && label(node,"original.md",false) && titleBarControl(node), "opened original recovered document");
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()), OriginalStorageScenario.UPDATED),
                "Actual cold original storage note differs");
        }
    }

    private void originalAtomicWrite(String phase) throws Exception {
        if(phase.equals("atomic-seed")) {
            create("New note",OriginalAtomicWrite.PATH);inputText(noteBody(),OriginalAtomicWrite.ORIGINAL);save();
        } else if(phase.equals("atomic-edit")) {
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()),OriginalAtomicWrite.ORIGINAL),
                "Original atomic seed is not open; no input sent");
            inputText(noteBody(),OriginalAtomicWrite.UPDATED);waitLabel("Draft kept on this device");
        } else if(phase.equals("atomic-save")) {
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()),OriginalAtomicWrite.UPDATED),
                "Original atomic pending body changed; no Save sent");
            // The parent must deliberately interrupt this one invocation at
            // the original OS replacement boundary. An ordinary success fails.
            atomicTouchMark("before");
            click("Save",false);
            atomicTouchMark("after");
            long until=SystemClock.uptimeMillis()+120_000;
            while(SystemClock.uptimeMillis()<until&&!visibleLabel("Saved"))SystemClock.sleep(100);
            require(visibleLabel("Saved"),"Original atomic Save did not complete; no retry sent");
        } else {
            require(phase.equals("atomic-cold"),"Unknown original atomic phase");clickTitleBar("Files");
            AccessibilityNodeInfo folder=waitNode(node->label(node,"atomic-area",false)&&node.isEnabled()&&fileDrawerRow(node),"owned atomic directory");
            activate(folder,this::fileDrawerRow);
            AccessibilityNodeInfo file=waitNode(node->label(node,"original.md",false)&&node.isEnabled()&&fileDrawerRow(node),"owned atomic original");
            activate(file,this::fileDrawerRow);
            waitNode(node->APP.equals(string(node.getPackageName()))&&!node.isClickable()&&label(node,"original.md",false)
                &&titleBarControl(node),"opened recovered atomic original");
            if(visibleLabel("Completion is not yet confirmed.")) {
                click("Check original result",false);waitLabel("Saved");
            }
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()),OriginalAtomicWrite.UPDATED),"Cold original atomic body differs");
        }
    }

    private void atomicTouchMark(String stage) throws java.io.IOException {
        String nonce=arguments.getString("atomic_nonce","");
        require(nonce.matches("[0-9a-f]{32}")&&(stage.equals("before")||stage.equals("after")),"Original atomic touch scope refused");
        File target=new File(getContext().getFilesDir(),"atomic-"+nonce+"-"+stage+".json");
        require(target.createNewFile(),"Original atomic touch checkpoint already exists");
        String value="{\"nonce\":\""+nonce+"\",\"stage\":\""+stage+"\",\"uptime_ms\":"+SystemClock.uptimeMillis()+"}\n";
        try(FileOutputStream out=new FileOutputStream(target)) {
            out.write(value.getBytes(java.nio.charset.StandardCharsets.US_ASCII));out.flush();out.getFD().sync();
        }
    }

    private void originalDocumentLimits(String phase) throws Exception {
        String[] paths={"limits-exact.md","limits-over.md","limits-above-md.canvas","limits-exact.canvas","limits-over.canvas"};
        if(phase.equals("limits-seed")) {
            create("New note","limits-seed.md");
            inputText(noteBody(),"# Limits seed 原件 😀\nOne actual original GUI Save.\n");save();return;
        }
        if(phase.equals("limits-list")) {
            waitFilePageReady("limits-seed.md");
            if(!visibleFilePanel())clickTitleBar("Files");
            for(String path:paths)waitNode(n->label(n,path,false)&&n.isEnabled()&&fileDrawerRow(n),"owned original byte-limit file: "+path);
            AccessibilityNodeInfo close=waitNode(n->label(n,"Close",false)&&fileDrawerRow(n),"owned file panel Close");
            activate(close,this::fileDrawerRow);return;
        }
        String path;boolean allowed;
        switch(phase) {
            case "limits-md":path=paths[0];allowed=true;break;
            case "limits-md-over":path=paths[1];allowed=false;break;
            case "limits-canvas-over-md":path=paths[2];allowed=true;break;
            case "limits-canvas":case "limits-cold":path=paths[3];allowed=true;break;
            case "limits-canvas-over":path=paths[4];allowed=false;break;
            default:throw new IllegalStateException("Unknown document-limit phase");
        }
        if(!allowed) {
            clickTitleBar("Files");
            AccessibilityNodeInfo file=waitNode(n->label(n,path,false)&&n.isEnabled()&&fileDrawerRow(n),"original oversize file");
            activate(file,this::fileDrawerRow);
            // An unsuccessful open keeps the file drawer for recovery. Close
            // that same owned panel once to read its original scoped feedback.
            AccessibilityNodeInfo close=waitNode(n->label(n,"Close",false)&&fileDrawerRow(n),"owned file panel after refused open");
            activate(close,this::fileDrawerRow);
            waitNode(n->APP.equals(string(n.getPackageName()))&&n.isVisibleToUser()
                &&label(n,"This file exceeds editing limits (Markdown 1 MiB, canvas 4 MiB).",false),"actual original Native size refusal");
        } else {
            open(path);
            if(path.endsWith(".canvas"))waitNode(n->APP.equals(string(n.getPackageName()))&&n.isVisibleToUser()
                &&label(n,"text: Limits 原节点 😀",false),"actual original bounded Canvas node");
            else require(noteBody().isEditable(),"Accepted original Markdown has no editor");
            AccessibilityNodeInfo save=waitNode(n->APP.equals(string(n.getPackageName()))&&n.isVisibleToUser()
                &&label(n,"Save",false),"original saved file Save action");
            require(!save.isEnabled(),"Read-only open enabled a new Save");
        }
        progress.append("ORIGINAL_DOCUMENT_LIMIT path=").append(path).append(" allowed=").append(allowed?1:0).append('\n');
    }

    private void originalNoteSearch(String phase) throws Exception {
        if(phase.equals("search-seed-original")||phase.equals("search-seed-control")) {
            boolean original=phase.equals("search-seed-original");
            create("New note",original?OriginalNoteSearch.PATH:OriginalNoteSearch.CONTROL_PATH);
            inputText(noteBody(),original?OriginalNoteSearch.SEED:OriginalNoteSearch.CONTROL);save();return;
        }
        if(phase.equals("search-browse")) {
            waitSearchLanding();clickTitleBar("Files");
            AccessibilityNodeInfo folder=waitNode(n->label(n,OriginalNoteSearch.DIRECTORY,false)&&n.isEnabled()&&fileDrawerRow(n),"original Chinese directory, not a recent-note card");
            activate(folder,this::fileDrawerRow);
            searchResult(OriginalNoteSearch.NAME,OriginalNoteSearch.CONTROL_NAME,true);
            return;
        }
        if(phase.equals("search-open")) {
            searchOnce(OriginalNoteSearch.NEGATIVE);searchResult(null,null,false);
            searchOnce("资料😀");searchResult(OriginalNoteSearch.NAME,OriginalNoteSearch.CONTROL_NAME,false);
            AccessibilityNodeInfo file=waitNode(n->label(n,OriginalNoteSearch.NAME,false)&&n.isEnabled()&&fileDrawerRow(n),"original literal-search file");
            activate(file,this::fileDrawerRow);
            waitNode(n->APP.equals(string(n.getPackageName()))&&!n.isClickable()&&label(n,OriginalNoteSearch.NAME,false)&&titleBarControl(n),"original searched document title");
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()).replace("\r\n","\n"),OriginalNoteSearch.ORIGINAL.replace("\r\n","\n")),"Actual searched original Markdown body differs");
            return;
        }
        int index=OriginalNoteSearch.queryIndex(phase);
        searchOnce(OriginalNoteSearch.NEGATIVE);searchResult(null,null,false);
        searchOnce(OriginalNoteSearch.QUERIES[index]);
        String expected=OriginalNoteSearch.expected(index),other=index==5?OriginalNoteSearch.NAME:OriginalNoteSearch.CONTROL_NAME;
        searchResult(expected,other,false);
        progress.append("ORIGINAL_NOTE_SEARCH_RESULT ").append(index).append(" negative=0 positive=1\n");
    }
    private void waitSearchLanding() {
        long until=SystemClock.uptimeMillis()+25_000;
        do {
            int fields=0;
            for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser())fields|=OriginalNoteSearch.landingField(
                APP.equals(string(node.getPackageName())),string(node.getClassName()),string(node.getText()),
                string(node.getContentDescription()),node.isEditable(),node.isEnabled(),node.isClickable());
            if(OriginalNoteSearch.landingReady(fields)) {
                progress.append("ORIGINAL_SEARCH_LANDING fields=15 optional_note_shortcuts_not_required\n");return;
            }
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original owned workspace landing was not observed");
    }
    private void searchOnce(String query) {
        AccessibilityNodeInfo input=waitNode(n->APP.equals(string(n.getPackageName()))&&n.isEditable()&&label(n,"Search names and text",false),"owned exact file-search input");
        setText(input,query);hideKeyboard();
        java.util.function.Predicate<AccessibilityNodeInfo> button=n->OriginalNoteSearch.searchButton(APP.equals(string(n.getPackageName())),string(n.getClassName()),string(n.getText()),string(n.getContentDescription()),n.isEnabled(),n.isClickable());
        AccessibilityNodeInfo original=waitNode(button,"owned actual Search submit button");activate(original,button);
    }
    private void searchResult(String expected,String other,boolean both) {
        if(expected==null)waitLabel("No saved notes here");
        else waitNode(n->label(n,expected,false)&&fileDrawerRow(n),"original actual search-result leaf");
        if(both)waitNode(n->label(n,other,false)&&fileDrawerRow(n),"second actual directory leaf");
        int main=0,control=0;
        for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser()&&fileDrawerRow(node)) {
            if(label(node,OriginalNoteSearch.NAME,false))main++;
            if(label(node,OriginalNoteSearch.CONTROL_NAME,false))control++;
        }
        if(expected==null)require(main==0&&control==0,"Empty actual search still has an owned result");
        else if(both)require(main==1&&control==1,"Original nested directory did not show both files");
        else require(main==(expected.equals(OriginalNoteSearch.NAME)?1:0)&&control==(expected.equals(OriginalNoteSearch.CONTROL_NAME)?1:0),"Search is not the exact literal result or leaked the nonmatching control");
    }

    private void localFileActions(String phase) throws Exception {
        String kind=OriginalFileActions.kind(phase), step=phase.substring(("files-"+kind+"-").length());
        boolean canvas=kind.equals("canvas");
        if (step.equals("seed") || step.equals("seed-renamed")) {
            create(canvas?"New canvas":"New note",step.equals("seed-renamed")?OriginalFileActions.destination(kind):OriginalFileActions.original(kind));
            if (canvas) {
                click("Text",false);
                setText(editable("New text"),OriginalFileActions.NODE);
                click("Collapse right sidebar",false);
            } else inputText(noteBody(),OriginalFileActions.BODY);
            save();
        } else if (step.equals("rename-old") || step.equals("rename-new")) {
            hideKeyboard();
            localDocumentAction("Rename");
            setText(waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
                && label(node,"Note path",false), "original reviewed rename path"),OriginalFileActions.destination(kind));
            hideKeyboard();
            localFileButton("Review action");
            waitLabel("Awaiting confirmation");
            recordLocalFileFreeze();
        } else if (step.equals("reject-old")) {
            waitLabel("Awaiting confirmation");
            localFileButton("Apply reviewed action"); // One old frozen action only.
            waitNode(node -> OriginalFileActions.reviewedConflict(APP.equals(string(node.getPackageName())),
                string(node.getClassName()), string(node.getText()), string(node.getContentDescription()), node.isEditable()),
                "original reviewed file public REVISION_CONFLICT");
            localFileButton("Check original result"); // Read, never execute again.
            waitLabel("Requires a new review");
            recordLocalFileFreeze();
        } else if (step.equals("cancel-old") || step.equals("delete-cancel")) {
            localFileButton("Cancel");
            waitLabel("Back");
            waitNode(node -> label(node,"Save",false) && !node.isEnabled(), "original clean editor after cancellation");
        } else if (step.equals("rename-apply") || step.equals("delete-apply")) {
            waitLabel("Awaiting confirmation");
            localFileButton("Apply reviewed action");
            waitLabel("Completed");
        } else if (step.equals("delete-cancel-preview") || step.equals("delete-preview")) {
            localDocumentAction("Delete");
            waitLabel("Awaiting confirmation");
            recordLocalFileFreeze();
        } else if (step.equals("reopen") || step.equals("renamed-cold")) {
            clickTitleBar("Files");
            if (step.equals("renamed-cold")) {
                AccessibilityNodeInfo folder=waitNode(node -> label(node,OriginalFileActions.DIRECTORY,false)
                    && node.isEnabled() && fileDrawerRow(node), "owned renamed file directory");
                activate(folder,this::fileDrawerRow);
            }
            String name=step.equals("reopen")?OriginalFileActions.original(kind):"资料😀."+kind;
            AccessibilityNodeInfo file=waitNode(node -> label(node,name,false) && node.isEnabled()
                && fileDrawerRow(node), "owned original local file leaf");
            activate(file,this::fileDrawerRow);
            waitNode(node -> APP.equals(string(node.getPackageName())) && !node.isClickable()
                && label(node,name,false) && titleBarControl(node), "opened original local file title");
            if (canvas) waitLabel(OriginalFileActions.NODE);
            else require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()).replace("\r\n","\n"),
                    OriginalFileActions.UPDATED.replace("\r\n","\n")), "Original local Markdown UI differs after reopening");
        } else {
            require(step.equals("deleted-cold"), "Unknown owned file action");
            waitLabel("Start writing");
            clickTitleBar("Files");
            settleUi();
            require(!visibleLabel(OriginalFileActions.original(kind)) && !visibleLabel("资料😀."+kind),
                "Deleted own file is still in the fresh root listing");
        }
    }

    private void localDocumentAction(String wanted) {
        Predicate<AccessibilityNodeInfo> named = n -> OriginalFileActions.documentAction(
            APP.equals(string(n.getPackageName())),string(n.getClassName()),string(n.getText()),string(n.getContentDescription()),wanted);
        Predicate<AccessibilityNodeInfo> ready = n -> named.test(n)&&n.isEnabled()&&n.isClickable();
        settleUi();long until=SystemClock.uptimeMillis()+3000;int absent=0;boolean observed=false;
        do {
            List<AccessibilityNodeInfo> visible=new ArrayList<>(),summaries=new ArrayList<>();
            for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser()) {
                if(named.test(node))visible.add(node);
                if(APP.equals(string(node.getPackageName()))&&node.isClickable()&&label(node,"More actions",false))summaries.add(node);
            }
            require(visible.size()<=1&&summaries.size()<=1,"Ambiguous original document menu; no input sent");
            int expanded=-1;
            if(summaries.size()==1&&Build.VERSION.SDK_INT>=36) {
                int state=summaries.get(0).getExpandedState();
                if(state==AccessibilityNodeInfo.EXPANDED_STATE_FULL||state==AccessibilityNodeInfo.EXPANDED_STATE_PARTIAL)expanded=1;
                else if(state==AccessibilityNodeInfo.EXPANDED_STATE_COLLAPSED)expanded=0;
            }
            if(!visible.isEmpty())require(visible.get(0).isEnabled()&&visible.get(0).isClickable(),"Original document action is disabled; no toggle sent");
            absent=visible.isEmpty()&&summaries.size()==1&&expanded!=1?absent+1:0;
            int decision=OriginalFileActions.menuDecision(visible.size(),expanded,absent);
            progress.append("ORIGINAL_DOCUMENT_MENU actions=").append(visible.size()).append(" expanded=").append(expanded).append(" stable_absent=").append(absent).append('\n');
            if(decision==2){observed=true;break;}
            if(decision==1){clickMenu("More actions");observed=true;break;}
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        require(observed,"Original menu state not stable; no toggle or action replayed");
        AccessibilityNodeInfo original=waitNode(ready,"owned document action: "+wanted);activate(original,ready);
    }

    private void localFileButton(String wanted) {
        Predicate<AccessibilityNodeInfo> button = node -> OriginalFileActions.reviewedButton(
            APP.equals(string(node.getPackageName())), string(node.getClassName()), string(node.getText()),
            string(node.getContentDescription()), node.isEnabled(), node.isClickable(), wanted);
        AccessibilityNodeInfo original = waitNode(button, "owned original file action button: " + wanted);
        activate(original, button);
    }

    private void recordLocalFileFreeze() {
        waitLabel("Original operation"); waitLabel("Saved version");
        StringBuilder text=new StringBuilder();
        for (AccessibilityNodeInfo node:nodes()) {
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser()) {
                text.append(string(node.getText())).append('\n');
                require(text.length()<=32768,"Original local review AX exceeds its bound");
            }
        }
        String operation=OriginalFileActions.operation(text.toString());
        String expected=OriginalFileActions.expected(text.toString());
        progress.append("ORIGINAL_FILE_FREEZE ").append(operation).append(' ').append(expected).append('\n');
    }

    private void originalProductPairPhase(String phase) throws Exception {
        if (phase.startsWith("pair-note-")) {
            open(NOTE);
            require(OriginalMarkdownAccessibility.matches(string(noteBody().getText()), OriginalProductPairScenario.DESKTOP_NOTE),
                "Actual original Desktop Markdown is not open; no edit sent");
            if (phase.equals("pair-note-return")) {
                inputText(noteBody(), OriginalProductPairScenario.ANDROID_NOTE);
                save();
            }
            return;
        }
        open("peer.canvas");
        AccessibilityNodeInfo text = waitNode(node -> APP.equals(string(node.getPackageName()))
            && !node.isEditable() && string(node.getText()).equals(OriginalProductPairScenario.DESKTOP_CANVAS_TEXT),
            "original Desktop Canvas text, not a protocol-peer fixture");
        waitNode(node -> APP.equals(string(node.getPackageName()))
            && label(node, OriginalProductPairScenario.IMAGE_PATH, false), "original Desktop Canvas image reference");
        if (phase.equals("pair-canvas-return")) {
            // One actual touch selects the existing node, without moving it.
            activate(text);
            click("Properties", false);
            pairProperty("x", OriginalProductPairScenario.DESKTOP_X, OriginalProductPairScenario.ANDROID_X);
            pairProperty("y", OriginalProductPairScenario.DESKTOP_Y, OriginalProductPairScenario.ANDROID_Y);
            pairProperty("Node content", OriginalProductPairScenario.DESKTOP_CANVAS_TEXT, OriginalProductPairScenario.ANDROID_CANVAS_TEXT);
            // The mobile inspector applies input immediately; the separate
            // Save is the actual durable file operation. No fake Apply click.
            click("Collapse right sidebar", false);
            save();
        }
    }

    private void graphAction(String name) {
        settleUi();long until=SystemClock.uptimeMillis()+25_000;
        int absent=0;boolean opened=false,shown=false;
        do {
            List<AccessibilityNodeInfo> actions=new ArrayList<>(),triggers=new ArrayList<>();
            for(AccessibilityNodeInfo node:nodes())if(APP.equals(string(node.getPackageName()))&&node.isClickable()
                &&!node.isEditable()&&node.isEnabled()) {
                if(label(node,name,false)&&within(node,"Canvas operations"))actions.add(node);
                if(node.isVisibleToUser()&&"android.widget.Button".equals(string(node.getClassName()))&&label(node,"Canvas tools",false))triggers.add(node);
            }
            require(actions.size()<=1&&triggers.size()<=1,"Ambiguous original graph action; no input sent");
            int visible=actions.size()==1&&actions.get(0).isVisibleToUser()?1:0,expanded=-1;
            if(triggers.size()==1&&Build.VERSION.SDK_INT>=36) {
                int state=triggers.get(0).getExpandedState();
                if(state==AccessibilityNodeInfo.EXPANDED_STATE_FULL||state==AccessibilityNodeInfo.EXPANDED_STATE_PARTIAL)expanded=1;
                else if(state==AccessibilityNodeInfo.EXPANDED_STATE_COLLAPSED)expanded=0;
            }
            absent=visible==0&&triggers.size()==1&&expanded!=1&&(actions.isEmpty()||expanded==0)?Math.min(absent+1,32):0;
            int decision=OriginalCanvasGraph.actionDecision(visible,triggers.size(),expanded,absent);
            progress.append("ORIGINAL_GRAPH_CONTROL actions=").append(visible).append(" triggers=").append(triggers.size())
                .append(" expanded=").append(expanded).append(" stable_absent=").append(absent).append('\n');
            if(decision==1) { activate(actions.get(0),value->within(value,"Canvas operations"));return; }
            if(!shown&&actions.size()==1&&!actions.get(0).isVisibleToUser()&&expanded!=0) {
                shown=true;require(actions.get(0).performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId()),"Original graph action could not be shown");
            } else if(decision==2&&!opened) {
                opened=true;activate(triggers.get(0));absent=0;
            }
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original graph action not visible; no action retried");
    }

    private AccessibilityNodeInfo graphField(String name) {
        long until=SystemClock.uptimeMillis()+25_000;boolean shown=false;
        do {
            List<AccessibilityNodeInfo> found=new ArrayList<>();
            for(AccessibilityNodeInfo node:nodes())if(APP.equals(string(node.getPackageName()))
                &&node.isEditable()&&node.isEnabled()&&within(node,"Canvas nodes and connections")
                &&OriginalCanvasGraph.field(name,string(node.getText()),label(node,name,false)))found.add(node);
            require(found.size()<=1,"Ambiguous graph field; no input sent");
            if(found.size()==1) {
                AccessibilityNodeInfo node=found.get(0);
                if(node.isVisibleToUser())return node;
                if(!shown) { shown=true;require(node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId()),
                    "Original graph field could not be shown"); }
            }
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original graph field not visible");
    }

    private void graphValue(String name,String value) {
        setText(graphField(name),value);
        waitNode(node->node.isEditable()&&within(node,"Canvas nodes and connections")
            &&OriginalCanvasGraph.field(name,string(node.getText()),label(node,name,false))
            &&(string(node.getText()).equals(value)||string(node.getText()).equals(value+", "+name)),"original graph value after one input");
    }

    private void graphClosePanel() {
        if(visibleLabel("Collapse right sidebar")) {
            AccessibilityNodeInfo close=waitNode(node->APP.equals(string(node.getPackageName()))&&node.isClickable()
                &&label(node,"Collapse right sidebar",false)&&within(node,"Canvas nodes and connections"),"original graph inspector close");
            activate(close,value->within(value,"Canvas nodes and connections"));
            waitNode(node->label(node,"Free canvas",false),"owned graph after closing inspector");
        }
    }

    private void graphNode(String label) {
        graphClosePanel();
        Predicate<AccessibilityNodeInfo> target=value->APP.equals(string(value.getPackageName()))&&!value.isEditable()
            &&label(value,label,false)&&within(value,"Free canvas");
        waitNode(target,"original visible graph node: "+label);settleUi();
        int[][] previous=null,observed=null;int stable=0;
        for(int frame=0;frame<16&&stable<3;frame++) {
            observed=graphHitObservation(target);
            stable=java.util.Arrays.deepEquals(previous,observed)?stable+1:0;
            previous=observed;SystemClock.sleep(32);
        }
        require(stable>=3,"Original graph node/control rectangles did not settle; no touch sent");
        // Refresh once more immediately before input, including descendants.
        int[][] fresh=graphHitObservation(target);
        require(java.util.Arrays.deepEquals(observed,fresh),"Original graph hit observation changed; no touch sent");
        int[][] controls=java.util.Arrays.copyOfRange(fresh,2,fresh.length);
        int[] point=OriginalCanvasGraph.interiorPoint(fresh[0],fresh[1],controls);
        progress.append("ORIGINAL_GRAPH_NODE_TOUCH rectangles=").append(java.util.Arrays.deepToString(fresh))
            .append(" point=").append(java.util.Arrays.toString(point)).append(" single=1\n");
        long start=SystemClock.uptimeMillis();touch(start,start,MotionEvent.ACTION_DOWN,point[0],point[1]);
        SystemClock.sleep(60);touch(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,point[0],point[1]);
    }

    private int[][] graphHitObservation(Predicate<AccessibilityNodeInfo> target) {
        AccessibilityNodeInfo node=uniqueTarget(target),canvas=node;
        for(int depth=0;depth<64&&!label(canvas,"Free canvas",false);depth++) {
            canvas=canvas.getParent();require(canvas!=null,"Original graph viewport is missing");
        }
        require(APP.equals(string(canvas.getPackageName()))&&canvas.isVisibleToUser()&&label(canvas,"Free canvas",false),
            "Original graph viewport is not owned and visible");
        Rect bounds=new Rect(),clip=new Rect();node.getBoundsInScreen(bounds);canvas.getBoundsInScreen(clip);
        require(!bounds.isEmpty()&&!clip.isEmpty(),"Original graph has empty bounds");
        AccessibilityNodeInfo active=ui.getRootInActiveWindow();Rect screen=new Rect();
        require(active!=null&&APP.equals(string(active.getPackageName())),"Original active window is not owned");
        active.getBoundsInScreen(screen);
        require(!screen.isEmpty()&&clip.intersect(screen),"Original graph is outside the active owned window");
        List<int[]> rectangles=new ArrayList<>();
        rectangles.add(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom});
        rectangles.add(new int[]{Math.max(0,clip.left),Math.max(0,clip.top),clip.right,clip.bottom});
        List<AccessibilityNodeInfo> descendants=new ArrayList<>();descendants.add(node);
        for(int index=0;index<descendants.size();index++) {
            AccessibilityNodeInfo value=descendants.get(index);
            require(APP.equals(string(value.getPackageName())),"Foreign graph descendant; no input sent");
            if(index>0&&value.isVisibleToUser()&&(value.isClickable()||value.isLongClickable()||value.isEditable()
                ||value.isCheckable()||"android.widget.Button".equals(string(value.getClassName())))) {
                Rect control=new Rect();value.getBoundsInScreen(control);
                require(!control.isEmpty()&&rectangles.size()<34,"Unbounded or empty original graph control");
                rectangles.add(new int[]{control.left,control.top,control.right,control.bottom});
            }
            for(int child=0;child<value.getChildCount();child++) {
                AccessibilityNodeInfo next=value.getChild(child);
                if(next!=null) { require(descendants.size()<128,"Original graph descendant bound exceeded");descendants.add(next); }
            }
        }
        // Floating tools can overlap the node without being its descendants.
        for(AccessibilityNodeInfo value:nodes())if(APP.equals(string(value.getPackageName()))&&value.isVisibleToUser()
            &&value.isClickable()&&within(value,"Canvas operations")) {
            Rect control=new Rect();value.getBoundsInScreen(control);
            require(!control.isEmpty()&&rectangles.size()<34,"Unbounded or empty original canvas tool");
            rectangles.add(new int[]{control.left,control.top,control.right,control.bottom});
        }
        return rectangles.toArray(new int[rectangles.size()][]);
    }

    private void graphGeometry(int x,int y) {
        graphValue("x",Integer.toString(x));graphValue("y",Integer.toString(y));
        graphValue("width","120");graphValue("height","100");
    }

    private void graphReference(String action,String dialog,String value) {
        graphAction(action);
        AccessibilityNodeInfo field=waitNode(node->APP.equals(string(node.getPackageName()))&&node.isEditable()
            &&within(node,dialog),"original graph reference input");
        setText(field,value);hideKeyboard();
        AccessibilityNodeInfo add=waitNode(node->APP.equals(string(node.getPackageName()))&&node.isClickable()
            &&node.isEnabled()&&label(node,"Add to canvas",false)&&within(node,dialog),"owned graph reference confirmation");
        activate(add,node->within(node,dialog));
        waitNode(node->label(node,"Node properties",true),"new graph reference properties");
    }

    private void graphMenu(String action) {
        graphAction("More canvas actions");graphAction(action);
    }

    private void graphInspectorAction(String name) {
        long until=SystemClock.uptimeMillis()+25_000;boolean shown=false;
        do {
            List<AccessibilityNodeInfo> found=new ArrayList<>();
            for(AccessibilityNodeInfo node:nodes())if(APP.equals(string(node.getPackageName()))&&node.isClickable()
                &&node.isEnabled()&&label(node,name,false)&&within(node,"Canvas nodes and connections"))found.add(node);
            require(found.size()<=1,"Ambiguous original inspector action; no input sent");
            if(found.size()==1) {
                AccessibilityNodeInfo node=found.get(0);
                if(node.isVisibleToUser()) { activate(node,value->within(value,"Canvas nodes and connections"));return; }
                if(!shown) { shown=true;require(node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId()),"Original inspector action could not be shown"); }
            }
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original inspector action not visible");
    }

    private void originalCanvasGraph(String phase) throws Exception {
        require(OriginalCanvasGraph.phase(phase),"Unknown graph phase");
        String step=phase.substring("graph-".length());boolean save=true;
        switch(step) {
            case "seed":create("New canvas",OriginalCanvasGraph.PATH);break;
            case "open":case "cold":
                open(OriginalCanvasGraph.PATH);waitLabel("Free canvas");save=false;break;
            case "add-text":
                graphAction("Text");
                AccessibilityNodeInfo field=graphField("Node content");activate(field);CompositionIme.awaitTarget(APP);
                long now=SystemClock.uptimeMillis();
                for(int action:new int[]{KeyEvent.ACTION_DOWN,KeyEvent.ACTION_UP})require(ui.injectInputEvent(
                    new KeyEvent(now,SystemClock.uptimeMillis(),action,KeyEvent.KEYCODE_A,0,KeyEvent.META_CTRL_ON),true),"Graph select-all rejected");
                CompositionIme.compose(APP,OriginalCanvasGraph.TEXT+"草稿");
                waitNode(node->APP.equals(string(node.getPackageName()))&&label(node,"Save",false)&&!node.isEnabled(),"Save blocked during actual Canvas composition");
                CompositionIme.compose(APP,OriginalCanvasGraph.TEXT);CompositionIme.confirm(APP);
                waitNode(node->node.isEditable()&&label(node,"Node content",false)
                    &&string(node.getText()).equals(OriginalCanvasGraph.TEXT),"actual confirmed Canvas Chinese composition");
                progress.append("ORIGINAL_GRAPH_IME compose=2 confirm=1 save_during_composition=0\n");
                graphGeometry(0,120);graphClosePanel();break;
            case "add-file":graphReference("Note / image","File or image",OriginalCanvasGraph.FILE);graphGeometry(150,120);graphClosePanel();break;
            case "add-link":graphReference("URL","Web link",OriginalCanvasGraph.URL);graphGeometry(0,240);graphClosePanel();break;
            case "add-group":graphAction("Group");graphValue("Node content",OriginalCanvasGraph.GROUP);graphGeometry(350,0);graphClosePanel();break;
            case "connect":
                graphAction("Connect nodes");graphNode("link: example.invalid");graphNode("file: "+OriginalCanvasGraph.FILE);break;
            case "edge-label":
                graphNode("link: example.invalid");graphAction("Properties");
                graphInspectorAction("example.invalid → 原笔记😀.md");
                graphValue("Connection label",OriginalCanvasGraph.EDGE);graphClosePanel();break;
            case "copy":
                graphNode("text: "+OriginalCanvasGraph.SEED_A);graphAction("Select several");graphNode("text: "+OriginalCanvasGraph.SEED_B);
                graphMenu("Copy");save=false;break;
            case "duplicate":graphMenu("Duplicate");break;
            case "undo-duplicate":case "undo":case "undo-delete":graphAction("Undo");break;
            case "paste":graphMenu("Paste");break;
            case "redo":case "redo-delete":graphAction("Redo");break;
            case "delete":graphNode("link: example.invalid");graphMenu("Delete selected");break;
            case "fit":graphAction("Show all");save=false;break;
            default:throw new IllegalStateException("Unexpected graph action");
        }
        if(save)save();
        progress.append("ORIGINAL_CANVAS_GRAPH_STEP ").append(step).append(" save=").append(save?1:0).append('\n');
    }

    private Predicate<AccessibilityNodeInfo> motionTarget() {
        return node->APP.equals(string(node.getPackageName()))&&!node.isEditable()
            &&label(node,"text: "+OriginalCanvasMotion.NODE,false)&&within(node,"Free canvas");
    }
    private int[][] motionObservation() {
        int[][] previous=null,current=null;int stable=0;
        for(int frame=0;frame<24&&stable<3;frame++) {
            current=graphHitObservation(motionTarget());
            stable=java.util.Arrays.deepEquals(previous,current)?stable+1:0;previous=current;SystemClock.sleep(32);
        }
        require(stable>=3,"Original motion geometry did not settle; no gesture input sent");
        return current;
    }
    private int[][] motionObstacles(int[] clip) {
        List<int[]> boxes=new ArrayList<>();
        for(AccessibilityNodeInfo node:nodes()) {
            if(!APP.equals(string(node.getPackageName()))||!node.isVisibleToUser()||!node.isEnabled())continue;
            boolean world=label(node,"text: "+OriginalCanvasMotion.NODE,false)||label(node,"link: example.invalid",false)
                ||label(node,"Connection: "+OriginalCanvasMotion.EDGE,false);
            boolean control="android.widget.Button".equals(string(node.getClassName()))||node.isEditable()||node.isCheckable()
                ||node.isLongClickable()||node.isClickable()&&"android.view.View".equals(string(node.getClassName()))
                &&!label(node,"Free canvas",false);
            if(!world&&!control)continue;
            Rect bounds=new Rect();node.getBoundsInScreen(bounds);
            if(bounds.isEmpty()||bounds.right<=clip[0]||bounds.left>=clip[2]||bounds.bottom<=clip[1]||bounds.top>=clip[3])continue;
            require(boxes.size()<32,"Original motion obstacle bound exceeded; no gesture input sent");
            boxes.add(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom});
        }
        require(boxes.size()>=2,"Original motion world obstacles are missing");
        return boxes.toArray(new int[boxes.size()][]);
    }
    private JSONArray motionRows(int[][] values)throws Exception {
        JSONArray result=new JSONArray();for(int[] value:values)result.put(new JSONArray(value));return result;
    }
    private void motionRecord(String phase,String kind,JSONObject value)throws Exception {
        require((OriginalCanvasMotion.phase(phase)||viewPhase(phase))&&(kind.equals("intent")||kind.equals("result")),"Original motion record scope refused");
        File file=new File(getContext().getFilesDir(),phase+"-touch-"+kind+".json");
        require(file.createNewFile(),"Original motion record already exists; no action replayed");
        byte[] raw=(value.toString()+"\n").getBytes("UTF-8");require(raw.length<=16384,"Original motion record bound exceeded");
        try(FileOutputStream output=new FileOutputStream(file)){output.write(raw);output.getFD().sync();}
        progress.append("ORIGINAL_CANVAS_MOTION_RECORD ").append(phase).append(' ').append(kind).append('\n');
    }
    private boolean viewPhase(String phase) {
        for(String step:new String[]{"seed","open","pan","pinch","cancel","takeover","background","cold","inspector"})
            if(("view-"+step).equals(phase))return true;
        return false;
    }
    private String viewDigest(byte[] bytes)throws Exception {
        StringBuilder result=new StringBuilder();
        for(byte value:java.security.MessageDigest.getInstance("SHA-256").digest(bytes))result.append(String.format(java.util.Locale.ROOT,"%02x",value&255));
        return result.toString();
    }
    private Rect viewWebViewFrame() {
        AccessibilityNodeInfo node=uniqueTarget(motionTarget()),webview=node;
        for(int depth=0;depth<64&&!"android.webkit.WebView".equals(string(webview.getClassName()));depth++) {
            webview=webview.getParent();require(webview!=null,"Owned original Canvas WebView ancestor missing");
        }
        require(APP.equals(string(webview.getPackageName()))&&webview.isVisibleToUser()
            &&"android.webkit.WebView".equals(string(webview.getClassName())),"Original view WebView is not owned and visible");
        AccessibilityNodeInfo active=ui.getRootInActiveWindow();
        require(active!=null&&APP.equals(string(active.getPackageName())),"Original view window changed before observation");
        Rect frame=new Rect();webview.getBoundsInScreen(frame);require(!frame.isEmpty(),"Original WebView frame is empty");return frame;
    }
    private void viewCheckpoint(String phase,String stage,String nonce,int[][] expected)throws Exception {
        require(viewPhase(phase)&&(stage.equals("before")||stage.equals("after"))&&nonce.equals(java.util.UUID.fromString(nonce).toString()),"Original view checkpoint scope refused");
        boolean before=stage.equals("before");int[][] sampled=motionObservation();
        require(OriginalCanvasMotion.viewObservationStable(expected,sampled,before),"Original view input target or owned viewport changed before observation");
        expected=sampled;Rect frame=viewWebViewFrame();
        JSONObject value=new JSONObject().put("schema",1).put("scope","canvas-view").put("dispatch_id",nonce)
            .put("phase",phase).put("stage",stage).put("owner",APP).put("webview",new JSONArray(new int[]{frame.left,frame.top,frame.right,frame.bottom}))
            .put("clip",new JSONArray(expected[1])).put("node",new JSONArray(expected[0])).put("recorded_ms",SystemClock.uptimeMillis());
        byte[] raw=(value.toString()+"\n").getBytes("UTF-8");require(raw.length<=2048,"Original view metadata exceeds bound");
        File file=new File(getContext().getFilesDir(),phase+"-"+stage+"-native-view.json");
        require(file.createNewFile(),"Original view observation exists; no replay admitted");
        try(FileOutputStream output=new FileOutputStream(file)){output.write(raw);output.getFD().sync();}
        Bundle signal=new Bundle();signal.putString("owned_canvas_view_ready",nonce+":"+phase+":"+stage);sendStatus(0,signal);
        File acknowledgement=new File(getContext().getFilesDir(),phase+"-"+stage+"-view-ack.json");long until=SystemClock.uptimeMillis()+30000;
        while(!acknowledgement.exists()&&SystemClock.uptimeMillis()<until)SystemClock.sleep(25);
        require(acknowledgement.isFile()&&acknowledgement.length()>0&&acknowledgement.length()<=2048,"Original view observation was not acknowledged; no input retry");
        byte[] body=new byte[(int)acknowledgement.length()];int offset=0;
        try(java.io.FileInputStream input=new java.io.FileInputStream(acknowledgement)){
            while(offset<body.length){int count=input.read(body,offset,body.length-offset);require(count>0,"Original view acknowledgement incomplete");offset+=count;}
            require(input.read()==-1,"Original view acknowledgement changed");
        }
        JSONObject ack=new JSONObject(new String(body,"UTF-8"));
        require(ack.length()==6&&ack.getBoolean("passed")&&nonce.equals(ack.getString("dispatch_id"))&&phase.equals(ack.getString("phase"))
            &&stage.equals(ack.getString("stage"))&&viewDigest(raw).equals(ack.getString("native_sha256"))
            &&ack.getString("renderer_sha256").matches("[0-9a-f]{64}"),"Original view acknowledgement does not match this observation");
        int[][] fresh=motionObservation();
        require(frame.equals(viewWebViewFrame())&&OriginalCanvasMotion.viewObservationStable(expected,fresh,before),
            "Original view input target or owned frame changed during observation; no gesture retry");
        JSONObject checked=new JSONObject().put("schema",1).put("scope","canvas-view-frame-check").put("dispatch_id",nonce)
            .put("phase",phase).put("stage",stage).put("owner",APP).put("native_sha256",viewDigest(raw))
            .put("webview",new JSONArray(new int[]{frame.left,frame.top,frame.right,frame.bottom}))
            .put("before_read",motionRows(expected)).put("after_read",motionRows(fresh)).put("input_target_exact_guard",before);
        byte[] checkedRaw=(checked.toString()+"\n").getBytes("UTF-8");require(checkedRaw.length<=8192,"Original view frame record bound exceeded");
        File checkedFile=new File(getContext().getFilesDir(),phase+"-"+stage+"-native-frame-check.json");
        require(checkedFile.createNewFile(),"Original view frame record exists; no input replay");
        try(FileOutputStream output=new FileOutputStream(checkedFile)){output.write(checkedRaw);output.getFD().sync();}
    }
    private void motionInput(long start,int action,float[][] points,int[] clip,JSONArray events)throws Exception {
        require(points.length>=1&&points.length<=2,"Original motion pointer bound refused");
        MotionEvent.PointerProperties[] ids=new MotionEvent.PointerProperties[points.length];
        MotionEvent.PointerCoords[] coordinates=new MotionEvent.PointerCoords[points.length];
        for(int i=0;i<points.length;i++) {
            require(points[i].length==2&&OriginalCanvasMotion.contains(clip,points[i][0],points[i][1],0),"Original motion pointer outside owned viewport");
            ids[i]=new MotionEvent.PointerProperties();ids[i].id=i;ids[i].toolType=MotionEvent.TOOL_TYPE_FINGER;
            coordinates[i]=new MotionEvent.PointerCoords();coordinates[i].x=points[i][0];coordinates[i].y=points[i][1];
            coordinates[i].pressure=(action&MotionEvent.ACTION_MASK)==MotionEvent.ACTION_UP||(action&MotionEvent.ACTION_MASK)==MotionEvent.ACTION_CANCEL
                ||(action&MotionEvent.ACTION_MASK)==MotionEvent.ACTION_POINTER_UP&&i==1?0:1;coordinates[i].size=1;
        }
        long now=SystemClock.uptimeMillis();MotionEvent event=MotionEvent.obtain(start,now,action,points.length,ids,coordinates,0,0,1,1,0,0,InputDevice.SOURCE_TOUCHSCREEN,0);
        try {
            require(ui.injectInputEvent(event,true),"Original finger event rejected; no input retry");
            JSONArray pointerIds=new JSONArray(),tools=new JSONArray(),observed=new JSONArray();
            for(int i=0;i<event.getPointerCount();i++) {pointerIds.put(event.getPointerId(i));tools.put(event.getToolType(i));observed.put(new JSONArray(new float[]{event.getX(i),event.getY(i)}));}
            events.put(new JSONObject().put("action",event.getActionMasked()).put("index",event.getActionIndex())
                .put("source",event.getSource()).put("ids",pointerIds).put("tools",tools).put("points",observed)
                .put("down_ms",event.getDownTime()).put("time_ms",event.getEventTime()));
        } finally {event.recycle();}
    }
    private void motionMoves(long start,float[] plan,int[] clip,JSONArray events)throws Exception {
        for(int step=1;step<=8;step++) {
            SystemClock.sleep(35);float x=plan[0]+(plan[2]-plan[0])*step/8f,y=plan[1]+(plan[3]-plan[1])*step/8f;
            motionInput(start,MotionEvent.ACTION_MOVE,new float[][]{{x,y}},clip,events);
        }
    }
    private boolean motionHomeAndResume(ComponentName original)throws Exception {
        AccessibilityNodeInfo active=ui.getRootInActiveWindow();require(active!=null&&APP.equals(string(active.getPackageName())),"Original app not foreground before Home");
        long time=SystemClock.uptimeMillis();
        for(int action:new int[]{KeyEvent.ACTION_DOWN,KeyEvent.ACTION_UP})require(ui.injectInputEvent(new KeyEvent(time,SystemClock.uptimeMillis(),action,KeyEvent.KEYCODE_HOME,0),true),"Original Home key rejected");
        long until=SystemClock.uptimeMillis()+10000;boolean background=false;
        do {
            active=ui.getRootInActiveWindow();String owner=active==null?"":string(active.getPackageName());
            if(owner.equals("com.android.launcher3")||owner.equals("com.google.android.apps.nexuslauncher")){background=true;break;}
            SystemClock.sleep(100);
        }while(SystemClock.uptimeMillis()<until);
        require(background,"Actual original Home surface was not observed; no gesture completion sent");
        // The original pointer stream is left to Android's actual window-loss cancellation.
        // No artificial ACTION_CANCEL/UP or touch is sent to the home application.
        SystemClock.sleep(500);getContext().startActivity(new Intent().setComponent(original).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP));
        waitNode(node->APP.equals(string(node.getPackageName()))&&label(node,"text: "+OriginalCanvasMotion.NODE,false)&&within(node,"Free canvas"),"same owned graph after actual Home and resume");
        return background;
    }
    private void originalCanvasMotion(String phase)throws Exception {
        boolean view=viewPhase(phase);require(view||OriginalCanvasMotion.phase(phase),"Original motion phase refused");String step=phase.substring(view?5:7);
        String path=view?"view-original.canvas":OriginalCanvasMotion.PATH;
        if(step.equals("seed")){create("New canvas",path);save();return;}
        if(step.equals("open")||step.equals("cold")){
            if(phase.equals("view-cold")&&arguments.getString("view_cold_window_guard","").equals("yes"))awaitColdInputWindow();
            open(path);waitLabel("Free canvas");waitNode(motionTarget(),"original declared motion node");return;
        }
        graphClosePanel();graphAction("Show all");graphNode("text: "+OriginalCanvasMotion.NODE);settleUi();
        if(step.equals("inspector")){graphAction("Properties");waitNode(node->within(node,"Canvas nodes and connections")&&label(node,"Node properties",true),"original node inspector, read only");return;}
        int[][] observation=motionObservation();int[] bounds=observation[0],clip=observation[1];int[][] controls=java.util.Arrays.copyOfRange(observation,2,observation.length);
        int[][] obstacles=motionObstacles(clip);int[] resize=null;float[] plan;
        if(step.equals("resize")) {
            AccessibilityNodeInfo control=uniqueTarget(node->APP.equals(string(node.getPackageName()))&&node.isClickable()
                &&label(node,"Resize node",false)&&within(node,"text: "+OriginalCanvasMotion.NODE)&&within(node,"Free canvas"));
            Rect box=new Rect();control.getBoundsInScreen(box);resize=new int[]{box.left,box.top,box.right,box.bottom};
        }
        if(step.equals("pan"))plan=OriginalCanvasMotion.pan(clip,obstacles);
        else if(step.equals("pinch"))plan=OriginalCanvasMotion.emptyPair(clip,obstacles);
        else plan=OriginalCanvasMotion.drag(bounds,clip,controls,resize);
        JSONObject intent=new JSONObject().put("schema",1).put("scope",view?"canvas-view":"canvas-motion").put("phase",phase).put("owner",APP)
            .put("node_bounds",new JSONArray(bounds)).put("clip",new JSONArray(clip)).put("controls",motionRows(controls))
            .put("obstacles",motionRows(obstacles)).put("plan",new JSONArray(plan)).put("recorded_ms",SystemClock.uptimeMillis());
        if(resize!=null)intent.put("resize_control",new JSONArray(resize));
        float[] secondary=null;ComponentName resume=null;
        if(step.equals("takeover")) {
            int[][] swept=java.util.Arrays.copyOf(obstacles,obstacles.length+1);swept[obstacles.length]=new int[]{bounds[0],bounds[1],(int)Math.ceil(bounds[2]+plan[2]-plan[0]),(int)Math.ceil(bounds[3]+plan[3]-plan[1])};
            float[] empty=OriginalCanvasMotion.emptyPair(clip,swept);float x=empty[2],y=empty[3];
            require(Math.hypot(x-plan[2],y-plan[3])>=80,"Original second finger separation refused");
            secondary=new float[]{x,y,plan[2]+(x-plan[2])*1.1f,plan[3]+(y-plan[3])*1.1f};
            require(OriginalCanvasMotion.contains(clip,secondary[2],secondary[3],12),"Original second finger end outside viewport");
            intent.put("secondary",new JSONArray(secondary));
        }
        if(step.equals("background")) {
            String activity=arguments.getString("motion_activity","");resume=ComponentName.unflattenFromString(activity);
            require(activity.length()<=300&&resume!=null&&APP.equals(resume.getPackageName()),"Original resume activity is not the owned installed app");intent.put("resume_activity",activity);
        }
        String viewNonce=view?arguments.getString("view_dispatch_id",""):"";
        if(view){intent.put("dispatch_id",viewNonce);viewCheckpoint(phase,"before",viewNonce,observation);}
        motionRecord(phase,"intent",intent);JSONArray events=new JSONArray();long start=SystemClock.uptimeMillis();
        motionInput(start,MotionEvent.ACTION_DOWN,new float[][]{{plan[0],plan[1]}},clip,events);
        boolean background=false,resumed=false;
        if(step.equals("pinch")||step.equals("takeover")) {
            float x1=plan[0],y1=plan[1],x2=plan[2],y2=plan[3],end1x=x1,end1y=y1,end2x=x2,end2y=y2;
            if(step.equals("takeover")){motionMoves(start,plan,clip,events);x1=plan[2];y1=plan[3];x2=secondary[0];y2=secondary[1];end1x=x1;end1y=y1;end2x=secondary[2];end2y=secondary[3];}
            else {float center=(x1+x2)/2;end1x=center+(x1-center)*1.1f;end2x=center+(x2-center)*1.1f;}
            motionInput(start,MotionEvent.ACTION_POINTER_DOWN|(1<<MotionEvent.ACTION_POINTER_INDEX_SHIFT),new float[][]{{x1,y1},{x2,y2}},clip,events);
            for(int frame=1;frame<=8;frame++){SystemClock.sleep(35);motionInput(start,MotionEvent.ACTION_MOVE,new float[][]{{x1+(end1x-x1)*frame/8f,y1+(end1y-y1)*frame/8f},{x2+(end2x-x2)*frame/8f,y2+(end2y-y2)*frame/8f}},clip,events);}
            motionInput(start,MotionEvent.ACTION_POINTER_UP|(1<<MotionEvent.ACTION_POINTER_INDEX_SHIFT),new float[][]{{end1x,end1y},{end2x,end2y}},clip,events);
            float lastX=end1x+8,lastY=end1y+8;require(OriginalCanvasMotion.contains(clip,lastX,lastY,4),"Original remaining finger end refused");
            motionInput(start,MotionEvent.ACTION_MOVE,new float[][]{{lastX,lastY}},clip,events);motionInput(start,MotionEvent.ACTION_UP,new float[][]{{lastX,lastY}},clip,events);
        } else {
            motionMoves(start,plan,clip,events);
            if(step.equals("background")){background=motionHomeAndResume(resume);resumed=true;}
            else motionInput(start,step.equals("cancel")?MotionEvent.ACTION_CANCEL:MotionEvent.ACTION_UP,new float[][]{{plan[2],plan[3]}},clip,events);
        }
        settleUi();int[][] afterObservation=motionObservation();int[] after=afterObservation[0];AccessibilityNodeInfo active=ui.getRootInActiveWindow();
        JSONObject result=new JSONObject().put("schema",1).put("phase",phase).put("owner",APP)
            .put("before_node_bounds",new JSONArray(bounds)).put("after_node_bounds",new JSONArray(after)).put("events",events)
            .put("background_seen",background).put("resumed_owned",resumed).put("owned_root_after",active!=null&&APP.equals(string(active.getPackageName())));
        if(view)result.put("dispatch_id",viewNonce);
        motionRecord(phase,"result",result);
        if(step.equals("move")||step.equals("resize"))save();
        else waitNode(node->APP.equals(string(node.getPackageName()))&&label(node,"Save",false)&&!node.isEnabled(),"view-only/interrupted gesture keeps Save disabled");
        if(view)viewCheckpoint(phase,"after",viewNonce,afterObservation);
    }

    private void awaitColdInputWindow()throws Exception {
        require(arguments.getString("phase","").equals("view-cold"),"Original cold window observation scope refused");
        long until=SystemClock.uptimeMillis()+10000;int stable=0,previous=-1;Rect bounds=new Rect();
        while(SystemClock.uptimeMillis()<until&&stable<3) {
            AccessibilityNodeInfo active=ui.getRootInActiveWindow();AccessibilityWindowInfo found=null;
            if(active!=null&&APP.equals(string(active.getPackageName())))for(AccessibilityWindowInfo window:ui.getWindows()) {
                AccessibilityNodeInfo root=window.getRoot();
                if(window.isActive()&&window.isFocused()&&root!=null&&APP.equals(string(root.getPackageName()))) {
                    require(found==null,"Original cold focused window is ambiguous; no input sent");found=window;
                }
            }
            if(found==null){stable=0;previous=-1;}else {
                Rect current=new Rect();found.getBoundsInScreen(current);
                stable=found.getId()==previous&&bounds.equals(current)&&!current.isEmpty()?stable+1:1;
                previous=found.getId();bounds=current;
            }
            if(stable<3)SystemClock.sleep(100);
        }
        require(stable==3,"Original cold owned input window did not become focused; no input sent");
        JSONObject value=new JSONObject().put("schema",1).put("scope","canvas-view-cold-window").put("owner",APP)
            .put("active_focused",true).put("stable_samples",stable).put("window_id",previous)
            .put("bounds",new JSONArray(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom}))
            .put("before_any_navigation_input",true).put("recorded_ms",SystemClock.uptimeMillis());
        File file=new File(getContext().getFilesDir(),"view-cold-input-window.json");require(file.createNewFile(),"Original cold window record exists; no input retry");
        try(FileOutputStream output=new FileOutputStream(file)){output.write((value.toString()+"\n").getBytes("UTF-8"));output.getFD().sync();}
    }

    private void pairProperty(String name, String before, String after) {
        AccessibilityNodeInfo field=waitPairProperty(name,before);
        setText(field,after);
        waitPairProperty(name,after);
    }

    private AccessibilityNodeInfo waitPairProperty(String name, String expected) {
        waitingFor="original named Canvas property";
        progress.append("wait: ").append(waitingFor).append('\n');
        long until=SystemClock.uptimeMillis()+25_000;
        boolean shown=false;
        do {
            List<AccessibilityNodeInfo> matches=new ArrayList<>();
            for(AccessibilityNodeInfo node:nodes()) {
                if(APP.equals(string(node.getPackageName())) && node.isEditable() && node.isEnabled()
                    && within(node,"Canvas nodes and connections")
                    && OriginalCanvasProperty.matches(name,expected,string(node.getText()),label(node,name,false))) matches.add(node);
            }
            require(matches.size()<=1,"Ambiguous original Canvas property; no input sent");
            if(matches.size()==1) {
                AccessibilityNodeInfo field=matches.get(0);
                if(field.isVisibleToUser())return field;
                if(!shown) {
                    // Scroll only the exact existing property, then reacquire
                    // its fresh node/value. Never repeat the value mutation.
                    shown=true;
                    require(field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId()),
                        "Original Canvas property could not be shown; no input sent");
                }
            }
            SystemClock.sleep(200);
        } while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original Canvas property not visible with its original value");
    }

    private void waitOriginalImageNode() {
        waitNode(node -> string(node.getText()).contains("attachments/")
            || string(node.getContentDescription()).contains("attachments/"), "original image node");
    }

    private void clickMenu(String text) {
        if (text.equals("More canvas actions")) showCanvasTools();
        activate(waitNode(node -> label(node, text, false) && node.isEnabled() && node.isClickable(), "clickable menu: " + text));
    }

    private void showCanvasTools() {
        List<AccessibilityNodeInfo> triggers = new ArrayList<>();
        for (AccessibilityNodeInfo node : nodes()) {
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser()
                    && node.isClickable() && label(node, "Canvas tools", false)) triggers.add(node);
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser()
                    && label(node, "Canvas operations", false)) return;
        }
        // An older fixed APK or a wide tablet renders the original inline
        // toolbar. No input is sent unless its exact narrow trigger is seen.
        require(triggers.size() <= 1, "Ambiguous canvas tools trigger; no input sent");
        if (triggers.isEmpty()) return;
        require(triggers.get(0).isEnabled(), "Canvas tools are unavailable during composition");
        activate(triggers.get(0));
        waitNode(node -> APP.equals(string(node.getPackageName())) && node.isVisibleToUser()
            && label(node, "Canvas operations", false), "expanded shared Canvas operations");
    }

    private boolean visibleLabel(String text) {
        for (AccessibilityNodeInfo node : nodes()) {
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser() && label(node, text, false)) return true;
        }
        return false;
    }

    private void canvasToolsState(boolean trigger, boolean toolbar) {
        waitNode(node -> APP.equals(string(node.getPackageName())) && node.isVisibleToUser()
            && label(node, trigger ? "Canvas tools" : "Canvas operations", false)
            && visibleCanvasTrigger() == trigger && visibleLabel("Canvas operations") == toolbar,
            "responsive owned Canvas tools at the original width");
    }

    private boolean visibleCanvasTrigger() {
        // The wide inline area's accessible group name is also Canvas tools.
        // Only the actual clickable phone button establishes trigger mode.
        for (AccessibilityNodeInfo node : nodes()) {
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser() && node.isClickable()
                && "android.widget.Button".equals(string(node.getClassName())) && label(node,"Canvas tools",false)) return true;
        }
        return false;
    }

    private boolean titleBarControl(AccessibilityNodeInfo node) {
        AccessibilityNodeInfo parent = node.getParent();
        return parent != null && APP.equals(string(parent.getPackageName()))
            && descendantLabel(parent, "Main navigation") && descendantLabel(parent, "Settings");
    }

    private void clickTitleBar(String text) {
        AccessibilityNodeInfo node = waitNode(value -> APP.equals(string(value.getPackageName()))
            && label(value, text, false) && value.isEnabled() && value.isClickable()
            && titleBarControl(value), "workspace title bar: " + text);
        activate(node, this::titleBarControl);
    }

    private void clickFormatting(String text) {
        final String scope = "Markdown formatting toolbar";
        // The editor's selection bubble exposes the same formatting labels.
        // Address the expanded shared toolbar by its actual accessible container.
        AccessibilityNodeInfo node = waitNode(value -> APP.equals(string(value.getPackageName()))
            && label(value, text, false) && value.isEnabled() && value.isClickable()
            && within(value, scope), "expanded formatting toolbar: " + text);
        activate(node, scope);
    }

    private boolean within(AccessibilityNodeInfo node, String container) {
        for (int depth = 0; node != null && depth < 32; depth++, node = node.getParent())
            if (APP.equals(string(node.getPackageName())) && label(node, container, false)) return true;
        return false;
    }

    private void chooseOriginalImage() {
        // API36 routes image GET_CONTENT through the real Photo Picker first.
        // Owned screenshots/AX trees identify both the standalone photopicker
        // and providers.media.module variants and their More action. Android
        // CTS also uses overflow -> Browse to reach
        // DocumentsUI: platform/cts commit afa92427, PhotoPickerUiUtils.java.
        AccessibilityNodeInfo picker=waitNode(node -> documentsUi(node) || photoPicker(node), "system image or document picker");
        if (photoPicker(picker)) {
            activate(waitNode(node -> photoPicker(node) && (label(node,"More",false) || label(node,"More options",false)),
                "observed Photo Picker overflow"));
            activate(waitNode(node -> photoPicker(node)
                && (label(node,"Browse",false) || label(node,"Browse…",false)),
                "observed Photo Picker Browse… documents action"));
        }
        // Select the owned original through the actual provider, never inject
        // a URI, index user media, or grant broad media/storage permissions.
        waitNode(this::documentsUi, "system document picker");
        activate(waitNode(node -> documentsUi(node) && label(node, "Show roots", false), "system picker roots"));
        // A reopened picker can still expose the current Downloads breadcrumb
        // while its drawer animates. Select only the named sidebar root.
        activate(waitNode(node -> documentsUi(node) && label(node, "Downloads", false) && withinDocumentRoots(node),
            "owned Downloads sidebar root"));
        waitForDocumentRootsClosed();
        activate(waitNode(this::originalImageRow, "owned original PNG document row"));
        waitNode(node -> APP.equals(string(node.getPackageName())) && label(node, "Back", false), "return from original system picker");
    }

    private boolean photoPicker(AccessibilityNodeInfo node) {
        String name=string(node.getPackageName());
        return "com.google.android.photopicker".equals(name)
            || "com.google.android.providers.media.module".equals(name);
    }
    private boolean withinDocumentRoots(AccessibilityNodeInfo node) {
        for (int depth=0;node!=null && depth<32;depth++,node=node.getParent()) {
            if (!documentsUi(node)) return false;
            if (string(node.getViewIdResourceName()).endsWith(":id/roots_list")) return true;
        }
        return false;
    }
    private void waitForDocumentRootsClosed() {
        waitingFor="system picker sidebar closed";
        progress.append("wait: ").append(waitingFor).append('\n');
        long until=SystemClock.uptimeMillis()+25000;
        do {
            boolean open=false;
            for (AccessibilityNodeInfo node:nodes())
                if (documentsUi(node) && node.isVisibleToUser()
                    && string(node.getViewIdResourceName()).endsWith(":id/roots_list")) open=true;
            if (!open) return;
            SystemClock.sleep(150);
        } while (SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("System picker sidebar stayed open; no file tapped");
    }
    private boolean originalImageRow(AccessibilityNodeInfo node) {
        if (!documentsUi(node)) return false;
        if (label(node,"opennexus-ci-original.png",false)) return true;
        // The observed provider grid exposes the exact filename followed by
        // comma-delimited size/date metadata on its directory row. Its separate
        // "Preview the file ..." action is not a selection target.
        AccessibilityNodeInfo parent=node.getParent();
        return string(node.getContentDescription()).startsWith("opennexus-ci-original.png, ")
            && parent!=null && string(parent.getPackageName()).equals(string(node.getPackageName()))
            && string(parent.getViewIdResourceName()).endsWith(":id/dir_list");
    }

    private boolean fileDrawerRow(AccessibilityNodeInfo node) {
        if (!APP.equals(string(node.getPackageName())) || !node.isClickable()
            || !"android.widget.Button".equals(string(node.getClassName()))) return false;
        // The landing page may expose a recent-note button with the same name
        // while the drawer is loading. Only the actual Files/New note/Close
        // header identifies the shared file tree; never tap a covered card.
        for (int depth = 0; node != null && depth < 8; depth++, node = node.getParent()) {
            for (int child = 0; child < node.getChildCount(); child++) {
                AccessibilityNodeInfo header = node.getChild(child);
                if (header == null || header.getChildCount() > 4) continue;
                boolean files = false, create = false, close = false;
                for (int item = 0; item < header.getChildCount(); item++) {
                    AccessibilityNodeInfo control = header.getChild(item);
                    if (control == null || !APP.equals(string(control.getPackageName()))) continue;
                    files |= label(control, "Files", false) && !control.isClickable();
                    create |= label(control, "New note", false) && control.isClickable();
                    close |= label(control, "Close", false) && control.isClickable();
                }
                if (files && create && close) return true;
            }
        }
        return false;
    }

    private boolean visibleFilePanel() {
        for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser()&&node.isEnabled()
            &&label(node,"Close",false)&&fileDrawerRow(node))return true;
        return false;
    }
    private void waitFilePageReady(String path) {
        // The title bar exists before boot's asynchronous Native list returns.
        // Observe that original data (or an already open editor) before one tap;
        // geometry stability alone is not a completed cold-start observation.
        long until=SystemClock.uptimeMillis()+25_000;
        do {
            AccessibilityNodeInfo active=ui.getRootInActiveWindow();
            if(active!=null&&APP.equals(string(active.getPackageName()))) {
                for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser()&&node.isEnabled()
                    &&node.isClickable()&&APP.equals(string(node.getPackageName()))) {
                    boolean editor=titleBarControl(node)&&label(node,"Back",false);
                    boolean file="android.widget.Button".equals(string(node.getClassName()))&&label(node,path,false);
                    if(editor||file) {
                        progress.append("ORIGINAL_FILE_PAGE_READY target=").append(path)
                            .append(" editor=").append(editor?1:0).append('\n');return;
                    }
                }
            }
            SystemClock.sleep(150);
        }while(SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Original Native file data did not become ready; no file-menu input sent");
    }
    private void open(String path) {
        waitFilePageReady(path);
        if(!visibleFilePanel())clickTitleBar("Files");
        AccessibilityNodeInfo row = waitNode(node -> label(node, path, false)
            && node.isEnabled() && fileDrawerRow(node), "file drawer row: " + path);
        activate(row, this::fileDrawerRow);
        final String name = path.substring(path.lastIndexOf('/') + 1);
        // The actual opened title is the navigation result, independently of
        // whether a separate Back control is present in the current AX tree.
        waitNode(node -> APP.equals(string(node.getPackageName())) && !node.isClickable()
            && label(node, name, false) && titleBarControl(node), "opened document title in the owned workspace: " + name);
    }
    private void save() {
        // Save is in the visible editor toolbar. Injecting Back to hide an
        // input window that is already closing can instead leave the editor,
        // especially after unmounting the Canvas property field. Tap the
        // freshly observed real action; do not navigate as a side effect.
        progress.append("save: actual visible toolbar action, no Back injection\n");
        click("Save", false);
        waitLabel("Saved");
    }

    private void beforeImageSave(String phase) throws java.io.IOException {
        if (!"yes".equals(arguments.getString("image_checkpoint"))) return;
        require(phase.equals("rich-image") || phase.equals("canvas-image"), "Unexpected image checkpoint");
        waitNode(node -> APP.equals(string(node.getPackageName())) && label(node,"Save",false)
            && node.isEnabled() && node.isClickable(), "enabled Save after original image import");
        awaitImageObservation(phase, "save");
    }

    private void awaitImageObservation(String phase, String stage) throws java.io.IOException {
        require((phase.equals("rich-image") || phase.equals("canvas-image"))
            && (stage.equals("save") || phase.equals("rich-image") && stage.equals("editor")), "Unexpected image observation");
        // Keep this same instrumentation alive while the runner reads the
        // original native receipt and actual rendered image. The generic dirty
        // label predates image insertion and cannot establish import readiness.
        File root=getContext().getNoBackupFilesDir();
        File ready=new File(root,"owned-"+phase+"-"+stage+".ready");
        require(ready.createNewFile(),"Original image checkpoint already exists");
        try (FileOutputStream out=new FileOutputStream(ready)) {
            out.write("ready\n".getBytes(java.nio.charset.StandardCharsets.UTF_8));out.getFD().sync();
        }
        File proceed=new File(root,"owned-"+phase+"-"+stage+".proceed");
        waitingFor="read-only original image readiness: "+phase+" "+stage;
        long until=SystemClock.uptimeMillis()+45000;
        while (!proceed.isFile() && SystemClock.uptimeMillis()<until) SystemClock.sleep(100);
        byte[] originalSignal=proceed.isFile() && proceed.length()==8 ? java.nio.file.Files.readAllBytes(proceed.toPath()) : new byte[0];
        progress.append("original image marker: ").append(stage).append(" length=")
            .append(proceed.isFile()?proceed.length():-1).append(" bytes=")
            .append(java.util.Arrays.toString(originalSignal)).append('\n');
        require(proceed.isFile() && java.util.Arrays.equals(originalSignal,
            "proceed\n".getBytes(java.nio.charset.StandardCharsets.UTF_8)),"Original image/save observation missing; no Save sent");
        progress.append("original image readiness observed: ").append(stage).append('\n');
    }
    private void waitNotesLanding() {
        waitNode(node -> APP.equals(string(node.getPackageName())) && label(node, "Start writing", false),
            "owned notes landing heading (not the drafts tab New note action)");
        waitNode(node -> APP.equals(string(node.getPackageName())) && label(node, "Notes in this vault", false),
            "original saved notes on the owned landing");
    }
    private void waitLabel(String text) { waitNode(node -> label(node, text, false), "label: " + text); }
    private void click(String text, boolean prefix) {
        if (!prefix && (text.equals("Text") || text.equals("Properties"))) showCanvasTools();
        AccessibilityNodeInfo node = waitNode(value -> label(value, text, prefix) && value.isEnabled(), "enabled action: " + text);
        progress.append("tap: ").append(text).append('\n');
        activate(node);
    }
    private static String string(CharSequence value) { return value == null ? "" : value.toString(); }
    private static boolean label(AccessibilityNodeInfo node, String text, boolean prefix) {
        String actual = string(node.getText()), description = string(node.getContentDescription());
        // Android WebView exposes an editable ARIA name as hint text.
        String hint = Build.VERSION.SDK_INT >= 26 ? string(node.getHintText()) : "";
        return actual.equals(text) || description.equals(text) || hint.equals(text)
            || prefix && (actual.startsWith(text + " ") || description.startsWith(text + " "));
    }

    private AccessibilityNodeInfo noteBody() {
        return waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
            && label(node, "Markdown note body", false), "visible owned Markdown body (not file search)");
    }

    private boolean editorContainsText(AccessibilityNodeInfo root, String text) {
        if (!APP.equals(string(root.getPackageName())) || !root.isEditable()
            || !label(root, "Markdown note body", false) || !root.isVisibleToUser()) return false;
        List<AccessibilityNodeInfo> nodes = new ArrayList<>();
        nodes.add(root);
        for (int index = 0; index < nodes.size() && index < 200; index++) {
            AccessibilityNodeInfo node = nodes.get(index);
            if (APP.equals(string(node.getPackageName())) && node.isVisibleToUser()
                && string(node.getText()).contains(text)) return true;
            for (int child = 0; child < node.getChildCount(); child++) {
                AccessibilityNodeInfo value = node.getChild(child);
                if (value != null) nodes.add(value);
            }
        }
        return false;
    }

    private AccessibilityNodeInfo editable(String original) {
        return waitNode(node -> APP.equals(string(node.getPackageName())) && node.isEditable()
            && string(node.getText()).equals(original), "original Canvas property text");
    }

    private void setText(AccessibilityNodeInfo node, String value) {
        progress.append("set text: ").append(value.length()).append(" UTF-16 units\n");
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        Bundle text = new Bundle();
        text.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text), "Accessibility text input rejected");
        SystemClock.sleep(250);
    }

    private void inputText(AccessibilityNodeInfo node, String value) throws Exception {
        require(APP.equals(string(node.getPackageName())) && node.isEditable(), "Refusing text outside the owned editor");
        progress.append("IME: commit ").append(value.length()).append(" original UTF-16 units\n");
        activateNoteBody(node);
        CompositionIme.awaitTarget(APP);
        long now = SystemClock.uptimeMillis();
        // Select the actual editor document through its normal keyboard handler.
        // Accessibility ACTION_SET_TEXT on WebView contenteditable flattens breaks.
        for (int action : new int[] { KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP }) {
            require(ui.injectInputEvent(new KeyEvent(now, SystemClock.uptimeMillis(), action,
                KeyEvent.KEYCODE_A, 0, KeyEvent.META_CTRL_ON), true), "Original editor select-all rejected");
        }
        CompositionIme.commit(APP, value);
        SystemClock.sleep(250);
    }

    private void activate(AccessibilityNodeInfo node) {
        activate(node, (Predicate<AccessibilityNodeInfo>) null);
    }

    private void activate(AccessibilityNodeInfo node, String container) {
        activate(node, container == null ? null : value -> within(value, container));
    }

    private void activate(AccessibilityNodeInfo node, Predicate<AccessibilityNodeInfo> scope) {
        // WebView accepting ACTION_CLICK does not prove that a user tap reached its handler.
        // Send exactly one real tap to the freshly observed target; never replay a write.
        // A WebView virtual ID can change when a read-only list finishes loading.
        // Reacquire the exact unique semantic target before sending any input.
        final String expectedPackage = string(node.getPackageName()), expectedClass = string(node.getClassName());
        final String expectedText = string(node.getText()), expectedDescription = string(node.getContentDescription());
        final String expectedHint = Build.VERSION.SDK_INT >= 26 ? string(node.getHintText()) : "";
        final String expectedId = string(node.getViewIdResourceName());
        final boolean namedEditable = node.isEditable() && (!expectedHint.isEmpty() || !expectedDescription.isEmpty());
        final String accessibleName = !expectedHint.isEmpty() ? expectedHint : expectedDescription;
        require(!expectedText.isEmpty() || !expectedDescription.isEmpty() || !expectedHint.isEmpty() || !expectedId.isEmpty(), "Cannot reacquire an unnamed target");
        // A named editor's value/placeholder is mutable during editor hydration.
        // Its accessible name, role, package and optional container identify it;
        // file/content assertions remain separate from locating the input control.
        Predicate<AccessibilityNodeInfo> sameTarget = value -> expectedPackage.equals(string(value.getPackageName()))
            && (namedEditable
                // During contenteditable hydration WebView can move the same
                // ARIA name between hint and description or replace its virtual
                // class. Require its editable role and exact accessible name,
                // rather than a transient representation of that name/value.
                ? value.isEditable() && label(value, accessibleName, false)
                    && (expectedId.isEmpty() || expectedId.equals(string(value.getViewIdResourceName())))
                : expectedClass.equals(string(value.getClassName())) && expectedText.equals(string(value.getText()))
                    && expectedDescription.equals(string(value.getContentDescription()))
                    && expectedId.equals(string(value.getViewIdResourceName()))
                    && (Build.VERSION.SDK_INT < 26 || expectedHint.equals(string(value.getHintText()))))
            && (scope == null || scope.test(value));
        progress.append("target identity: ").append(namedEditable ? "editable ARIA " + accessibleName : expectedClass).append('\n');
        tapUniqueTarget(sameTarget);
    }

    private void activateNoteBody() {
        activateNoteBody(noteBody());
    }

    private void activateNoteBody(AccessibilityNodeInfo observed) {
        require(APP.equals(string(observed.getPackageName())) && observed.isEditable()
            && label(observed, "Markdown note body", false) && observed.isVisibleToUser(),
            "Refusing focus outside the observed owned Markdown body");
        // Empty rich contenteditable nodes can leave the AX tree while idle.
        // Focus the freshly observed named editor before checking stable tap
        // bounds. No text is sent here; exact role and InputConnection ownership
        // are still required by the following actions.
        require(observed.isFocused() || observed.performAction(AccessibilityNodeInfo.ACTION_FOCUS),
            "Owned named editor refused input focus; no text sent");
        // The requested semantic name is known by the caller. Do not infer it
        // from a soon-to-be-replaced virtual node or retain its resource id.
        progress.append("tap target: owned Markdown note body editable role\n");
        tapUniqueTarget(value -> APP.equals(string(value.getPackageName())) && value.isEditable()
            && label(value, "Markdown note body", false));
    }

    private void tapUniqueTarget(Predicate<AccessibilityNodeInfo> sameTarget) {
        settleUi();
        AccessibilityNodeInfo node;
        node = uniqueTarget(sameTarget);
        Rect bounds = new Rect(); node.getBoundsInScreen(bounds);
        Rect previous = new Rect(bounds);
        int stable = 0;
        for (int frame = 0; frame < 12 && stable < 3; frame++) {
            SystemClock.sleep(32);
            node = uniqueTarget(sameTarget);
            node.getBoundsInScreen(bounds);
            stable = bounds.equals(previous) ? stable + 1 : 0;
            previous.set(bounds);
        }
        require(stable >= 3, "Observed UI target did not reach stable screen bounds");
        require(node.isVisibleToUser() && node.isEnabled() && !bounds.isEmpty(), "UI target has no visible enabled bounds");
        progress.append("single touch bounds: ").append(bounds.toShortString()).append('\n');
        long time = SystemClock.uptimeMillis();
        touch(time, time, MotionEvent.ACTION_DOWN, bounds.centerX(), bounds.centerY());
        SystemClock.sleep(60);
        touch(time, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, bounds.centerX(), bounds.centerY());
    }

    private AccessibilityNodeInfo uniqueTarget(Predicate<AccessibilityNodeInfo> sameTarget) {
        long until = SystemClock.uptimeMillis() + 2000;
        do {
            List<AccessibilityNodeInfo> found = new ArrayList<>();
            for (AccessibilityNodeInfo candidate : nodes())
                if (candidate.isVisibleToUser() && candidate.isEnabled() && sameTarget.test(candidate)) found.add(candidate);
            require(found.size() <= 1, "Original semantic target is ambiguous; no input sent");
            if (found.size() == 1) return found.get(0);
            SystemClock.sleep(50);
        } while (SystemClock.uptimeMillis() < until);
        throw new IllegalStateException("Original semantic target is missing; no input sent");
    }

    private void settleUi() {
        try { ui.waitForIdle(250, 5000); }
        catch (java.util.concurrent.TimeoutException error) {
            throw new IllegalStateException("Accessibility did not reach an idle frame; no tap or write replayed", error);
        }
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
        // A touchscreen source alone leaves the short obtain() overload's
        // tool type unknown. DocumentsUI's selection router distinguishes
        // finger input: use the actual finger pointer properties as well.
        MotionEvent.PointerProperties pointer=new MotionEvent.PointerProperties();
        pointer.id=0;pointer.toolType=MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords point=new MotionEvent.PointerCoords();
        point.x=x;point.y=y;point.pressure=action==MotionEvent.ACTION_UP || action==MotionEvent.ACTION_CANCEL ? 0 : 1;point.size=1;
        MotionEvent event=MotionEvent.obtain(start,when,action,1,new MotionEvent.PointerProperties[]{pointer},
            new MotionEvent.PointerCoords[]{point},0,0,1,1,0,0,InputDevice.SOURCE_TOUCHSCREEN,0);
        require(event.getToolType(0)==MotionEvent.TOOL_TYPE_FINGER,"Owned touch has no finger identity");
        try {
            boolean accepted=ui.injectInputEvent(event,true);
            progress.append("ORIGINAL_TOUCH_RESULT action=").append(event.getActionMasked()).append(" accepted=").append(accepted)
                .append(" down_ms=").append(event.getDownTime()).append(" time_ms=").append(event.getEventTime()).append('\n');
            require(accepted,"Touch injection rejected");
        } finally { event.recycle(); }
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
        // WebView may otherwise return the pre-dialog virtual-node snapshot.
        // This clears only the test client's accessibility cache, not app data.
        if (Build.VERSION.SDK_INT >= 34) ui.clearCache();
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
            .append(node.getText()).append(" | ").append(node.getContentDescription())
            .append(" | editable=").append(node.isEditable()).append(" visible=").append(node.isVisibleToUser())
            .append(" enabled=").append(node.isEnabled()).append(" focused=").append(node.isFocused())
            .append(" hint=").append(Build.VERSION.SDK_INT>=26 ? node.getHintText() : "")
            .append(" id=").append(node.getViewIdResourceName()).append('\n');
        return text.toString();
    }
    private static void require(boolean condition, String message) { if (!condition) throw new IllegalStateException(message); }
}
