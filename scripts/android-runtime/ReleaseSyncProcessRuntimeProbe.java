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

/** The host owns process termination; this self-target driver never kills or reopens the product. */
public final class ReleaseSyncProcessRuntimeProbe extends SyncRuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private OriginalSyncProcessActions actions;
    private int originalWindow=-1,originalUid=-1;
    public ReleaseSyncProcessRuntimeProbe() {super(true);}
    @Override public void onCreate(Bundle args) {
        if(args==null || args.size()!=1 || !OriginalSyncProcessActions.allowed(args.getString("phase",""))) {
            Bundle result=new Bundle();result.putString("stream","OPENNEXUS_RELEASE_SYNC_PROCESS_SCOPE_REFUSED no_product_input=1\n");
            finish(1,result);return;
        }
        super.onCreate(args);
    }
    @Override protected String namespace() {return "RELEASE_SYNC_PROCESS";}
    @Override protected boolean hostedCycleTouch(String name) {return "Sync one cycle now".equals(name);}
    @Override protected boolean hostedCyclePhaseAllowed(String phase) {return OriginalSyncProcessActions.allowed(phase);}
    @Override protected boolean productUiObservationClosed() {return actions!=null && actions.productUiClosed();}
    @Override protected List<AccessibilityNodeInfo> nodes() {
        require(!productUiObservationClosed(),"Process product UI observation closed");return super.nodes();
    }
    @Override protected boolean additionalPhase(String phase) throws Exception {
        if(!OriginalSyncProcessActions.allowed(phase))return false;
        actions=new OriginalSyncProcessActions(phase,UUID.randomUUID().toString(),UUID.randomUUID().toString());
        guardDriver();
        File expected=signal("expect-loss"),release=signal("released");
        require(!expected.exists() && !Files.isSymbolicLink(expected.toPath())
            && !release.exists() && !Files.isSymbolicLink(release.toPath()),"Original process signal already exists");
        write("owned-sync-process-"+phase+"-intent.json",new JSONObject().put("schema",1).put("phase",phase)
            .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input).put("native_invocation_id_observed",false));
        try {
            readCurrent();signedIn();click("Read queue and original records");
            waitFor(node->label(node,"Sync one cycle now") && node.isEnabled(),true);
            actions.begin(actions.expectedAction());click("Sync one cycle now");
            if(OriginalSyncProcessActions.RESUME.equals(phase)) {
                stage="process-original-explicit-read-result";
                waitPresent(node->label(node,"Cycle completed"));
                guardOwner(originalWindow,null);actions.complete();
                facts("OPENNEXUS_SYNC_AFTER_PROCESS_LOSS_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false");
                return true;
            }
            // The host withholds the input finish until the original held
            // service200 marker. A second same-parent signal explicitly opens
            // the expected-loss boundary while this original owner is live.
            stage="process-original-await-expected-loss";
            OriginalSyncStopActions.await(expected,()->guardOwner(originalWindow,null),SystemClock::uptimeMillis,SystemClock::sleep,5000);
            actions.authorized();guardOwner(originalWindow,null);
            actions.closeProductUi();
            write("owned-sync-process-"+phase+"-loss-ready.json",new JSONObject().put("schema",1).put("phase",phase)
                .put("parent_intent_id",actions.parent).put("input_intent_id",actions.input).put("package",APP)
                .put("uid",originalUid).put("window",originalWindow).put("elapsed_ms",SystemClock.elapsedRealtime())
                .put("product_UI_observation_closed",true).put("process_absence_verified",false));
            facts("OPENNEXUS_SYNC_PROCESS_EXPECTED_LOSS_BOUNDARY "+actions.summary());
            // No product AX, window, credential cleanup or diagnostic traversal
            // is allowed beyond this point, including inherited failure paths.
            stage="process-original-await-host-release";
            OriginalSyncStopActions.await(release,this::guardDriver,SystemClock::uptimeMillis,SystemClock::sleep,10000);
            actions.released();guardDriver();actions.complete();
            facts("OPENNEXUS_SYNC_PROCESS_HOST_RELEASE_SIGNAL "+actions.summary());return true;
        } finally {
            actions.close();facts("OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY "+actions.summary());
        }
    }
    @Override protected void awaitHostedCycleTouch(Rect original,Rect visible,int window) {
        try {
            require(actions!=null,"Unscoped original process input");guardOwner(window,visible);
            JSONArray full=new JSONArray(),hit=new JSONArray();
            for(int n:new int[]{original.left,original.top,original.right,original.bottom})full.put(n);
            for(int n:new int[]{visible.left,visible.top,visible.right,visible.bottom})hit.put(n);
            write("owned-sync-process-"+actions.phase+"-"+actions.expectedAction()+"-ready.json",new JSONObject().put("schema",1)
                .put("phase",actions.phase).put("action",actions.expectedAction()).put("parent_intent_id",actions.parent)
                .put("driver_intent_id",actions.input).put("package",APP).put("uid",originalUid).put("window",window)
                .put("original",full).put("visible",hit).put("x",visible.centerX()).put("y",visible.centerY())
                .put("elapsed_ms",SystemClock.elapsedRealtime()));
            File finish=new File(getContext().getNoBackupFilesDir(),"owned-sync-process-touch-"+actions.input+".finish");
            OriginalSyncStopActions.await(finish,()->guardOwner(window,null),SystemClock::uptimeMillis,SystemClock::sleep,30000);
            actions.finished(actions.expectedAction());
        } catch(Exception error) {throw new IllegalStateException("OWNED_SYNC_PROCESS_INPUT_NOT_FINISHED");}
    }
    private void guardDriver() {
        require(DRIVER.equals(getContext().getPackageName()) && DRIVER.equals(getTargetContext().getPackageName()),
            "Process instrumentation target changed");
    }
    private void guardOwner(int window,Rect region) throws Exception {
        actions.beforeProductUi();guardDriver();
        require(APP.equals("cc.kronecker.opennexus"),"Process APP identity changed");
        android.app.UiAutomation ui=getUiAutomation();
        if(android.os.Build.VERSION.SDK_INT>=34)ui.clearCache();
        AccessibilityNodeInfo root=ui.getRootInActiveWindow();
        require(root!=null && APP.equals(text(root.getPackageName())) && !root.isPassword() && root.getWindowId()==window,
            "Process original window unavailable");
        int uid=getContext().getPackageManager().getApplicationInfo(APP,0).uid;
        require(uid>=10000 && uid<=99999 && (originalUid<0 || originalUid==uid) && (originalWindow<0 || originalWindow==window),
            "Process original owner changed");
        List<AccessibilityWindowInfo> windows=ui.getWindows();AccessibilityWindowInfo own=null;int count=0;
        for(AccessibilityWindowInfo candidate:windows) {
            require(candidate.getType()!=AccessibilityWindowInfo.TYPE_INPUT_METHOD,"Process keyboard visible");
            if(candidate.getId()==window) {own=candidate;count++;}
        }
        require(count==1 && own!=null && own.isActive() && own.isFocused()
            && own.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && own.getDisplayId()==0,"Process window identity changed");
        if(region!=null)for(AccessibilityWindowInfo other:windows)
            if(other.getId()!=window && other.getDisplayId()==0 && other.getLayer()>=own.getLayer()) {
                Rect bounds=new Rect();other.getBoundsInScreen(bounds);
                require(!Rect.intersects(bounds,region),"Process input obstructed");
            }
        originalUid=uid;originalWindow=window;
    }
    private File signal(String kind) {
        return new File(getContext().getNoBackupFilesDir(),"owned-sync-process-"+kind+"-"+actions.parent+".finish");
    }
    private void write(String name,JSONObject value) throws Exception {
        OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),name),value.toString().getBytes(StandardCharsets.UTF_8));
    }
    private void facts(String value) {Bundle result=new Bundle();result.putString("stream",value+"\n");sendStatus(1,result);}
}
