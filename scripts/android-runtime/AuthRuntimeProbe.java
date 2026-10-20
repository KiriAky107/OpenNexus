package cc.kronecker.opennexus.testdriver;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import org.json.JSONArray;
import org.json.JSONObject;

/** Actual installed-app UI only. No password arguments, raw UI dump or screenshot. */
public class AuthRuntimeProbe extends Instrumentation {
    protected final String APP;
    private Bundle args;
    private UiAutomation ui;
    protected String stage = "initialization";
    private String inputTrace="none";
    private String inputTarget="none";
    private final List<String> inputHistory=new ArrayList<>();

    public AuthRuntimeProbe() { this(false); }
    protected AuthRuntimeProbe(boolean release) { APP=ReleaseScene.application(release); }

    @Override public void onCreate(Bundle value) { super.onCreate(value); args=value; start(); }
    @Override public void onStart() {
        Bundle result=new Bundle();
        try {
            ui=getUiAutomation();
            AccessibilityServiceInfo info=ui.getServiceInfo();
            info.flags|=AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS|AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
            ui.setServiceInfo(info);
            String phase=args.getString("phase","");
            openConnection();
            switch (phase) {
                case "login-a": case "login-b-held":
                    readCurrent();
                    signIn(phase.equals("login-a") ? 0 : 1);
                    if (phase.equals("login-a")) signedIn();
                    else waitForOriginalBackground();
                    break;
                case "current": click("Read current connection"); signedIn(); break;
                case "refresh":
                    readCurrent(); signedIn(); click("Refresh session"); signedIn(); break;
                case "refresh-unknown":
                    readCurrent(); signedIn(); click("Refresh session"); waitText("Check original operation");
                    click("Read original ID"); waitText("Refresh result needs checking"); break;
                case "acknowledge-unknown":
                    waitText("Check original operation"); click("Keep original record and continue");
                    unusable(); break;
                case "current-unusable": click("Read current connection"); unusable(); break;
                case "certificate-cold-unusable":
                    // Mounting the real connection pane already reads the
                    // current Native record. Observe that completed result;
                    // a second offscreen read button is unnecessary here.
                    stage="certificate-cold-auto-read-result";
                    waitPresent(node->text(node.getText()).contains("Original result is unconfirmed")
                        &&text(node.getText()).contains("No confirmed usable session"));
                    unusable();break;
                case "invalid-certificate":
                    readCurrent(); signInUntrustedCertificate();
                    waitPresent(node->text(node.getText()).endsWith("(SYNC_NETWORK_ERROR)")
                        ||text(node.getText()).endsWith("(SYNC_CONNECTION_FAILED)"));
                    waitText("Check original operation");click("Read original ID");
                    waitPresent(node->label(node,"Sign-in result needs checking")||label(node,"Original result is unconfirmed"));
                    break;
                case "logout":
                    readCurrent(); signedIn(); click("Sign out on server"); waitText("Review connection action");
                    click("Confirm this action"); waitText("The service confirmed sign-out of the original device session.");
                    unusable(); break;
                default: require(additionalPhase(phase), "Unknown owned auth phase");
            }
            clearPassword();
            result.putString("stream","OPENNEXUS_"+namespace()+"_PHASE_OK "+phase+"\n");finish(0,result);
        } catch (Throwable error) {
            // Even the failure path never serializes AX values, exception
            // messages, passwords, usernames, endpoints or native replies.
            clearPassword();
            result.putString("stream","OPENNEXUS_"+namespace()+"_PHASE_FAILED stage="+stage+" class="+error.getClass().getSimpleName()+"\n"+shape());
            finish(1,result);
        }
    }

    protected String namespace() { return "AUTH"; }
    protected boolean additionalPhase(String phase) throws Exception { return false; }
    protected String[] diagnosticLabels() { return new String[0]; }
    protected String[] diagnosticCodes() { return new String[0]; }
    protected boolean viewportAdmission() { return false; }

    protected final String originalCycleInputMode() { return args.getString("cycle_input","touch"); }
    protected final String originalCycleDispatchId() { return args.getString("cycle_dispatch_id",""); }

    protected void previewMerge() {
        String mode=args.getString("association_preview","touch");
        require(mode.equals("touch") || mode.equals("native-accessibility") || mode.equals("webview-touch"),"Unknown original preview input mode");
        if(mode.equals("touch")) { click("Preview merge"); return; }
        if(!"none".equals(inputTarget)) {
            inputHistory.add(inputTarget+" "+inputTrace);
            if(inputHistory.size()>12)inputHistory.remove(0);
        }
        inputTarget="Preview merge";
        inputTrace="native_accessibility=not_sent physical_touch=not_sent automatic_retry=false";
        stage="original-preview-wait-before-input";
        Predicate<AccessibilityNodeInfo> target=node->APP.equals(text(node.getPackageName()))
            && label(node,"Preview merge") && node.isEnabled() && node.isClickable();
        long admissionStarted=SystemClock.uptimeMillis();
        AccessibilityNodeInfo original;
        try {
            waitFor(target,true);
            stage="original-preview-fresh-unique-before-input";
            original=unique(target);
        } catch (RuntimeException error) {
            int named=0,visible=0,enabled=0;
            for(AccessibilityNodeInfo node:nodes()) {
                if(APP.equals(text(node.getPackageName())) && label(node,"Preview merge") && node.isClickable()) {
                    named++;
                    if(node.isVisibleToUser())visible++;
                    if(node.isEnabled())enabled++;
                }
            }
            // Counts and elapsed time distinguish a pending native read from
            // a changed fresh AX target. Do not export field values/reasons,
            // change the deadline, or send an input after either failure.
            inputTrace="admission_elapsed_ms="+(SystemClock.uptimeMillis()-admissionStarted)
                +" named="+named+" visible="+visible+" enabled="+enabled
                +" native_accessibility=not_sent physical_touch=not_sent automatic_retry=false";
            throw error;
        }
        if(mode.equals("webview-touch")) {
            String nonce=args.getString("preview_dispatch_id","");
            require(nonce.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
                "Original owned WebView input dispatch is missing");
            // One readiness signal on this original instrumentation stream.
            // The parent sends software browser input; this branch sends no
            // native touch/accessibility action or application IPC request.
            stage="owned-webview-preview-ready";
            inputTrace="owned_webview_touch=requested native_accessibility=not_sent physical_touch=not_sent automatic_retry=false";
            Bundle ready=new Bundle();ready.putString("owned_preview_ready",nonce);sendStatus(1,ready);
            return;
        }
        stage="single-native-accessibility-preview";
        inputTrace="native_accessibility=attempted physical_touch=not_sent automatic_retry=false";
        boolean accepted=original.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        inputTrace="native_accessibility="+(accepted?"accepted":"refused")+" physical_touch=not_sent automatic_retry=false";
        require(accepted,"Original native accessibility action was refused; no touch fallback sent");
        try { ui.waitForIdle(150,5000); }
        catch(java.util.concurrent.TimeoutException ignored) {
            // Read the following original UI/native result; never send another action.
        }
    }

    private void openConnection() {
        stage="open-connection";
        // The Settings dialog and its title can already be rendered when a
        // new instrumentation connection attaches. Prefer its existing tab;
        // a non-action title must never become the Settings button candidate.
        long until=SystemClock.uptimeMillis()+30000;
        do {
            if (hasText("Server connection and sign-in")) return;
            if (hasText("Sync account")) { click("Sync account"); break; }
            boolean settings=false;
            for (AccessibilityNodeInfo node:nodes())
                if (node.isVisibleToUser() && actionable(node,"Settings")) settings=true;
            if (settings) { click("Settings"); click("Sync account"); break; }
            SystemClock.sleep(150);
        } while (SystemClock.uptimeMillis()<until);
        // A confirmed native profile makes this pane taller than the screen.
        // The heading can exist above its restored scroll position; finding it
        // is a navigation check, not permission to click another Settings tab.
        waitPresent(node->label(node,"Server connection and sign-in"));
    }

    private JSONObject privateConfiguration() throws Exception {
        File file=new File(getContext().getNoBackupFilesDir(),"owned-auth.json");
        require(file.isFile() && file.length()>0 && file.length()<=8192,"Private input missing");
        byte[] data=new byte[(int)file.length()];
        try (FileInputStream input=new FileInputStream(file)) {
            int offset=0,count;while (offset<data.length && (count=input.read(data,offset,data.length-offset))>0) offset+=count;
            require(offset==data.length && input.read()==-1,"Private input changed");
        }
        try { return new JSONObject(new String(data,StandardCharsets.UTF_8)); }
        finally { java.util.Arrays.fill(data,(byte)0); }
    }

    private void signIn(int index) throws Exception {
        stage="fill-owned-identity";
        JSONObject config=privateConfiguration();String endpoint=config.getString("endpoint");
        require(endpoint.matches("https://[a-z0-9]+(-[a-z0-9]+)*\\.trycloudflare\\.com"),"Owned origin changed");
        JSONArray accounts=config.getJSONArray("accounts");require(accounts.length()==2,"Owned accounts changed");
        JSONObject account=accounts.getJSONObject(index);
        set("Server URL",endpoint,false);set("Account",account.getString("username"),false);
        set("Device name",account.getString("device_name"),false);
        // Fill the secret last. Identity edits deliberately clear it in the app.
        stage="fill-owned-password";set("Password",account.getString("password"),true);
        stage="submit-once";click("Sign in");
        stage="password-cleared-before-result";
        waitFor(node->node.isEditable() && node.isPassword() && text(node.getText()).isEmpty(),false);
    }

    protected void signedIn() {
        stage="current-signed-in";waitPresent(node->label(node,"Session saved · Signed in"));
        waitFor(node->label(node,"Refresh session") && node.isEnabled(),true);
    }
    private void signInUntrustedCertificate() throws Exception {
        JSONObject config=privateConfiguration();String endpoint=config.getString("endpoint");
        require(endpoint.equals("https://10.0.2.2:19074")&&config.getString("scope").equals("owned-untrusted-certificate")
            &&config.getString("certificate_sha256").matches("[0-9a-f]{64}"),"Untrusted test origin changed");
        JSONArray accounts=config.getJSONArray("accounts");require(accounts.length()==2,"Owned accounts changed");
        JSONObject account=accounts.getJSONObject(0);
        stage="untrusted-certificate-fill-owned-identity";
        set("Server URL",endpoint,false);set("Account",account.getString("username"),false);
        set("Device name",account.getString("device_name"),false);
        stage="untrusted-certificate-fill-password";set("Password",account.getString("password"),true);
        stage="untrusted-certificate-submit-once";click("Sign in");
        waitFor(node->node.isEditable()&&node.isPassword()&&text(node.getText()).isEmpty(),false);
    }
    protected void readCurrent() {
        // Finishing an instrumentation connection may clear the WebView's RAM
        // view through visibility cancellation. Restore it with the same
        // explicit read a person uses; never infer a usable native selection
        // from the preceding instrumentation's view or re-submit a mutation.
        stage="read-current-before-action";click("Read current connection");
        stage="read-current-rendered-list";
        waitPresent(node->label(node,"Saved connections"));
        stage="read-current-idle-action";
        waitFor(node->label(node,"Read current connection") && node.isEnabled(),true);
    }
    private void unusable() {
        stage="current-not-usable";
        waitPresent(node->text(node.getText()).contains("No confirmed usable session"));
        for (AccessibilityNodeInfo node:nodes())
            require(!(label(node,"Refresh session") && node.isEnabled()),"Unconfirmed session enabled refresh");
    }
    private void set(String name,String value,boolean secret) {
        stage="input-"+name.replace(' ','-');
        AccessibilityNodeInfo node=waitFor(n->n.isEditable() && n.isEnabled() && inputLabel(n,name),true);
        require(node.isPassword()==secret,"Wrong input role");
        Bundle input=new Bundle();input.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,value);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,input),"Input rejected");
        SystemClock.sleep(100);
    }
    private void clearPassword() {
        if (ui==null) return;
        try { for (AccessibilityNodeInfo node:nodes()) if (node.isEditable() && node.isPassword()) {
            Bundle empty=new Bundle();empty.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,"");
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,empty);
        } } catch (Throwable ignored) { /* No raw UI is captured on this path. */ }
    }
    private void waitForOriginalBackground() throws Exception {
        // Keep this same UI connection alive until the host has observed the
        // original committed/held response and actually backgrounded the app.
        // Finishing instrumentation earlier could itself cancel the view and
        // make a later Home/late-response assertion vacuous.
        stage="await-owned-background";
        File root=getContext().getNoBackupFilesDir();
        File ready=new File(root,"owned-auth-held.ready");
        require(ready.createNewFile(),"Original ready marker already exists");
        try (java.io.FileOutputStream out=new java.io.FileOutputStream(ready)) {
            out.write("ready\n".getBytes(StandardCharsets.UTF_8));out.getFD().sync();
        }
        File finish=new File(root,"owned-auth-held.finish");
        long until=SystemClock.uptimeMillis()+90000;
        while (!finish.isFile() && SystemClock.uptimeMillis()<until) SystemClock.sleep(100);
        require(finish.isFile() && finish.length()==7,"Original background signal missing");
        byte[] signal=java.nio.file.Files.readAllBytes(finish.toPath());
        require(java.util.Arrays.equals(signal,"finish\n".getBytes(StandardCharsets.UTF_8)),"Original background signal changed");
    }
    protected void click(String name) {
        if(!"none".equals(inputTarget)) {
            inputHistory.add(inputTarget+" "+inputTrace);
            if(inputHistory.size()>12)inputHistory.remove(0);
        }
        inputTarget=name;
        stage="tap-"+name.replace(' ','-');
        inputTrace="admission=not_completed down=not_sent up=not_sent";
        AccessibilityNodeInfo initial=waitFor(n->actionable(n,name),true);
        Predicate<AccessibilityNodeInfo> target=n->label(n,name) && n.isEnabled() && n.isClickable();
        // A label may be a child of the actual clickable button.
        if (!initial.isClickable()) {
            AccessibilityNodeInfo parent=initial.getParent();
            require(parent!=null && APP.equals(text(parent.getPackageName())) && parent.isClickable(),"Named button is not actionable");
            final String role=text(parent.getClassName());
            target=n->n.isClickable() && role.equals(text(n.getClassName())) && descendantLabel(n,name,0);
        }
        stage="unique-"+name.replace(' ','-');
        if(viewportAdmission()) {
            AccessibilityNodeInfo current=unique(target);Rect full=new Rect();current.getBoundsInScreen(full);
            Rect visible=visibleTouch(current);
            if(visible==null || !visible.equals(full))showTarget(current);
            stage="idle-before-input-"+name.replace(' ','-');
            try { ui.waitForIdle(100,3000); }
            catch(java.util.concurrent.TimeoutException error) { throw new IllegalStateException("Original UI stayed busy before input; no action sent"); }
        }
        stage="stable-"+name.replace(' ','-');
        // Selecting a remote vault triggers a read-only association status and
        // an accessibility scroll. Observe those layout changes before input;
        // do not abort on the first moved frame or ever re-tap after dispatch.
        TargetStability stability=new TargetStability(),visibleStability=new TargetStability();
        TouchViewport.RevealBudget reveal=new TouchViewport.RevealBudget();
        long until=SystemClock.uptimeMillis()+5000;Rect before=new Rect(),hit=new Rect();boolean stable=false;
        do {
            AccessibilityNodeInfo node=unique(target);node.getBoundsInScreen(before);
            Rect visible=viewportAdmission() ? visibleTouch(node) : new Rect(before);
            if(visible==null)visible=new Rect();hit.set(visible);
            inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=not_sent up=not_sent";
            long observed=SystemClock.uptimeMillis();
            // ACTION_SHOW_ON_SCREEN acceptance alone does not confirm that a
            // restored scroll offset exposed the button. Reacquire it and use
            // at most two directional ancestor scrolls before any touch.
            if(viewportAdmission() && reveal.scrollBeforeInput(!hit.isEmpty(),observed)) {
                scrollOwnedTarget(node);
                stability.observe(0,0,0,0,observed);visibleStability.observe(0,0,0,0,observed);
                SystemClock.sleep(100);continue;
            }
            boolean originalStable=stability.observe(before.left,before.top,before.right,before.bottom,observed);
            boolean hitStable=visibleStability.observe(hit.left,hit.top,hit.right,hit.bottom,observed);
            stable=originalStable && hitStable;
            if (stable) break;
            SystemClock.sleep(80);
        } while (SystemClock.uptimeMillis()<until);
        require(stable,"Named button did not settle before the bounded deadline; no input sent");
        inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=not_sent up=not_sent";
        stage="single-touch-down-"+name.replace(' ','-');
        inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=attempted_without_confirmation up=not_sent";
        long start=SystemClock.uptimeMillis();touch(start,start,MotionEvent.ACTION_DOWN,hit.centerX(),hit.centerY());
        inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=accepted up=not_sent";
        stage="single-touch-up-"+name.replace(' ','-');
        inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=accepted up=attempted_without_confirmation";
        SystemClock.sleep(50);touch(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,hit.centerX(),hit.centerY());
        inputTrace="original_bounds="+before.toShortString()+" visible_hit="+hit.toShortString()+" down=accepted up=accepted";
        stage="wait-idle-after-single-touch-"+name.replace(' ','-');
        try { ui.waitForIdle(150,5000); }
        catch (java.util.concurrent.TimeoutException error) {
            // Accessibility may keep reporting caret/timer events. The single
            // tap is already sent; only the following semantic state and
            // original native/provider outcomes can confirm it. Never re-tap.
        }
    }
    private static int[] rectangle(Rect value) { return new int[]{value.left,value.top,value.right,value.bottom}; }
    private Rect visibleTouch(AccessibilityNodeInfo node) {
        if(!node.isEnabled() || !node.isVisibleToUser())return null;
        Rect value=new Rect();node.getBoundsInScreen(value);int[] visible=rectangle(value);
        boolean ownWindow=false;
        List<AccessibilityWindowInfo> windows=ui.getWindows();
        for(AccessibilityWindowInfo window:windows) if(window.getId()==node.getWindowId()
            && window.getType()==AccessibilityWindowInfo.TYPE_APPLICATION && window.isActive()) {
            window.getBoundsInScreen(value);visible=TouchViewport.intersect(visible,rectangle(value));ownWindow=true;break;
        }
        if(!ownWindow)return null;
        int depth=0;
        for(AccessibilityNodeInfo parent=node.getParent();parent!=null && depth++<32;parent=parent.getParent()) {
            if(!APP.equals(text(parent.getPackageName())))return null;
            if(parent.isScrollable() || "android.webkit.WebView".equals(text(parent.getClassName()))) {
                parent.getBoundsInScreen(value);visible=TouchViewport.intersect(visible,rectangle(value));
            }
        }
        if(depth>=32)return null;
        for(AccessibilityWindowInfo window:windows) if(window.getType()==AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
            window.getBoundsInScreen(value);visible=TouchViewport.avoid(visible,rectangle(value));
        }
        return visible==null ? null : new Rect(visible[0],visible[1],visible[2],visible[3]);
    }
    private boolean actionable(AccessibilityNodeInfo node,String name) {
        if (!label(node,name) || !node.isEnabled()) return false;
        if (node.isClickable()) return true;
        AccessibilityNodeInfo parent=node.getParent();
        return parent!=null && APP.equals(text(parent.getPackageName())) && parent.isEnabled() && parent.isClickable();
    }
    private AccessibilityNodeInfo unique(Predicate<AccessibilityNodeInfo> predicate) {
        List<AccessibilityNodeInfo> found=new ArrayList<>();
        for (AccessibilityNodeInfo node:nodes()) if (node.isVisibleToUser() && predicate.test(node)) found.add(node);
        require(found.size()==1,"Named target is missing or ambiguous; no input replayed");return found.get(0);
    }
    protected AccessibilityNodeInfo waitFor(Predicate<AccessibilityNodeInfo> predicate,boolean scroll) {
        long until=SystemClock.uptimeMillis()+30000;long lastScroll=0;
        do {
            boolean foundTarget=false;
            for (AccessibilityNodeInfo node:nodes()) if (predicate.test(node)) {
                foundTarget=true;
                if (node.isVisibleToUser()) return node;
                if (scroll && SystemClock.uptimeMillis()-lastScroll>500) {
                    showTarget(node);lastScroll=SystemClock.uptimeMillis();
                }
            }
            if (!foundTarget && scroll && SystemClock.uptimeMillis()-lastScroll>1000) {
                for (AccessibilityNodeInfo node:nodes()) if (node.isVisibleToUser() && node.isScrollable()) {
                    if (node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)) break;
                }
                lastScroll=SystemClock.uptimeMillis();
            }
            SystemClock.sleep(150);
        } while (SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Owned UI state timed out");
    }
    protected void waitPresent(Predicate<AccessibilityNodeInfo> predicate) {
        // State/heading assertions do not require the text to fit the viewport.
        // Actual inputs and actions still use visible, enabled, uniquely named
        // targets and stable touch bounds. Never scroll away from a visible
        // action solely to show a non-action heading above a tall saved profile.
        long until=SystemClock.uptimeMillis()+30000;
        do {
            for (AccessibilityNodeInfo node:nodes()) if (predicate.test(node)) return;
            SystemClock.sleep(150);
        } while (SystemClock.uptimeMillis()<until);
        throw new IllegalStateException("Owned rendered state timed out");
    }
    private void showTarget(AccessibilityNodeInfo target) {
        if (target.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId())) return;
        // Scroll only the named target's owned ancestor, in the direction of
        // its actual bounds. A target above a restored scroll offset must not
        // trigger an endless forward-only search. No tap uses guessed bounds.
        scrollOwnedTarget(target);
    }
    private void scrollOwnedTarget(AccessibilityNodeInfo target) {
        if(!APP.equals(text(target.getPackageName())))return;
        Rect bounds=new Rect();target.getBoundsInScreen(bounds);
        if(bounds.isEmpty())return;
        int depth=0;
        for (AccessibilityNodeInfo parent=target.getParent();parent!=null && depth++<32;parent=parent.getParent()) {
            if (!APP.equals(text(parent.getPackageName()))) return;
            if (parent.isScrollable()) {
                Rect viewport=new Rect();parent.getBoundsInScreen(viewport);
                int direction=TouchViewport.verticalExposure(rectangle(bounds),rectangle(viewport));
                if(direction!=0) {
                    parent.performAction(direction<0 ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
                    return;
                }
            }
        }
    }
    private boolean hasText(String value) { for (AccessibilityNodeInfo node:nodes()) if (label(node,value)) return true; return false; }
    protected void waitText(String value) { waitFor(n->label(n,value),true); }
    protected static boolean label(AccessibilityNodeInfo node,String value) {
        return value.equals(text(node.getText())) || value.equals(text(node.getContentDescription()))
            || (Build.VERSION.SDK_INT>=26 && value.equals(text(node.getHintText())));
    }
    protected static boolean descendantLabel(AccessibilityNodeInfo node,String value,int depth) {
        if (label(node,value)) return true;if (depth>=4) return false;
        for (int i=0;i<node.getChildCount();i++) { AccessibilityNodeInfo child=node.getChild(i);if (child!=null && descendantLabel(child,value,depth+1)) return true; }
        return false;
    }
    private static int editableCount(AccessibilityNodeInfo node,int depth) {
        if (node.isEditable()) return 1;if (depth>=4) return 0;int count=0;
        for (int i=0;i<node.getChildCount();i++) { AccessibilityNodeInfo child=node.getChild(i);if (child!=null) count+=editableCount(child,depth+1); }
        return count;
    }
    private boolean inputLabel(AccessibilityNodeInfo node,String name) {
        if (label(node,name)) return true;
        AccessibilityNodeInfo associated=node.getLabeledBy();
        if (associated!=null && APP.equals(text(associated.getPackageName())) && label(associated,name)) return true;
        // The shared form uses an HTML label wrapping its one input. Refuse a
        // generic form/section with multiple editable descendants; never select
        // an input by its ordinal, existing value or a guessed screen location.
        AccessibilityNodeInfo parent=node.getParent();
        for (int depth=0;parent!=null && depth<3;depth++,parent=parent.getParent()) {
            if (!APP.equals(text(parent.getPackageName()))) return false;
            int count=editableCount(parent,0);if (count>1) return false;
            if (count==1 && descendantLabel(parent,name,0)) return true;
        }
        return false;
    }
    protected List<AccessibilityNodeInfo> nodes() {
        if (Build.VERSION.SDK_INT>=34) ui.clearCache();List<AccessibilityNodeInfo> result=new ArrayList<>();
        AccessibilityNodeInfo root=ui.getRootInActiveWindow();
        if (root!=null && APP.equals(text(root.getPackageName()))) result.add(root);
        for (int i=0;i<result.size() && result.size()<2500;i++) {
            AccessibilityNodeInfo node=result.get(i);
            for (int j=0;j<node.getChildCount();j++) { AccessibilityNodeInfo child=node.getChild(j);if (child!=null) result.add(child); }
        }
        return result;
    }
    private String shape() {
        StringBuilder value=new StringBuilder("redacted-auth-ui-shape\n");
        value.append("original_input ").append(inputTrace).append('\n');
        for(String previous:inputHistory)value.append("previous_original_input ").append(previous).append('\n');
        String[] labels={"Settings","Sync account","Server connection and sign-in","Read current connection","Saved connections","Server URL","Account","Password",
            "Device name","Sign in","Refresh session","Check original operation","Read original ID","Confirm this action"};
        for (String label:labels) {
            boolean visible=false;for (AccessibilityNodeInfo node:nodes()) visible|=label(node,label) && node.isVisibleToUser();
            value.append(label).append(" present=").append(hasText(label)).append(" visible=").append(visible).append('\n');
        }
        for (String label:diagnosticLabels()) {
            int present=0,visible=0,enabled=0,clickable=0;
            for (AccessibilityNodeInfo node:nodes()) if (!node.isPassword() && label(node,label)) {
                present++;if(node.isVisibleToUser())visible++;if(node.isEnabled())enabled++;if(node.isClickable())clickable++;
            }
            value.append(label).append(" present=").append(present).append(" visible=").append(visible)
                .append(" enabled=").append(enabled).append(" clickable=").append(clickable).append('\n');
        }
        for(String code:diagnosticCodes()) {
            boolean present=false;
            for(AccessibilityNodeInfo node:nodes()) if(!node.isPassword() && !node.isEditable()
                && text(node.getText()).endsWith("("+code+")"))present=true;
            if(present)value.append("public_error_code=").append(code).append('\n');
        }
        if (ui!=null) for (AccessibilityNodeInfo node:nodes()) {
            value.append("editable=").append(node.isEditable()).append(" password=").append(node.isPassword())
                .append(" visible=").append(node.isVisibleToUser()).append(" clickable=").append(node.isClickable())
                .append(" enabled=").append(node.isEnabled()).append('\n');
        }
        return value.toString();
    }
    private void touch(long start,long now,int action,float x,float y) {
        MotionEvent.PointerProperties pointer=new MotionEvent.PointerProperties();
        pointer.id=0;pointer.toolType=MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords point=new MotionEvent.PointerCoords();
        point.x=x;point.y=y;point.pressure=action==MotionEvent.ACTION_UP || action==MotionEvent.ACTION_CANCEL ? 0 : 1;point.size=1;
        MotionEvent event=MotionEvent.obtain(start,now,action,1,new MotionEvent.PointerProperties[]{pointer},
            new MotionEvent.PointerCoords[]{point},0,0,1,1,0,0,InputDevice.SOURCE_TOUCHSCREEN,0);
        require(event.getToolType(0)==MotionEvent.TOOL_TYPE_FINGER,"Owned touch has no finger identity");
        try { require(ui.injectInputEvent(event,true),"Owned touch rejected"); } finally { event.recycle(); }
    }
    protected static String text(CharSequence value) { return value==null ? "" : value.toString(); }
    protected static void require(boolean value,String message) { if (!value) throw new IllegalStateException(message); }
}
