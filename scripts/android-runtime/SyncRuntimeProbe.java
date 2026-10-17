package cc.kronecker.opennexus.testdriver;

import android.view.accessibility.AccessibilityNodeInfo;
import android.os.SystemClock;
import android.os.Bundle;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.UUID;
import org.json.JSONObject;

/** Actual Sync UI commands, never an HTTP/native/file-operation bridge to the product. */
public final class SyncRuntimeProbe extends AuthRuntimeProbe {
    @Override protected String namespace() { return "SYNC"; }
    @Override protected boolean viewportAdmission() { return true; }
    @Override protected String[] diagnosticCodes() {
        return new String[]{"SYNC_RESPONSE_INVALID","SYNC_NETWORK_ERROR","SYNC_CONNECTION_FAILED","SYNC_AUTH_SELECTION_CHANGED",
            "SYNC_AUTH_IDENTITY_CHANGED","SYNC_AUTH_RECONCILIATION_REQUIRED","SYNC_SESSION_EXPIRED","SYNC_LOGIN_REQUIRED",
            "SYNC_FOREGROUND_CHANGED","SYNC_FOREGROUND_REQUIRED","SYNC_ALREADY_BOUND","SYNC_PREVIEW_CHANGED","SYNC_PREVIEW_PAGE_INVALID",
            "SYNC_BINDING_REQUEST_INVALID","SYNC_BINDING_PAGE_INVALID","SYNC_BINDING_INTENT_INVALID","SYNC_BINDING_INTENT_NOT_FOUND",
            "SYNC_BINDING_INTENT_CHANGED","SYNC_OPERATION_MISMATCH","SYNC_WORKER_INTERRUPTED","VAULT_CHANGED","HOST_BUSY",
            "MOBILE_STORAGE_UNAVAILABLE","MOBILE_HOST_REQUIRED","SYNC_PLATFORM_UNSUPPORTED","QUOTA_EXCEEDED","FILESYSTEM_ERROR","DATABASE_ERROR"};
    }
    @Override protected String[] diagnosticLabels() {
        return new String[]{"Read remote vaults","Choose a vault","Read local association","Preview merge",
            "Initial merge preview","Confirm merge and bind","Confirm this association","No remote vault is associated yet.",
            "Read queue and original records","Sync one cycle now","Stop this cycle","Cycle completed","Cycle stopped",
            "Cycle did not complete","Last recorded sync error",
            "Sync did not complete. Check service status and retry. (SYNC_CONNECTION_FAILED)",
            "Sync did not complete. Check service status and retry. (SYNC_RESPONSE_INVALID)",
            "Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)",
            "Local files or the remote snapshot changed. Review a new preview."};
    }

    @Override protected boolean additionalPhase(String phase) throws Exception {
        switch (phase) {
            case "associate": associate(); return true;
            case "status":
                readCurrent(); signedIn(); click("Read queue and original records");
                waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true); return true;
            case "association-original":
                readCurrent(); signedIn(); click("Read this association’s original receipt");
                waitPresent(node->text(node.getText()).startsWith("Original association committed")); return true;
            case "cycle": cycle("cycle"); return true;
            case "cycle-incoming": cycle("cycle-incoming"); return true;
            case "cycle-media": cycle("cycle-media"); return true;
            case "cycle-rename": cycle("cycle-rename"); return true;
            case "cycle-delete": cycle("cycle-delete"); return true;
            case "cycle-after-stop": cycle("cycle-after-stop"); return true;
            case "cycle-reconcile-original": cycle("cycle-reconcile-original"); return true;
            case "cycle-conflict": cycle("cycle-conflict"); return true;
            case "cycle-conflict-copy": cycle("cycle-conflict-copy"); return true;
            case "conflict-copy": resolveOriginalCopy(); return true;
            case "cold-original-decision": readColdOriginalCopy(); return true;
            case "cycle-offline": failedCycle("cycle-offline"); return true;
            case "cycle-dropped-upload": failedCycle("cycle-dropped-upload"); return true;
            case "cycle-stop": stopOriginal(); return true;
            case "close": click("Close"); return true;
            default: return false;
        }
    }

    private void cycle(String original) throws Exception {
        readCurrent(); signedIn(); click("Read queue and original records");
        waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
        // Distinct explicit cycles have separate once-only driver intents.
        // These are not substituted for native invocation or file-operation IDs.
        persistOnce(original,new JSONObject().put("driver_intent_id",UUID.randomUUID().toString())
            .put("native_invocation_id_observed",false).put("action","Sync one cycle now"));
        String mode=originalCycleInputMode();
        require(mode.equals("touch")||mode.equals("webview-touch"),"Unknown original cycle input mode");
        if(mode.equals("webview-touch")) {
            String nonce=originalCycleDispatchId();
            require(nonce.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),"Original cycle dispatch is missing");
            stage="owned-webview-cycle-ready-"+original;
            Bundle ready=new Bundle();ready.putString("owned_cycle_ready",nonce);sendStatus(1,ready);
        } else click("Sync one cycle now");
        stage="wait-original-cycle-completion-"+original;
        waitPresent(node->label(node,"Cycle completed"));
    }

    private void stopOriginal() throws Exception {
        File root=getContext().getNoBackupFilesDir();
        File input=new File(root,"owned-sync-stop-input.json");
        require(input.isFile() && input.length()>0 && input.length()<=1024,"Original Stop input unavailable");
        JSONObject original=new JSONObject(new String(Files.readAllBytes(input.toPath()),StandardCharsets.UTF_8));
        require(original.length()==2 && canonical(original.getString("driver_intent_id"))
            && canonical(original.getString("control_operation_id")),"Original Stop input changed");
        readCurrent(); signedIn(); click("Read queue and original records");
        waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
        persistOnce("cycle-stop",original); // Driver/control IDs are not a native invocation ID.
        click("Sync one cycle now");
        waitFor(node->label(node,"Stop this cycle") && node.isEnabled(),true);
        persistOnce("cycle-stop-ready",original);
        File permission=new File(root,"owned-sync-stop-authorize.json");
        stage="wait-original-held-response-before-Stop";
        long deadline=SystemClock.elapsedRealtime()+30000;
        while (!permission.isFile() && SystemClock.elapsedRealtime()<deadline) Thread.sleep(50);
        require(permission.isFile() && permission.length()>0 && permission.length()<=1024,"Original Stop not authorized; no Stop tap sent");
        JSONObject approved=new JSONObject(new String(Files.readAllBytes(permission.toPath()),StandardCharsets.UTF_8));
        require(approved.length()==2
            && original.getString("driver_intent_id").equals(approved.optString("driver_intent_id"))
            && original.getString("control_operation_id").equals(approved.optString("control_operation_id")),
            "Another Stop checkpoint; no Stop tap sent");
        persistOnce("cycle-stop-confirmation",original);
        click("Stop this cycle");
        waitPresent(node->label(node,"Cycle stopped"));
    }

    private void failedCycle(String original) throws Exception {
        readCurrent(); signedIn(); click("Read queue and original records");
        waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
        persistOnce(original,new JSONObject().put("driver_intent_id",UUID.randomUUID().toString())
            .put("native_invocation_id_observed",false).put("action","Sync one cycle now"));
        click("Sync one cycle now");
        waitPresent(node->label(node,"Cycle did not complete"));
        waitPresent(node->text(node.getText()).startsWith("The previous outcome is not fully confirmed."));
        waitFor(node->label(node,"Sync one cycle now") && !node.isEnabled(),true);
    }

    private static boolean canonical(String value) {
        try { return UUID.fromString(value).toString().equals(value); }
        catch (IllegalArgumentException error) { return false; }
    }

    private AccessibilityNodeInfo originalCopyDialog() {
        AccessibilityNodeInfo button=waitFor(node->label(node,"Confirm this original content") && node.isEnabled(),true);
        for (AccessibilityNodeInfo parent=button;parent!=null;parent=parent.getParent())
            if (descendantLabel(parent,"Review digest",0) && descendantLabel(parent,"Remote sequence",0)) return parent;
        throw new IllegalStateException("Frozen conflict review is not observable; no confirmation sent");
    }
    private LinkedHashSet<String> matchingValues(AccessibilityNodeInfo root,String pattern) {
        LinkedHashSet<String> result=new LinkedHashSet<>();List<AccessibilityNodeInfo> pending=new ArrayList<>();pending.add(root);
        for(int i=0;i<pending.size() && pending.size()<256;i++) {
            AccessibilityNodeInfo node=pending.get(i);
            require("cc.kronecker.opennexus.dev".equals(text(node.getPackageName())) && !node.isPassword(),"Foreign conflict review");
            String value=text(node.getText());if(value.matches(pattern))result.add(value);
            for(int j=0;j<node.getChildCount();j++){AccessibilityNodeInfo child=node.getChild(j);if(child!=null)pending.add(child);}
        }
        require(pending.size()<256,"Conflict review exceeds bound");return result;
    }
    private void resolveOriginalCopy() throws Exception {
        readCurrent();signedIn();click("Read queue and original records");
        click("Read original decision and content changes");
        waitPresent(node->label(node,"Current local"));waitPresent(node->label(node,"Remote revision"));
        waitPresent(node->text(node.getText()).contains("Android 本机并发修改 😀"));
        waitPresent(node->text(node.getText()).contains("独立客户端并发修改 😀"));
        stage="original-conflict-copy-path";
        AccessibilityNodeInfo input=waitFor(node->{
            if(!node.isEditable() || !node.isEnabled() || node.isPassword())return false;
            AccessibilityNodeInfo parent=node.getParent();
            return parent!=null && descendantLabel(parent,"Relative copy path",0);
        },true);
        Bundle value=new Bundle();value.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,OriginalConflictScenario.COPY);
        require(input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,value),"Original copy path not accepted");
        click("Save local copy and use remote");
        AccessibilityNodeInfo dialog=originalCopyDialog();
        LinkedHashSet<String> digests=matchingValues(dialog,"[0-9a-f]{64}");
        require(digests.size()==1 && descendantLabel(dialog,"2",0) && descendantLabel(dialog,OriginalConflictScenario.COPY,0),
            "Frozen conflict scope differs; no confirmation sent");
        JSONObject frozen=new JSONObject().put("driver_intent_id",UUID.randomUUID().toString()).put("sequence",2)
            .put("choice","copy").put("destination",OriginalConflictScenario.COPY).put("fingerprint",digests.iterator().next())
            .put("native_operation_ids_not_yet_assigned",true);
        persistOnce("conflict-copy-frozen",frozen);
        click("Cancel");
        click("Save local copy and use remote");
        dialog=originalCopyDialog();
        require(matchingValues(dialog,"[0-9a-f]{64}").equals(digests) && descendantLabel(dialog,"2",0)
            && descendantLabel(dialog,OriginalConflictScenario.COPY,0),"Canceled original review was replaced; no confirmation sent");
        persistOnce("conflict-copy-confirmation",frozen);
        click("Confirm this original content");
        waitPresent(node->text(node.getText()).startsWith("The original local decision completed."));
        click("Read original decision");
        waitPresent(node->text(node.getText()).startsWith("The original local decision completed."));
        AccessibilityNodeInfo query=waitFor(node->label(node,"Read original decision") && node.isEnabled(),true);
        AccessibilityNodeInfo section=query;
        while(section!=null && !descendantLabel(section,"Original conflict decision",0))section=section.getParent();
        require(section!=null,"Original conflict receipt section unavailable");
        LinkedHashSet<String> ids=matchingValues(section,"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
        require(ids.size()==5,"Original conflict receipt does not contain five distinct IDs");
        org.json.JSONArray originals=new org.json.JSONArray();for(String id:ids)originals.put(id);
        persistOnce("conflict-copy-receipt",new JSONObject().put("sequence",2).put("fingerprint",frozen.getString("fingerprint"))
            .put("original_native_ids",originals).put("read_only_original_query",true));
    }

    private JSONObject configuration() throws Exception {
        File file=new File(getContext().getNoBackupFilesDir(),"owned-sync.json");
        require(file.isFile() && file.length()>0 && file.length()<=2048,"Owned Sync input missing");
        JSONObject value=new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8));
        require(value.length()==2 && value.getString("vault_id").matches("[0-9a-f]{32}")
            && value.getString("vault_name").matches("Owned Android Sync [0-9a-f]{32}"),"Owned Sync input changed");
        return value;
    }

    private void readColdOriginalCopy() throws Exception {
        readCurrent();signedIn();click("Read queue and original records");
        waitFor(node->label(node,"Read original decisions") && node.isEnabled(),true);
        for(AccessibilityNodeInfo node:nodes())
            require(!label(node,"Original conflict decision"),"Cold original unexpectedly already displayed");
        click("Read original decisions");
        waitPresent(node->text(node.getText()).startsWith("Remote sequence 2"));
        waitPresent(node->text(node.getText()).contains("Completed locally"));
        click("Read this original decision");
        waitPresent(node->text(node.getText()).startsWith("The original local decision completed."));
        AccessibilityNodeInfo query=waitFor(node->label(node,"Read original decision") && node.isEnabled(),true);
        AccessibilityNodeInfo section=query;
        while(section!=null && !descendantLabel(section,"Original conflict decision",0))section=section.getParent();
        require(section!=null && !matchingValues(section,"(?s).*"+java.util.regex.Pattern.quote(OriginalConflictScenario.COPY)+".*").isEmpty(),
            "Cold original target differs");
        LinkedHashSet<String> ids=matchingValues(section,"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
        require(ids.size()==5,"Cold original does not retain five distinct IDs");
        for(AccessibilityNodeInfo node:nodes())
            require(!label(node,"Review and continue original decision"),"Completed original exposed a replay action");
        org.json.JSONArray originals=new org.json.JSONArray();for(String id:ids)originals.put(id);
        persistOnce("conflict-copy-cold-receipt",new JSONObject().put("sequence",2)
            .put("original_native_ids",originals).put("read_only_original_query",true)
            .put("cold_original_list_entry",true).put("review_digest_displayed_in_original_panel",false)
            .put("native_operations_created_by_driver",false));
    }

    private void associate() throws Exception {
        JSONObject config=configuration();
        readCurrent(); signedIn(); click("Read remote vaults");
        click("Choose a vault");
        try { click(config.getString("vault_name")+" · 0"); }
        catch (Throwable error) { stage="choose-owned-vault"; throw error; }
        previewMerge(); click("Confirm merge and bind");
        stage="review-original-association";
        waitPresent(node->label(node,config.optString("vault_id")));
        AccessibilityNodeInfo button=waitFor(node->label(node,"Confirm this association") && node.isEnabled(),true);
        AccessibilityNodeInfo dialog=button;
        for (int depth=0;depth<8 && dialog!=null;depth++,dialog=dialog.getParent()) {
            if (descendantLabel(dialog,"Original ID",0) && descendantLabel(dialog,"Preview digest",0)) break;
        }
        require(dialog!=null,"Frozen original association is not observable; no confirmation sent");
        LinkedHashSet<String> ids=new LinkedHashSet<>(),digests=new LinkedHashSet<>();
        List<AccessibilityNodeInfo> children=new ArrayList<>();children.add(dialog);
        for (int i=0;i<children.size() && children.size()<128;i++) {
            AccessibilityNodeInfo node=children.get(i);String value=text(node.getText());
            require("cc.kronecker.opennexus.dev".equals(text(node.getPackageName())) && !node.isPassword(),"Foreign frozen review");
            if (value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) ids.add(value);
            if (value.matches("[0-9a-f]{64}")) digests.add(value);
            for (int j=0;j<node.getChildCount();j++) { AccessibilityNodeInfo child=node.getChild(j);if(child!=null) children.add(child); }
        }
        require(children.size()<128 && ids.size()==1 && digests.size()==1,"Frozen IDs/digest ambiguous; no confirmation sent");
        persistOnce("association",new JSONObject().put("operation_id",ids.iterator().next())
            .put("preview_fingerprint",digests.iterator().next()).put("remote_vault",config.getString("vault_id")));
        click("Confirm this association");
        waitPresent(node->text(node.getText()).startsWith("Associated remote vault")
            && text(node.getText()).contains(config.optString("vault_id")));
    }

    private void persistOnce(String kind,JSONObject original) throws Exception {
        stage="persist-original-"+kind;
        File file=new File(getContext().getNoBackupFilesDir(),"owned-sync-"+kind+"-intent.json");
        require(file.createNewFile(),"Existing original intent; no mutation replayed");
        try (FileOutputStream output=new FileOutputStream(file)) {
            output.write(original.toString().getBytes(StandardCharsets.UTF_8));output.getFD().sync();
        }
    }
}
