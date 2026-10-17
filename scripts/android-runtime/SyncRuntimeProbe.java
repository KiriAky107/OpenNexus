package cc.kronecker.opennexus.testdriver;

import android.view.accessibility.AccessibilityNodeInfo;
import android.os.SystemClock;
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
    @Override protected boolean semanticClicks() { return true; }
    @Override protected String[] diagnosticLabels() {
        return new String[]{"Read remote vaults","Choose a vault","Read local association","Preview merge",
            "Initial merge preview","Confirm merge and bind","Confirm this association","No remote vault is associated yet.",
            "Read queue and original records","Sync one cycle now","Stop this cycle","Cycle completed","Cycle stopped",
            "Cycle did not complete","Last recorded sync error"};
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
        click("Sync one cycle now");
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

    private JSONObject configuration() throws Exception {
        File file=new File(getContext().getNoBackupFilesDir(),"owned-sync.json");
        require(file.isFile() && file.length()>0 && file.length()<=2048,"Owned Sync input missing");
        JSONObject value=new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8));
        require(value.length()==2 && value.getString("vault_id").matches("[0-9a-f]{32}")
            && value.getString("vault_name").matches("Owned Android Sync [0-9a-f]{32}"),"Owned Sync input changed");
        return value;
    }

    private void associate() throws Exception {
        JSONObject config=configuration();
        readCurrent(); signedIn(); click("Read remote vaults");
        click("Choose a vault");
        try { click(config.getString("vault_name")+" · 0"); }
        catch (Throwable error) { stage="choose-owned-vault"; throw error; }
        click("Preview merge"); click("Confirm merge and bind");
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
