package cc.kronecker.opennexus.testdriver;

import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Actual failed UI while the host withholds one original already-executed response. */
public final class ReleaseSyncTimeoutRuntimeProbe extends SyncRuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private static final String UNKNOWN="The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.";
    private static final String NETWORK="Last recorded sync error · Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)";
    private static final String DETAIL_FOOTER="A successful cycle does not mean the queue is empty. Receiving download bytes does not mean verification succeeded; review conflicts separately.";
    private OriginalSyncTimeoutActions actions;
    private int originalWindow=-1,originalUid=-1;
    private int[][] lastAlerts;
    private long lastAlertAt=-1;
    private OriginalSyncTimeoutPreinputFlow preinput;
    private OriginalSyncTimeoutPreinputFlow.Frame lastPreinputFrame;
    private AccessibilityNodeInfo lastPreinputSection;
    private boolean preinputClosed,preinputRevealInvoked;
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
            if(OriginalSyncTimeoutActions.TIMEOUT.equals(phase))prepareCurrentPublicRegion();
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
        } catch(Exception error) {
            // Retain only closed facts already read by the original observer.
            // Do not inspect UI again, record exception text, or change result.
            if(OriginalSyncTimeoutActions.TIMEOUT.equals(phase))try {failureObservation(error);}catch(Exception ignored) {}
            throw error;
        } finally {actions.close();facts("OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY "+actions.summary());}
    }
    @Override protected void awaitHostedCycleTouch(Rect original,Rect visible,int window) {
        try {
            require(actions!=null,"Unscoped original timeout input");guardOwner(window,visible);
            if(OriginalSyncTimeoutActions.TIMEOUT.equals(actions.phase)) {
                require(preinput!=null && !preinputClosed,"Timeout preinput preparation unavailable");
                preinput.finalCheck(rectangle(original),rectangle(visible),window);preinputClosed=true;
            }
            JSONArray full=new JSONArray(),hit=new JSONArray();
            for(int n:new int[]{original.left,original.top,original.right,original.bottom})full.put(n);
            for(int n:new int[]{visible.left,visible.top,visible.right,visible.bottom})hit.put(n);
            long elapsed=SystemClock.elapsedRealtime();actions.ready(elapsed);
            JSONObject ready=new JSONObject().put("schema",1)
                .put("phase",actions.phase).put("action",actions.expectedAction()).put("parent_intent_id",actions.parent)
                .put("driver_intent_id",actions.input).put("package",APP).put("uid",originalUid).put("window",window)
                .put("original",full).put("visible",hit).put("x",visible.centerX()).put("y",visible.centerY()).put("elapsed_ms",elapsed);
            String readyName="owned-sync-timeout-"+actions.phase+"-"+actions.expectedAction()+"-ready.json";
            if(OriginalSyncTimeoutActions.TIMEOUT.equals(actions.phase))
                OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),readyName),
                    preinput.readyBytes(originalUid,window,rectangle(original),rectangle(visible),visible.centerX(),visible.centerY(),elapsed));
            else write(readyName,ready);
            File finish=new File(getContext().getNoBackupFilesDir(),"owned-sync-timeout-touch-"+actions.input+".finish");
            OriginalSyncTimeoutActions.awaitSignal(finish,()->guardOwner(window,null),SystemClock::elapsedRealtime,SystemClock::sleep,OriginalSyncTimeoutActions.INPUT_WAIT_MS);
            actions.finished(actions.expectedAction());
        } catch(Exception error) {throw new IllegalStateException("OWNED_SYNC_TIMEOUT_INPUT_NOT_FINISHED");}
    }
    private void guardDriver() {
        require(DRIVER.equals(getContext().getPackageName()) && DRIVER.equals(getTargetContext().getPackageName()),"Timeout instrumentation target changed");
    }
    private void prepareCurrentPublicRegion() throws Exception {
        stage="timeout-preinput-current-public-region";
        File root=getContext().getNoBackupFilesDir();
        for(String suffix:new String[]{"viewport-intent.json","viewport-attempt.json","cycle-start-ready.json"}) {
            File file=new File(root,"owned-sync-timeout-"+actions.phase+"-"+suffix);
            require(!file.exists() && !Files.isSymbolicLink(file.toPath()),"Timeout preinput original already exists");
        }
        preinput=OriginalSyncTimeoutPreinputFlow.prepare(actions.parent,actions.input,new OriginalSyncTimeoutPreinputFlow.Port() {
            public OriginalSyncTimeoutPreinputFlow.Frame read() throws Exception {return preinputFrame();}
            public long uptime() {require(!preinputClosed,"Timeout preinput clock closed");return SystemClock.uptimeMillis();}
            public void pause(long millis) {require(!preinputClosed && millis==50,"Timeout preinput pause refused");SystemClock.sleep(millis);}
            public void write(OriginalSyncTimeoutPreinputFlow.Record record,byte[] bytes) throws Exception {
                require(!preinputClosed && bytes!=null && bytes.length>0 && bytes.length<=4096 && record!=null,"Timeout preinput write refused");
                String suffix=record==OriginalSyncTimeoutPreinputFlow.Record.INTENT?"viewport-intent.json":"viewport-attempt.json";
                OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),"owned-sync-timeout-"+actions.phase+"-"+suffix),bytes);
            }
            public boolean revealSection(long previous,long deadline) throws Exception {return revealCurrentSection(previous,deadline);}
        });
    }
    private OriginalSyncTimeoutPreinputFlow.Frame preinputFrame() throws Exception {
        require(!preinputClosed && actions!=null && OriginalSyncTimeoutActions.TIMEOUT.equals(actions.phase),"Timeout preinput reads closed");
        android.app.UiAutomation current=getUiAutomation();AccessibilityNodeInfo root=current.getRootInActiveWindow();
        require(root!=null && APP.equals(text(root.getPackageName())) && !root.isPassword(),"Timeout preinput root unavailable");
        int window=root.getWindowId();guardOwner(window,null);
        List<AccessibilityNodeInfo> all=nodes(),buttons=new ArrayList<>(),sections=new ArrayList<>(),footers=new ArrayList<>();
        require(all.size()<2500,"Timeout preinput tree incomplete");
        for(AccessibilityNodeInfo node:all) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=window || node.isPassword() || node.isEditable())continue;
            String value=text(node.getText()),description=text(node.getContentDescription());
            if(preinputLabel(value,description,"Sync one cycle now") && node.isEnabled()) {
                AccessibilityNodeInfo target=node.isClickable()?node:node.getParent();
                if(target!=null && APP.equals(text(target.getPackageName())) && target.getWindowId()==window
                        && !target.isPassword() && !target.isEditable() && target.isEnabled() && target.isClickable()
                        && !buttons.contains(target))buttons.add(target);
            }
            if(preinputLabel(value,description,"Sync details") && !sections.contains(node))sections.add(node);
            if(preinputLabel(value,description,DETAIL_FOOTER))footers.add(node);
        }
        require(buttons.size()==1,"Timeout preinput original button ambiguous");
        List<AccessibilityNodeInfo> containers=new ArrayList<>();
        for(AccessibilityNodeInfo section:sections)for(AccessibilityNodeInfo footer:footers)
            if(ownedAncestor(footer,section,window) && contains(rectangle(section),rectangle(footer)) && !containers.contains(section))containers.add(section);
        require(containers.size()==1,"Timeout preinput complete section unavailable");
        AccessibilityNodeInfo button=buttons.get(0),section=containers.get(0);
        require(button.isVisibleToUser(),"Timeout preinput original button hidden");
        List<AccessibilityWindowInfo> windows=current.getWindows();AccessibilityWindowInfo own=null;int count=0;
        for(AccessibilityWindowInfo candidate:windows) {
            require(candidate.getType()!=AccessibilityWindowInfo.TYPE_INPUT_METHOD,"Timeout preinput keyboard visible");
            if(candidate.getId()==window) {own=candidate;count++;}
        }
        require(count==1 && own!=null && own.isActive() && own.isFocused()
            && own.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && own.getDisplayId()==0,"Timeout preinput window changed");
        Rect owned=new Rect();own.getBoundsInScreen(owned);int[] viewport=bounded(owned);
        int[] b=rectangle(button),s=rectangle(section),bc=preinputClip(button,viewport,windows,own),sc=preinputClip(section,viewport,windows,own);
        require(bc!=null && sc!=null && (section.isVisibleToUser() || !contains(sc,s)),"Timeout preinput visibility unknown");
        lastPreinputSection=section;
        lastPreinputFrame=new OriginalSyncTimeoutPreinputFlow.Frame(originalUid,window,viewport,b,bc,s,sc);
        return lastPreinputFrame;
    }
    private boolean revealCurrentSection(long previous,long deadline) throws Exception {
        require(!preinputClosed && !preinputRevealInvoked && lastPreinputFrame!=null,"Timeout preinput reveal reentry");
        preinputRevealInvoked=true;OriginalSyncTimeoutPreinputFlow.Frame expected=lastPreinputFrame;
        OriginalSyncTimeoutPreinputFlow.Frame fresh=preinputFrame();
        require(expected.same(fresh),"Timeout preinput section moved before reveal");
        long now=SystemClock.uptimeMillis();
        require(previous>=0 && previous<=now && now<=deadline && deadline-previous<=5000,"Timeout preinput reveal late");
        return lastPreinputSection.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId());
    }
    private static boolean preinputLabel(String value,String description,String label) {
        return (label.equals(value) || label.equals(description))
            && (value.isEmpty() || label.equals(value)) && (description.isEmpty() || label.equals(description));
    }
    private boolean ownedAncestor(AccessibilityNodeInfo child,AccessibilityNodeInfo ancestor,int window) {
        int depth=0;
        for(AccessibilityNodeInfo node=child.getParent();node!=null && depth++<32;node=node.getParent()) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=window || node.isPassword() || node.isEditable())return false;
            if(node.equals(ancestor))return true;
        }
        return false;
    }
    private int[] preinputClip(AccessibilityNodeInfo node,int[] viewport,List<AccessibilityWindowInfo> windows,AccessibilityWindowInfo own) {
        int[] clip=viewport.clone();int depth=0;
        for(AccessibilityNodeInfo parent=node.getParent();parent!=null;parent=parent.getParent()) {
            require(depth++<32 && APP.equals(text(parent.getPackageName())) && parent.getWindowId()==own.getId()
                && !parent.isPassword() && !parent.isEditable(),"Timeout preinput clip lineage changed");
            if(parent.isScrollable() || "android.webkit.WebView".equals(text(parent.getClassName())))clip=TouchViewport.intersect(clip,rectangle(parent));
            if(clip==null)return null;
        }
        for(AccessibilityWindowInfo other:windows)if(other.getId()!=own.getId() && other.getDisplayId()==0 && other.getLayer()>=own.getLayer()) {
            Rect covered=new Rect();other.getBoundsInScreen(covered);clip=TouchViewport.avoid(clip,bounded(covered));if(clip==null)return null;
        }
        return clip;
    }
    private static int[] rectangle(AccessibilityNodeInfo node) {Rect value=new Rect();node.getBoundsInScreen(value);return bounded(value);}
    private static int[] rectangle(Rect value) {return new int[]{value.left,value.top,value.right,value.bottom};}
    private static int[] bounded(Rect value) {
        int[] rect=rectangle(value);for(int n:rect)require(n>=0 && n<=16384,"Timeout preinput geometry outside ready bounds");
        require(rect[0]<rect[2] && rect[1]<rect[3],"Timeout preinput geometry empty");return rect;
    }
    private static boolean contains(int[] outer,int[] inner) {
        return outer[0]<=inner[0] && outer[1]<=inner[1] && inner[2]<=outer[2] && inner[3]<=outer[3];
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
        ReleaseSyncTimeoutAlerts.Tally alerts=new ReleaseSyncTimeoutAlerts.Tally();
        for(AccessibilityNodeInfo node:nodes()) {
            if(!APP.equals(text(node.getPackageName())) || node.getWindowId()!=originalWindow || node.isPassword() || !node.isVisibleToUser())continue;
            if(label(node,"Cycle did not complete"))failure++;
            if(!node.isEditable() && text(node.getText()).equals(UNKNOWN))unknown++;
            if(networkAlertNode(node,alerts))network++;
            if(label(node,"Sync one cycle now") && !node.isEnabled()) {Rect bounds=new Rect();node.getBoundsInScreen(bounds);guardOwner(originalWindow,bounds);disabled++;}
            if(label(node,"Sync one cycle now") && node.isEnabled())enabledCycle++;
            if(label(node,"Stop this cycle") && node.isEnabled())stop++;
            if(label(node,"Cycle completed"))complete++;
            if(label(node,"Cycle stopped"))stopped++;
        }
        long sampled=SystemClock.elapsedRealtime();
        actions.recordScene(new int[]{failure,disabled,unknown,network,stop,complete,stopped,enabledCycle},sampled);
        lastAlerts=alerts.snapshot();lastAlertAt=sampled;
        return OriginalSyncTimeoutActions.failedScene(failure,disabled,unknown,network,stop,complete,stopped,enabledCycle);
    }
    private void failedGuard() throws Exception {guardOwner(originalWindow,null);require(failedState(),"Original timeout failed UI changed");}
    static boolean networkAlert(String text) {return NETWORK.equals(text);}
    static boolean networkAlertNode(AccessibilityNodeInfo node) {
        return networkAlertNode(node,null);
    }
    static boolean networkAlertNode(AccessibilityNodeInfo node,ReleaseSyncTimeoutAlerts.Tally alerts) {
        if(node.isEditable())return false;
        String value=text(node.getText()),description=text(node.getContentDescription());
        if(alerts!=null)alerts.record(value,description);
        // An occupied label channel must be the complete fixed product warning.
        // The surrounding scene still checks the original owner and visibility.
        return (networkAlert(value) || networkAlert(description))
            && (value.isEmpty() || networkAlert(value)) && (description.isEmpty() || networkAlert(description));
    }
    private void failureObservation(Exception error) throws Exception {
        int[] scene=actions.lastScene();JSONArray counts=new JSONArray();
        if(scene!=null)for(int count:scene)counts.put(count);
        String point="timeout-original-bounded-failed-state".equals(stage)?"waiting_failed_scene":
            "timeout-original-held-until-release".equals(stage)?"waiting_release":"other";
        JSONObject alertFacts=null;
        if(scene!=null && lastAlerts!=null && lastAlertAt==actions.lastSceneAt()) {
            JSONArray texts=new JSONArray(),descriptions=new JSONArray(),strict=new JSONArray();
            JSONArray bodyTexts=new JSONArray(),bodyDescriptions=new JSONArray(),headers=new JSONArray(),details=new JSONArray();
            JSONArray nonexactTexts=new JSONArray(),nonexactDescriptions=new JSONArray();
            for(int n:lastAlerts[0])texts.put(n);
            for(int n:lastAlerts[1])descriptions.put(n);
            for(int n:lastAlerts[2])strict.put(n);
            for(int n:lastAlerts[4])bodyTexts.put(n);
            for(int n:lastAlerts[5])bodyDescriptions.put(n);
            for(int n:lastAlerts[6])headers.put(n);
            for(int n:lastAlerts[7])details.put(n);
            for(int n:lastAlerts[8])nonexactTexts.put(n);
            for(int n:lastAlerts[9])nonexactDescriptions.put(n);
            alertFacts=new JSONObject().put("schema",3).put("catalog_sha256",ReleaseSyncTimeoutAlerts.CATALOG_SHA256)
                .put("scan_elapsed_ms",lastAlertAt).put("text_counts",texts).put("description_counts",descriptions)
                .put("strict_counts",strict).put("known_channel_conflicts",lastAlerts[3][0])
                .put("fragment_catalog_sha256",ReleaseSyncTimeoutAlerts.FRAGMENT_CATALOG_SHA256)
                .put("fragment_body_text_counts",bodyTexts).put("fragment_body_description_counts",bodyDescriptions)
                .put("fragment_header_counts",headers).put("fragment_detail_counts",details)
                .put("unassociated_source_fragments_only",true).put("complete_alert_inferred",false)
                .put("whitespace_rule_id",ReleaseSyncTimeoutAlertWhitespace.RULE_ID)
                .put("whitespace_nonexact_text_counts",nonexactTexts).put("whitespace_nonexact_description_counts",nonexactDescriptions)
                .put("whitespace_complete_candidates_only",true).put("whitespace_is_not_admission",true)
                .put("raw_UI_recorded",false).put("native_timeout_verified",false);
        }
        OriginalSyncTimeoutWaitFacts wait=actions.lastWaitFailure();JSONObject waitFacts=null;
        if(wait!=null)waitFacts=new JSONObject().put("schema",1).put("point",wait.point.name())
            .put("ready_elapsed_ms",wait.readyElapsedMs).put("previous_elapsed_ms",wait.previousElapsedMs)
            .put("sampled_elapsed_ms",wait.sampledElapsedMs).put("deadline_elapsed_ms",wait.deadlineElapsedMs)
            .put("state_observed",wait.stateObserved==null?JSONObject.NULL:wait.stateObserved)
            .put("original_guard_rejected",true).put("diagnostic_only",true).put("native_timeout_verified",false);
        write("owned-sync-timeout-"+actions.phase+"-failure-observation.json",new JSONObject().put("schema",5)
            .put("phase",actions.phase).put("parent_intent_id",actions.parent).put("input_intent_id",actions.input)
            .put("package",APP).put("uid",originalUid).put("window",originalWindow).put("phase_stage",point)
            .put("original_ready_elapsed_ms",actions.originalReadyAt()).put("input_finished",actions.originalInputFinished())
            .put("last_scene_scan_finished",scene!=null).put("label_counts",scene==null?JSONObject.NULL:counts)
            .put("observed_elapsed_ms",scene==null?JSONObject.NULL:actions.lastSceneAt()).put("count_saturated_at",2)
            .put("alert_diagnostics",alertFacts==null?JSONObject.NULL:alertFacts)
            .put("wait_guard_failure",waitFacts==null?JSONObject.NULL:waitFacts)
            .put("driver_error_kind",error instanceof IllegalStateException && "OWNED_SYNC_TIMEOUT_ORIGINAL_SEQUENCE".equals(error.getMessage())?"sequence_guard":"unclassified")
            .put("failure_only",true).put("native_timeout_verified",false).put("raw_UI_recorded",false));
    }
    private void write(String name,JSONObject value) throws Exception {
        OriginalSyncStopActions.writeOnce(new File(getContext().getNoBackupFilesDir(),name),value.toString().getBytes(StandardCharsets.UTF_8));
    }
    private void facts(String value) {Bundle result=new Bundle();result.putString("stream",value+"\n");sendStatus(1,result);}
}
