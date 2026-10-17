package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.UUID;
import org.json.JSONObject;

/** Source candidate: original UI only. The parent must prove native/provider state. */
final class OriginalOutcomeReview {
    private final AuthRuntimeProbe host;
    private final File root;
    private JSONObject input;
    OriginalOutcomeReview(AuthRuntimeProbe host) {
        this.host=host;root=host.getContext().getNoBackupFilesDir();
    }
    private static void need(boolean value) {
        if(!value)throw new IllegalStateException("Original outcome review refused");
    }
    private static boolean uuid(String value) {
        try{return UUID.fromString(value).toString().equals(value);}catch(IllegalArgumentException error){return false;}
    }
    private JSONObject read(File path,int bytes) throws Exception {
        need(path.isFile() && path.length()>0 && path.length()<=bytes);
        return new JSONObject(new String(Files.readAllBytes(path.toPath()),StandardCharsets.UTF_8));
    }
    private void publish(String stage,JSONObject value) throws Exception {
        File path=new File(root,"owned-outcome-"+stage+".json");
        need(path.createNewFile());
        try(FileOutputStream out=new FileOutputStream(path)) {
            out.write(value.toString().getBytes(StandardCharsets.UTF_8));out.getFD().sync();
        }
        // Parent reads only AFTER this completed-write ready event. File existence
        // is deliberately not the publication signal, and no write is retried.
        Bundle ready=new Bundle();ready.putString("owned_outcome_ready",input.getString("nonce"));
        ready.putString("owned_outcome_stage",stage);host.sendStatus(1,ready);
    }
    private void permission(String phase,String fingerprint) throws Exception {
        File path=new File(root,"owned-outcome-authorize-"+phase+".json");
        host.stage="wait-original-outcome-authorization-"+phase;
        long deadline=SystemClock.elapsedRealtime()+45000;
        while(!path.isFile() && SystemClock.elapsedRealtime()<deadline)Thread.sleep(50);
        // Parent must atomically publish this complete original body, not an empty
        // marker. A malformed/late permission never causes a confirmation.
        JSONObject value=read(path,1024);
        need(value.length()==3 && value.getString("nonce").equals(input.getString("nonce"))
            && value.getString("phase").equals(phase) && value.getString("fingerprint").equals(fingerprint));
    }
    private AccessibilityNodeInfo dialog() {
        AccessibilityNodeInfo confirm=host.waitFor(n->AuthRuntimeProbe.label(n,"Confirm this original content") && n.isEnabled(),true);
        for(AccessibilityNodeInfo p=confirm;p!=null;p=p.getParent())
            if(AuthRuntimeProbe.descendantLabel(p,"Review digest",0) && AuthRuntimeProbe.descendantLabel(p,"Remote sequence",0))return p;
        throw new IllegalStateException("Original frozen review not found");
    }
    private String fingerprint(AccessibilityNodeInfo root) {
        LinkedHashSet<String> values=new LinkedHashSet<>();List<AccessibilityNodeInfo> nodes=new ArrayList<>();nodes.add(root);
        for(int i=0;i<nodes.size() && nodes.size()<256;i++) {
            AccessibilityNodeInfo node=nodes.get(i);
            need("cc.kronecker.opennexus.dev".equals(AuthRuntimeProbe.text(node.getPackageName())) && !node.isPassword());
            String text=AuthRuntimeProbe.text(node.getText());if(text.matches("[0-9a-f]{64}"))values.add(text);
            for(int j=0;j<node.getChildCount();j++){AccessibilityNodeInfo child=node.getChild(j);if(child!=null)nodes.add(child);}
        }
        need(nodes.size()<256 && values.size()==1 && AuthRuntimeProbe.descendantLabel(root,"2",0)
            && AuthRuntimeProbe.descendantLabel(root,OriginalConflictScenario.COPY,0));
        return values.iterator().next();
    }
    private String freeze(String name,String hash) throws Exception {
        AccessibilityNodeInfo field=host.waitFor(n->{
            if(!n.isEditable() || !n.isEnabled() || n.isPassword())return false;
            AccessibilityNodeInfo parent=n.getParent();return parent!=null && AuthRuntimeProbe.descendantLabel(parent,"Relative copy path",0);
        },true);
        Bundle value=new Bundle();value.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,OriginalConflictScenario.COPY);
        need(field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,value));
        host.click("Save local copy and use remote");String digest=fingerprint(dialog());
        publish(name,new JSONObject().put("nonce",input.getString("nonce")).put("sequence",2)
            .put("choice","copy").put("destination",OriginalConflictScenario.COPY).put("fingerprint",digest)
            .put("expected_hash_from_parent_native_observation",hash).put("expected_hash_displayed_by_dialog",false)
            .put("native_operation_ids_not_assigned_by_driver",true));
        return digest;
    }
    void run() throws Exception {
        input=read(new File(root,"owned-outcome-input.json"),1024);
        need(input.length()==3 && uuid(input.getString("nonce"))
            && input.getString("initial_hash").matches("[0-9a-f]{64}")
            && input.getString("concurrent_hash").matches("[0-9a-f]{64}")
            && !input.getString("initial_hash").equals(input.getString("concurrent_hash")));
        host.readCurrent();host.signedIn();host.click("Read queue and original records");
        host.click("Read original decision and content changes");
        host.waitPresent(n->AuthRuntimeProbe.label(n,"Current local"));
        host.waitPresent(n->AuthRuntimeProbe.label(n,"Remote revision"));
        String old=freeze("old-frozen",input.getString("initial_hash"));
        permission("stale-confirm",old);
        need(fingerprint(dialog()).equals(old));
        publish("stale-confirm-intent",new JSONObject().put("nonce",input.getString("nonce")).put("fingerprint",old));
        host.click("Confirm this original content");
        // The current sync_resolve_reviewed_checked producer returns this exact
        // code for a fingerprint/hash change before any decision is created.
        host.waitPresent(n->AuthRuntimeProbe.text(n.getText()).contains("(SYNC_CONFLICT_CHANGED)"));
        publish("known-stale-rejection",new JSONObject().put("nonce",input.getString("nonce"))
            .put("fingerprint",old).put("expected_public_code","SYNC_CONFLICT_CHANGED")
            .put("known_rejection_not_unknown_result",true).put("native_rows_not_proven_by_UI",true));
        permission("native-absence-checked",old);
        host.click("Read original decision");
        host.waitPresent(n->AuthRuntimeProbe.text(n.getText()).startsWith("This original decision was not found."));
        host.click("Keep original record and return");host.click("Read original decision and content changes");
        host.waitPresent(n->AuthRuntimeProbe.label(n,"Current local"));
        String current=freeze("new-frozen",input.getString("concurrent_hash"));need(!current.equals(old));
        permission("interrupt-confirm",current);
        need(fingerprint(dialog()).equals(current));
        publish("interrupt-confirm-intent",new JSONObject().put("nonce",input.getString("nonce")).put("fingerprint",current));
        host.click("Confirm this original content");
        host.stage="wait-original-native-pause-and-owned-process-exit";
        host.waitPresent(n->AuthRuntimeProbe.text(n.getText()).startsWith("The original local decision completed."));
        // This phase is meant to be deliberately interrupted, never reported as
        // successful by an ordinary completed callback or a later UI pass.
        throw new IllegalStateException("Original completion was consumed; interruption not proved");
    }
}
