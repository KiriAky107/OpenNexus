package cc.kronecker.opennexus.testdriver;

import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Dedicated signed quota UI producer. Not registered or wired to a live host yet.
 * A complete public UI report is never a Native, provider, DB or client ACK.
 */
public final class ReleaseSyncQuotaRuntimeProbe extends SyncRuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private static final String UNKNOWN="The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.";
    private OriginalSyncQuotaActions actions;
    private File root;
    private int originalUid=-1,originalWindow=-1;
    private boolean closed;

    public ReleaseSyncQuotaRuntimeProbe() {super(true);}
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !OriginalSyncQuotaActions.PHASE.equals(args.getString("phase",""))) {
            closed=true;Bundle result=new Bundle();
            result.putString("stream","OPENNEXUS_RELEASE_SYNC_QUOTA_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() {return "SIGNED_QUOTA";}
    // The unchanged superclass honors this for both clearPassword and failure
    // shape. This entry never performs a diagnostic UI reread or password edit.
    @Override protected boolean productUiObservationClosed() {return true;}
    @Override protected List<AccessibilityNodeInfo> nodes() {
        require(!closed,"Owned quota UI observation closed");return super.nodes();
    }
    @Override protected boolean hostedCycleTouch(String name) {return "Sync one cycle now".equals(name);}
    @Override protected boolean hostedCyclePhaseAllowed(String phase) {return OriginalSyncQuotaActions.PHASE.equals(phase);}

    @Override protected boolean additionalPhase(String phase) throws Exception {
        require(!closed && actions==null && OriginalSyncQuotaActions.PHASE.equals(phase),"Owned quota phase refused");
        actions=new OriginalSyncQuotaActions(phase,UUID.randomUUID().toString(),UUID.randomUUID().toString());
        try {
            guardDriver();root=getContext().getNoBackupFilesDir();
            write("intent.json",new JSONObject().put("schema",1).put("phase",phase)
                .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input)
                .put("native_invocation_id_observed",false));
            readCurrent();signedIn();click("Read queue and original records");
            AccessibilityNodeInfo button=waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
            guardOwner(button.getWindowId(),null);
            stage="quota-original-context-before-ready";
            actions.awaitContext(()->guardOwner(originalWindow,null),()->{
                String name=actions.contextName();
                boolean complete=OriginalAuthSignal.complete(new File(root,name));
                return actions.contextSample(name,Boolean.valueOf(complete));
            },SystemClock::uptimeMillis,SystemClock::sleep);
            actions.begin("cycle-start");click("Sync one cycle now");
            stage="quota-original-bounded-full-failure";
            actions.awaitFailure(()->guardOwner(originalWindow,null),this::readScene,
                SystemClock::elapsedRealtime,SystemClock::sleep);
            long sampled=actions.failureAt();
            require(sampled>=0 && sampled==actions.lastSceneAt(),"Owned quota scene scope changed");
            JSONArray counts=new JSONArray();for(int n:actions.lastScene())counts.put(n);
            write("failed-ready.json",new JSONObject().put("schema",1).put("phase",phase)
                .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input)
                .put("package",APP).put("uid",originalUid).put("window",originalWindow)
                .put("elapsed_ms",sampled).put("scene_scan_elapsed_ms",sampled).put("scene_counts",counts)
                .put("public_alert_rule_id",ReleaseSyncQuotaScene.RULE)
                .put("failed_cycle_UI_observed",true).put("quota_error_UI_observed",true)
                .put("raw_UI_recorded",false).put("native_original_state_verified",false));
            actions.complete();facts(actions.uiSuccessLine());return true;
        } finally {closed=true;actions.close();}
    }

    @Override protected void awaitHostedCycleTouch(Rect original,Rect visible,int window) {
        try {
            require(actions!=null && !closed,"Owned quota input scope unavailable");guardOwner(window,visible);
            checkedRect(original,false);checkedRect(visible,true);
            require(original.left<=visible.left && original.top<=visible.top
                && visible.right<=original.right && visible.bottom<=original.bottom,"Owned quota hit outside original");
            JSONArray full=new JSONArray(),hit=new JSONArray();
            for(int n:new int[]{original.left,original.top,original.right,original.bottom})full.put(n);
            for(int n:new int[]{visible.left,visible.top,visible.right,visible.bottom})hit.put(n);
            long elapsed=SystemClock.elapsedRealtime();actions.ready(elapsed);
            write("cycle-start-ready.json",new JSONObject().put("schema",1).put("phase",OriginalSyncQuotaActions.PHASE)
                .put("action","cycle-start").put("parent_intent_id",actions.parent).put("driver_intent_id",actions.input)
                .put("package",APP).put("uid",originalUid).put("window",window).put("original",full).put("visible",hit)
                .put("x",visible.centerX()).put("y",visible.centerY()).put("elapsed_ms",elapsed));
            actions.awaitInputFinish(()->guardOwner(window,null),()->{
                String name=actions.inputFinishName();
                boolean complete=OriginalAuthSignal.complete(new File(root,name));
                return actions.inputFinishSample(name,Boolean.valueOf(complete));
            },SystemClock::elapsedRealtime,SystemClock::sleep);
        } catch(Exception error) {
            if(actions!=null)actions.close();throw new IllegalStateException("OWNED_SYNC_QUOTA_INPUT_NOT_FINISHED");
        }
    }

    private OriginalSyncQuotaActions.SceneSample readScene() throws Exception {
        actions.beforeProductUi();List<AccessibilityNodeInfo> scan=nodes();
        require(!scan.isEmpty() && scan.size()<2500,"Owned quota scene incomplete");
        AccessibilityNodeInfo first=scan.get(0);
        require(APP.equals(text(first.getPackageName())) && first.getWindowId()==originalWindow
            && !first.isPassword() && !first.isEditable(),"Owned quota scene root changed");
        int[] counts=new int[8];
        for(AccessibilityNodeInfo node:scan) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=originalWindow
                || node.isPassword() || node.isEditable() || !node.isVisibleToUser())continue;
            // Exactly the same two channel reads feed every closed counter.
            String value=text(node.getText()),description=text(node.getContentDescription());
            if(pair(value,description,"Cycle did not complete"))counts[0]++;
            if(UNKNOWN.equals(value))counts[2]++;
            if(ReleaseSyncQuotaScene.strictPair(value,description))counts[3]++;
            if(pair(value,description,"Sync one cycle now")) {
                if(node.isEnabled())counts[7]++;
                else {Rect bounds=new Rect();node.getBoundsInScreen(bounds);guardOwner(originalWindow,bounds);counts[1]++;}
            }
            if(pair(value,description,"Stop this cycle") && node.isEnabled())counts[4]++;
            if(pair(value,description,"Cycle completed"))counts[5]++;
            if(pair(value,description,"Cycle stopped"))counts[6]++;
        }
        long sampled=SystemClock.elapsedRealtime();
        return actions.sceneSample(counts,sampled);
    }
    private static boolean pair(String value,String description,String label) {return label.equals(value) || label.equals(description);}
    private void guardDriver() {
        require(DRIVER.equals(getContext().getPackageName()) && DRIVER.equals(getTargetContext().getPackageName()),"Owned quota instrumentation changed");
    }
    private void guardOwner(int window,Rect region) throws Exception {
        require(actions!=null && !closed,"Owned quota observer closed");actions.beforeProductUi();guardDriver();
        require(window>=1 && window<=65535,"Owned quota window outside scope");
        android.app.UiAutomation ui=getUiAutomation();if(android.os.Build.VERSION.SDK_INT>=34)ui.clearCache();
        AccessibilityNodeInfo active=ui.getRootInActiveWindow();
        require(active!=null && APP.equals(text(active.getPackageName())) && !active.isPassword()
            && !active.isEditable() && active.getWindowId()==window,"Owned quota original root unavailable");
        int uid=getContext().getPackageManager().getApplicationInfo(APP,0).uid;
        require(uid>=10000 && uid<=99999 && (originalUid<0 || originalUid==uid)
            && (originalWindow<0 || originalWindow==window),"Owned quota original owner changed");
        List<AccessibilityWindowInfo> windows=ui.getWindows();AccessibilityWindowInfo own=null;int count=0;
        for(AccessibilityWindowInfo candidate:windows) {
            require(candidate.getType()!=AccessibilityWindowInfo.TYPE_INPUT_METHOD,"Owned quota keyboard visible");
            if(candidate.getId()==window){own=candidate;count++;}
        }
        require(count==1 && own!=null && own.isActive() && own.isFocused()
            && own.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && own.getDisplayId()==0,"Owned quota window changed");
        if(region!=null)for(AccessibilityWindowInfo other:windows)
            if(other.getId()!=window && other.getDisplayId()==0 && other.getLayer()>=own.getLayer()) {
                Rect covered=new Rect();other.getBoundsInScreen(covered);require(!Rect.intersects(covered,region),"Owned quota input obstructed");
            }
        originalUid=uid;originalWindow=window;
    }
    private static void checkedRect(Rect value,boolean visible) {
        require(value!=null && value.left<value.right && value.top<value.bottom,"Owned quota rectangle empty");
        for(int n:new int[]{value.left,value.top,value.right,value.bottom})
            require(n>=(visible?0:-16384) && n<=16384,"Owned quota rectangle outside ready bounds");
    }
    private void write(String suffix,JSONObject value) throws Exception {
        byte[] bytes=value.toString().getBytes(StandardCharsets.UTF_8);
        require(bytes.length>0 && bytes.length<=4096,"Owned quota record too large");
        OriginalSyncStopActions.writeOnce(new File(root,"owned-sync-quota-"+OriginalSyncQuotaActions.PHASE+"-"+suffix),bytes);
    }
    private void facts(String line) {Bundle status=new Bundle();status.putString("stream",line+"\n");sendStatus(1,status);}
}
