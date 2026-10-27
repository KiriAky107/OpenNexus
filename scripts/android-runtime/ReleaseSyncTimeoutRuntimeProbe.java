package cc.kronecker.opennexus.testdriver;

import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Actual failed UI while the host withholds one original already-executed response. */
public final class ReleaseSyncTimeoutRuntimeProbe extends SyncRuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private static final String UNKNOWN="The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.";
    private static final String NETWORK="Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)";
    private OriginalSyncTimeoutActions actions;
    private int originalWindow=-1,originalUid=-1;
    public ReleaseSyncTimeoutRuntimeProbe() {super(true);}
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !OriginalSyncTimeoutActions.allowed(args.getString("phase",""))) {
            Bundle result=new Bundle();result.putString("stream","OPENNEXUS_RELEASE_SYNC_TIMEOUT_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() {return "RELEASE_SYNC_TIMEOUT";}
    @Override protected boolean hostedCycleTouch(String name) {return "Sync one cycle now".equals(name);}
    @Override protected boolean hostedCyclePhaseAllowed(String phase) {return OriginalSyncTimeoutActions.allowed(phase);}
    @Override protected boolean additionalPhase(String phase) throws Exception {
        if(!OriginalSyncTimeoutActions.allowed(phase))return false;
        actions=new OriginalSyncTimeoutActions(phase,UUID.randomUUID().toString(),UUID.randomUUID().toString());
        guardDriver();File release=new File(getContext().getNoBackupFilesDir(),"owned-sync-timeout-released-"+actions.parent+".finish");
        require(!release.exists() && !Files.isSymbolicLink(release.toPath()),"Original timeout release already exists");
        write("owned-sync-timeout-"+phase+"-intent.json",new JSONObject().put("schema",1).put("phase",phase)
            .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input).put("native_invocation_id_observed",false));
        try {
            readCurrent();signedIn();click("Read queue and original records");
            waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
            actions.begin(actions.expectedAction());click("Sync one cycle now");
            if(OriginalSyncTimeoutActions.RESUME.equals(phase)) {
                stage="timeout-original-explicit-read-result";waitPresent(node->label(node,"Cycle completed"));
                guardOwner(originalWindow,null);actions.complete();
                facts("OPENNEXUS_SYNC_AFTER_TIMEOUT_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false");
                return true;
            }
            stage="timeout-original-bounded-failed-state";
            // This independent36second bound starts at original ready, before
            // the host's one input. Input-finish does not reset this deadline.
            actions.observeFailure(()->guardOwner(originalWindow,null),this::failedState,SystemClock::elapsedRealtime,SystemClock::sleep);
            facts("OPENNEXUS_SYNC_TIMEOUT_FAILED_UI "+actions.summary());
            write("owned-sync-timeout-"+phase+"-failed-ready.json",new JSONObject().put("schema",1).put("phase",phase)
                .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input).put("package",APP)
                .put("uid",originalUid).put("window",originalWindow).put("elapsed_ms",SystemClock.elapsedRealtime())
                .put("failed_cycle_UI_observed",true).put("network_error_UI_observed",true).put("native_timeout_verified",false));
            stage="timeout-original-held-until-release";
            actions.awaitRelease(release,this::failedGuard,SystemClock::elapsedRealtime,SystemClock::sleep);
            long until=SystemClock.elapsedRealtime()+600,last=SystemClock.elapsedRealtime();
            while(SystemClock.elapsedRealtime()<until) {
                long now=SystemClock.elapsedRealtime();require(now>=last,"Timeout final clock changed");last=now;
                failedGuard();SystemClock.sleep(50);
            }
            failedGuard();actions.complete();facts("OPENNEXUS_SYNC_TIMEOUT_RELEASE_GUARD "+actions.summary());return true;
        } finally {actions.close();facts("OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY "+actions.summary());}
    }
    @Override protected void awaitHostedCycleTouch(Rect original,Rect visible,int window) {
        try {
            require(actions!=null,"Unscoped original timeout input");guardOwner(window,visible);
            JSONArray full=new JSONArray(),hit=new JSONArray();
            for(int n:new int[]{original.left,original.top,original.right,original.bottom})full.put(n);
            for(int n:new int[]{visible.left,visible.top,visible.right,visible.bottom})hit.put(n);
            long elapsed=SystemClock.elapsedRealtime();actions.ready(elapsed);
            write("owned-sync-timeout-"+actions.phase+"-"+actions.expectedAction()+"-ready.json",new JSONObject().put("schema",1)
                .put("phase",actions.phase).put("action",actions.expectedAction()).put("parent_intent_id",actions.parent)
                .put("driver_intent_id",actions.input).put("package",APP).put("uid",originalUid).put("window",window)
                .put("original",full).put("visible",hit).put("x",visible.centerX()).put("y",visible.centerY()).put("elapsed_ms",elapsed));
            File finish=new File(getContext().getNoBackupFilesDir(),"owned-sync-timeout-touch-"+actions.input+".finish");
            OriginalSyncTimeoutActions.awaitSignal(finish,()->guardOwner(window,null),SystemClock::elapsedRealtime,SystemClock::sleep,OriginalSyncTimeoutActions.INPUT_WAIT_MS);
            actions.finished(actions.expectedAction());
        } catch(Exception error) {throw new IllegalStateException("OWNED_SYNC_TIMEOUT_INPUT_NOT_FINISHED");}
    }
    private void guardDriver() {
        require(DRIVER.equals(getContext().getPackageName()) && DRIVER.equals(getTargetContext().getPackageName()),"Timeout instrumentation target changed");
    }
    private void guardOwner(int window,Rect region) throws Exception {
        actions.beforeProductUi();guardDriver();require(APP.equals("cc.kronecker.opennexus"),"Timeout APP identity changed");
        android.app.UiAutomation ui=getUiAutomation();if(android.os.Build.VERSION.SDK_INT>=34)ui.clearCache();
        AccessibilityNodeInfo root=ui.getRootInActiveWindow();
        require(root!=null && APP.equals(text(root.getPackageName())) && !root.isPassword() && root.getWindowId()==window,"Timeout original window unavailable");
        int uid=getContext().getPackageManager().getApplicationInfo(APP,0).uid;
        require(uid>=10000 && uid<=99999 && (originalUid<0 || originalUid==uid) && (originalWindow<0 || originalWindow==window),"Timeout original owner changed");
        List<AccessibilityWindowInfo> windows=ui.getWindows();AccessibilityWindowInfo own=null;int count=0;
        for(AccessibilityWindowInfo candidate:windows) {
            require(candidate.getType()!=AccessibilityWindowInfo.TYPE_INPUT_METHOD,"Timeout keyboard visible");
            if(candidate.getId()==window) {own=candidate;count++;}
        }
        require(count==1 && own!=null && own.isActive() && own.isFocused() && own.getType()==AccessibilityWindowInfo.TYPE_APPLICATION
            && own.getDisplayId()==0,"Timeout window identity changed");
        if(region!=null)for(AccessibilityWindowInfo other:windows)
            if(other.getId()!=window && other.getDisplayId()==0 && other.getLayer()>=own.getLayer()) {
                Rect bounds=new Rect();other.getBoundsInScreen(bounds);require(!Rect.intersects(bounds,region),"Timeout input obstructed");
            }
        originalUid=uid;originalWindow=window;
    }
    private boolean failedState() throws Exception {
        int failure=0,disabled=0,unknown=0,network=0,stop=0,complete=0,stopped=0,enabledCycle=0;
        for(AccessibilityNodeInfo node:nodes()) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=originalWindow || node.isPassword() || !node.isVisibleToUser())continue;
            if(label(node,"Cycle did not complete"))failure++;
            if(!node.isEditable() && text(node.getText()).equals(UNKNOWN))unknown++;
            if(!node.isEditable() && text(node.getText()).equals(NETWORK))network++;
            if(label(node,"Sync one cycle now") && !node.isEnabled()) {Rect bounds=new Rect();node.getBoundsInScreen(bounds);guardOwner(originalWindow,bounds);disabled++;}
            if(label(node,"Sync one cycle now") && node.isEnabled())enabledCycle++;
            if(label(node,"Stop this cycle") && node.isEnabled())stop++;
            if(label(node,"Cycle completed"))complete++;
            if(label(node,"Cycle stopped"))stopped++;
        }
        return OriginalSyncTimeoutActions.failedScene(failure,disabled,unknown,network,stop,complete,stopped,enabledCycle);
    }
    private void failedGuard() throws Exception {guardOwner(originalWindow,null);require(failedState(),"Original timeout failed UI changed");}
    private void write(String name,JSONObject value) throws Exception {
        OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),name),value.toString().getBytes(StandardCharsets.UTF_8));
    }
    private void facts(String value) {Bundle result=new Bundle();result.putString("stream",value+"\n");sendStatus(1,result);}
}
