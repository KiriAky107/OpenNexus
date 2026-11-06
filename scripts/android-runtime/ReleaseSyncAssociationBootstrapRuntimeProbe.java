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
import org.json.JSONObject;

/** Source-only signed association producer; no manifest or live host binding.
 * Public UI success and an own-driver intent never attest Native state or ACK.
 */
public final class ReleaseSyncAssociationBootstrapRuntimeProbe extends SyncRuntimeProbe {
    static final String PHASE="associate-bootstrap-signed-original";
    static final String NAMESPACE="SIGNED_ASSOCIATION_BOOTSTRAP";
    static final String UI_LINE="OPENNEXUS_SIGNED_ASSOCIATION_BOOTSTRAP_UI association_confirmed_UI_observed=true original_intent_written=true raw_UI_recorded=false native_original_state_verified=false\n";
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private boolean created,started,acted,closed;

    public ReleaseSyncAssociationBootstrapRuntimeProbe() {super(true);}
    @Override public void onCreate(Bundle args) {
        try {
            require(!created && !closed && args!=null && args.size()==1
                && args.get("phase") instanceof String && PHASE.equals(args.get("phase")),"Owned association phase refused");
            guardDriver();created=true;super.onCreate(args);
        } catch(RuntimeException error) {refuse();}
    }
    @Override public void onStart() {
        if(!created || started || closed) {refuse();return;}
        started=true;
        // The inherited private openConnection runs before additionalPhase.
        try {super.onStart();} finally {closed=true;}
    }
    private void refuse() {
        closed=true;Bundle result=new Bundle();
        result.putString("stream","OPENNEXUS_SIGNED_ASSOCIATION_BOOTSTRAP_SCOPE_REFUSED no_product_input=1\n");
        finish(1,result);
    }
    @Override protected String namespace() {return NAMESPACE;}
    @Override protected boolean productUiObservationClosed() {return true;}
    @Override protected List<AccessibilityNodeInfo> nodes() {
        require(!closed,"Owned association UI observation closed");return super.nodes();
    }
    @Override protected boolean additionalPhase(String phase) throws Exception {
        require(created && started && !acted && !closed && PHASE.equals(phase),"Owned association already closed");
        acted=true;
        try {
            guardDriver();JSONObject config=configuration();
            readCurrent();signedIn();click("Read remote vaults");click("Choose a vault");
            try {click(config.getString("vault_name")+" · 0");}
            catch(Throwable error) {stage="choose-owned-vault";throw error;}
            previewMerge();stage="wait-original-preview-before-confirm-search";
            OriginalSyncPreview.await(()->{
                int sections=0,confirmations=0,enabled=0,errors=0;boolean owned=true;
                List<AccessibilityNodeInfo> scan=nodes();
                require(!scan.isEmpty() && scan.size()<2500,"Owned association preview incomplete");
                for(AccessibilityNodeInfo node:scan) {
                    if(node.isPassword() || node.isEditable())continue;
                    boolean section=label(node,"Initial merge preview"),confirm=label(node,"Confirm merge and bind");
                    if(section || confirm) {
                        owned &= APP.equals(text(node.getPackageName()));
                        if(section)sections++;
                        if(confirm){confirmations++;if(node.isEnabled())enabled++;}
                    }
                    for(String code:diagnosticCodes())if(text(node.getText()).endsWith("("+code+")"))errors++;
                }
                return new OriginalSyncPreview.State(sections,confirmations,enabled,errors,owned);
            },SystemClock::uptimeMillis,SystemClock::sleep,value->{/* no dynamic stream export */});
            click("Confirm merge and bind");stage="review-original-association";
            waitPresent(node->label(node,config.optString("vault_id")));
            AccessibilityNodeInfo button=waitFor(node->label(node,"Confirm this association") && node.isEnabled(),true);
            AccessibilityNodeInfo dialog=button;
            for(int depth=0;depth<8 && dialog!=null;depth++,dialog=dialog.getParent())
                if(descendantLabel(dialog,"Original ID",0) && descendantLabel(dialog,"Preview digest",0))break;
            require(dialog!=null,"Frozen original association unavailable; no confirmation sent");
            LinkedHashSet<String> ids=new LinkedHashSet<>(),digests=new LinkedHashSet<>();
            List<AccessibilityNodeInfo> children=new ArrayList<>();children.add(dialog);
            for(int i=0;i<children.size() && children.size()<128;i++) {
                AccessibilityNodeInfo node=children.get(i);
                admitReviewPackage(node.getPackageName(),node.isPassword());
                String value=text(node.getText());
                if(value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))ids.add(value);
                if(value.matches("[0-9a-f]{64}"))digests.add(value);
                for(int j=0;j<node.getChildCount();j++) {AccessibilityNodeInfo child=node.getChild(j);if(child!=null)children.add(child);}
            }
            require(children.size()<128 && ids.size()==1 && digests.size()==1,"Frozen original association ambiguous; no confirmation sent");
            writeIntent(new JSONObject().put("operation_id",ids.iterator().next())
                .put("preview_fingerprint",digests.iterator().next()).put("remote_vault",config.getString("vault_id")));
            click("Confirm this association");
            waitPresent(node->text(node.getText()).startsWith("Associated remote vault")
                && text(node.getText()).contains(config.optString("vault_id")));
            Bundle report=new Bundle();report.putString("stream",UI_LINE);sendStatus(1,report);
            return true;
        } finally {closed=true;}
    }
    private void guardDriver() {
        require(DRIVER.equals(getContext().getPackageName()) && DRIVER.equals(getTargetContext().getPackageName()),
            "Owned association instrumentation changed");
    }
    private JSONObject configuration() throws Exception {
        File file=new File(getContext().getNoBackupFilesDir(),"owned-sync.json");
        require(file.isFile() && file.length()>0 && file.length()<=2048,"Owned Sync input missing");
        JSONObject value=new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8));
        require(value.length()==2 && value.getString("vault_id").matches("[0-9a-f]{32}")
            && value.getString("vault_name").matches("Owned Android Sync [0-9a-f]{32}"),"Owned Sync input changed");
        return value;
    }
    private void writeIntent(JSONObject value) throws Exception {
        guardDriver();stage="persist-original-association";
        byte[] raw=value.toString().getBytes(StandardCharsets.UTF_8);
        require(raw.length>0 && raw.length<=2048,"Owned association intent outside bound");
        File file=new File(getContext().getNoBackupFilesDir(),"owned-sync-association-intent.json");
        require(file.createNewFile(),"Existing original intent; no mutation replayed");
        try(FileOutputStream output=new FileOutputStream(file)) {output.write(raw);output.getFD().sync();}
    }
}
