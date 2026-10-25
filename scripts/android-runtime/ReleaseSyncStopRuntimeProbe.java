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

/** Only the original Start/Stop GUI pair or one explicit subsequent read cycle. */
public final class ReleaseSyncStopRuntimeProbe extends SyncRuntimeProbe {
    private static final String UNKNOWN="The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.";
    private OriginalSyncStopActions actions;
    private String action,nonce;
    private int originalWindow=-1,originalUid=-1;
    public ReleaseSyncStopRuntimeProbe() {super(true);}
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !OriginalSyncStopActions.allowed(args.getString("phase",""))) {
            Bundle result=new Bundle();result.putString("stream","OPENNEXUS_RELEASE_SYNC_STOP_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() {return "RELEASE_SYNC_STOP";}
    @Override protected boolean hostedCycleTouch(String name) {
        return "Sync one cycle now".equals(name) || "Stop this cycle".equals(name);
    }
    @Override protected boolean hostedCyclePhaseAllowed(String phase) {
        return OriginalSyncStopActions.RESUME.equals(phase);
    }
    @Override protected boolean additionalPhase(String phase) throws Exception {
        if(!OriginalSyncStopActions.allowed(phase))return false;
        actions=new OriginalSyncStopActions(phase,UUID.randomUUID().toString(),UUID.randomUUID().toString(),
            OriginalSyncStopActions.STOP.equals(phase)?UUID.randomUUID().toString():null);
        write("owned-sync-stop-"+phase+"-intent.json",new JSONObject().put("schema",1).put("phase",phase)
            .put("parent_intent_id",actions.parent).put("first_intent_id",actions.first)
            .put("second_intent_id",actions.second==null?JSONObject.NULL:actions.second).put("native_invocation_id_observed",false));
        try {
            if(OriginalSyncStopActions.RESUME.equals(phase)) {
                readCurrent();signedIn();click("Read queue and original records");
                waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
                action="cycle-resume";nonce=actions.begin(action);
                click("Sync one cycle now");
                stage="stop-original-explicit-read-result";
                waitPresent(node->label(node,"Cycle completed"));
                guardOwner(originalWindow,null);actions.complete();
                facts("OPENNEXUS_SYNC_AFTER_STOP_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false");
                return true;
            }
            readCurrent();signedIn();click("Read queue and original records");
            waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
            action="cycle-start";nonce=actions.begin(action);click("Sync one cycle now");
            waitFor(node->label(node,"Stop this cycle") && node.isEnabled(),false);
            action="cycle-stop";nonce=actions.begin(action);click("Stop this cycle");
            stage="stop-original-result";
            waitPresent(node->label(node,"Cycle stopped"));
            waitPresent(node->text(node.getText()).equals(UNKNOWN));
            waitFor(node->label(node,"Sync one cycle now") && !node.isEnabled(),false);
            stoppedGuard();actions.stopped();
            facts("OPENNEXUS_SYNC_STOPPED_UI "+actions.summary());
            // Host releases only after the actual original Stop result. The
            // same owning instrumentation remains here through that release.
            stage="stop-original-held-until-release";
            File release=new File(getContext().getNoBackupFilesDir(),"owned-sync-stop-release-"+actions.parent+".finish");
            OriginalSyncStopActions.await(release,this::stoppedGuard,SystemClock::uptimeMillis,SystemClock::sleep,30000);
            actions.released();
            long until=SystemClock.uptimeMillis()+600;
            while(SystemClock.uptimeMillis()<until) {stoppedGuard();SystemClock.sleep(50);}
            stoppedGuard();actions.complete();
            facts("OPENNEXUS_SYNC_STOP_RELEASE_GUARD "+actions.summary());return true;
        } finally {
            actions.close();
            facts("OPENNEXUS_SYNC_STOP_INPUT_SUMMARY "+actions.summary());
        }
    }
    @Override protected void awaitHostedCycleTouch(Rect original,Rect visible,int window) {
        try {
            require(actions!=null && OriginalSyncStopActions.canonical(nonce),"Unscoped original Stop input");
            require(("cycle-start".equals(action) || "cycle-resume".equals(action)) && nonce.equals(actions.first)
                || "cycle-stop".equals(action) && nonce.equals(actions.second),"Another original Stop action");
            guardOwner(window,visible);
            JSONArray full=new JSONArray(),hit=new JSONArray();
            for(int n:new int[]{original.left,original.top,original.right,original.bottom})full.put(n);
            for(int n:new int[]{visible.left,visible.top,visible.right,visible.bottom})hit.put(n);
            write("owned-sync-stop-"+actions.phase+"-"+action+"-ready.json",new JSONObject().put("schema",1)
                .put("phase",actions.phase).put("action",action).put("parent_intent_id",actions.parent).put("driver_intent_id",nonce)
                .put("package",APP).put("uid",originalUid).put("window",window).put("original",full).put("visible",hit)
                .put("x",visible.centerX()).put("y",visible.centerY()).put("elapsed_ms",SystemClock.elapsedRealtime()));
            File finish=new File(getContext().getNoBackupFilesDir(),"owned-sync-stop-touch-"+nonce+".finish");
            // Layout may legitimately change after the actual input and before
            // finish. Keep owner/window/IME guards, not the old button state.
            OriginalSyncStopActions.await(finish,()->guardOwner(window,null),SystemClock::uptimeMillis,SystemClock::sleep,30000);
            actions.finished(action);
        } catch(Exception error) {
            throw new IllegalStateException("OWNED_SYNC_STOP_INPUT_NOT_FINISHED");
        }
    }
    private void guardOwner(int window,Rect region) throws Exception {
        require(APP.equals("cc.kronecker.opennexus"),"Stop APP identity changed");
        android.app.UiAutomation ui=getUiAutomation();
        if(android.os.Build.VERSION.SDK_INT>=34)ui.clearCache();
        AccessibilityNodeInfo root=ui.getRootInActiveWindow();
        require(root!=null && APP.equals(text(root.getPackageName())) && !root.isPassword() && root.getWindowId()==window,
            "Stop original window unavailable");
        int uid=getContext().getPackageManager().getApplicationInfo(APP,0).uid;
        require(uid>0 && (originalUid<0 || originalUid==uid) && (originalWindow<0 || originalWindow==window),
            "Stop original owner changed");
        List<AccessibilityWindowInfo> windows=ui.getWindows();AccessibilityWindowInfo own=null;int count=0;
        for(AccessibilityWindowInfo candidate:windows) {
            require(candidate.getType()!=AccessibilityWindowInfo.TYPE_INPUT_METHOD,"Stop keyboard visible");
            if(candidate.getId()==window) {own=candidate;count++;}
        }
        require(count==1 && own!=null && own.isActive() && own.isFocused()
            && own.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && own.getDisplayId()==0,"Stop window identity changed");
        if(region!=null) for(AccessibilityWindowInfo other:windows)
            if(other.getId()!=window && other.getDisplayId()==0 && other.getLayer()>=own.getLayer()) {
                Rect bounds=new Rect();other.getBoundsInScreen(bounds);
                require(!Rect.intersects(bounds,region),"Stop input obstructed");
            }
        originalUid=uid;originalWindow=window;
    }
    private void stoppedGuard() throws Exception {
        guardOwner(originalWindow,null);int stopped=0,disabled=0,enabledStop=0;boolean unknown=false;
        for(AccessibilityNodeInfo node:nodes()) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=originalWindow || node.isPassword())continue;
            if(node.isVisibleToUser() && label(node,"Cycle stopped"))stopped++;
            if(node.isVisibleToUser() && text(node.getText()).equals(UNKNOWN))unknown=true;
            if(node.isVisibleToUser() && label(node,"Sync one cycle now") && !node.isEnabled()) {
                Rect bounds=new Rect();node.getBoundsInScreen(bounds);guardOwner(originalWindow,bounds);disabled++;
            }
            if(node.isVisibleToUser() && label(node,"Stop this cycle") && node.isEnabled())enabledStop++;
        }
        require(stopped==1 && disabled==1 && unknown && enabledStop==0,"Original stopped GUI changed");
    }
    private void write(String name,JSONObject value) throws Exception {
        OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),name),value.toString().getBytes(StandardCharsets.UTF_8));
    }
    private void facts(String value) {
        Bundle result=new Bundle();result.putString("stream",value+"\n");sendStatus(1,result);
    }
}
