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
                case "storage-init": waitLabel("Start writing"); break;
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

    private void originalNoteSearch(String phase) throws Exception {
        if(phase.equals("search-seed-original")||phase.equals("search-seed-control")) {
            boolean original=phase.equals("search-seed-original");
            create("New note",original?OriginalNoteSearch.PATH:OriginalNoteSearch.CONTROL_PATH);
            inputText(noteBody(),original?OriginalNoteSearch.SEED:OriginalNoteSearch.CONTROL);save();return;
        }
        if(phase.equals("search-browse")) {
            waitNotesLanding();clickTitleBar("Files");
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
        List<AccessibilityNodeInfo> visible=new ArrayList<>();
        for(AccessibilityNodeInfo node:nodes())if(node.isVisibleToUser()&&named.test(node))visible.add(node);
        require(visible.size()<=1,"Ambiguous original document action; no input sent");
        if(visible.isEmpty())clickMenu("More actions");
        else require(visible.get(0).isEnabled()&&visible.get(0).isClickable(),"Original document action is disabled; no toggle or action sent");
        // Cancelling the review can leave this actual document menu open.
        // Observe its original action before deciding whether one opening tap is needed.
        Predicate<AccessibilityNodeInfo> ready = n -> named.test(n)&&n.isEnabled()&&n.isClickable();
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

    private void open(String path) {
        clickTitleBar("Files");
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
