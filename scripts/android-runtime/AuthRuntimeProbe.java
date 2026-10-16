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
import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import org.json.JSONArray;
import org.json.JSONObject;

/** Actual installed-app UI only. No password arguments, raw UI dump or screenshot. */
public final class AuthRuntimeProbe extends Instrumentation {
    private static final String APP = "cc.kronecker.opennexus.dev";
    private Bundle args;
    private UiAutomation ui;
    private String stage = "initialization";

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
                case "logout":
                    readCurrent(); signedIn(); click("Sign out on server"); waitText("Review connection action");
                    click("Confirm this action"); waitText("The service confirmed sign-out of the original device session.");
                    unusable(); break;
                default: throw new IllegalStateException("Unknown owned auth phase");
            }
            clearPassword();
            result.putString("stream","OPENNEXUS_AUTH_PHASE_OK "+phase+"\n");finish(0,result);
        } catch (Throwable error) {
            // Even the failure path never serializes AX values, exception
            // messages, passwords, usernames, endpoints or native replies.
            clearPassword();
            result.putString("stream","OPENNEXUS_AUTH_PHASE_FAILED stage="+stage+" class="+error.getClass().getSimpleName()+"\n"+shape());
            finish(1,result);
        }
    }

    private void openConnection() {
        stage="open-connection";
        if (!hasText("Server connection and sign-in")) {
            click("Settings"); click("Sync account");
        }
        // A confirmed native profile makes this pane taller than the screen.
        // The heading can exist above its restored scroll position; finding it
        // is a navigation check, not permission to click another Settings tab.
        waitText("Server connection and sign-in");
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

    private void signedIn() {
        stage="current-signed-in";waitText("Session saved · Signed in");
        waitFor(node->label(node,"Refresh session") && node.isEnabled(),true);
    }
    private void readCurrent() {
        // Finishing an instrumentation connection may clear the WebView's RAM
        // view through visibility cancellation. Restore it with the same
        // explicit read a person uses; never infer a usable native selection
        // from the preceding instrumentation's view or re-submit a mutation.
        stage="read-current-before-action";click("Read current connection");
        waitText("Saved connections");
        waitFor(node->label(node,"Read current connection") && node.isEnabled(),true);
    }
    private void unusable() {
        stage="current-not-usable";
        waitFor(node->text(node.getText()).contains("No confirmed usable session"),true);
        for (AccessibilityNodeInfo node:nodes())
            require(!(node.isVisibleToUser() && label(node,"Refresh session") && node.isEnabled()),"Unconfirmed session enabled refresh");
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
    private void click(String name) {
        AccessibilityNodeInfo initial=waitFor(n->label(n,name) && n.isEnabled(),true);
        Predicate<AccessibilityNodeInfo> target=n->label(n,name) && n.isEnabled() && n.isClickable();
        // A label may be a child of the actual clickable button.
        if (!initial.isClickable()) {
            AccessibilityNodeInfo parent=initial.getParent();
            require(parent!=null && APP.equals(text(parent.getPackageName())) && parent.isClickable(),"Named button is not actionable");
            final String role=text(parent.getClassName());
            target=n->n.isClickable() && role.equals(text(n.getClassName())) && descendantLabel(n,name,0);
        }
        AccessibilityNodeInfo node=unique(target);Rect before=new Rect();node.getBoundsInScreen(before);
        for (int i=0;i<3;i++) {
            SystemClock.sleep(50);node=unique(target);Rect now=new Rect();node.getBoundsInScreen(now);
            require(before.equals(now) && !now.isEmpty(),"Button moved before the single tap");
        }
        long start=SystemClock.uptimeMillis();touch(start,start,MotionEvent.ACTION_DOWN,before.centerX(),before.centerY());
        SystemClock.sleep(50);touch(start,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,before.centerX(),before.centerY());
    }
    private AccessibilityNodeInfo unique(Predicate<AccessibilityNodeInfo> predicate) {
        List<AccessibilityNodeInfo> found=new ArrayList<>();
        for (AccessibilityNodeInfo node:nodes()) if (node.isVisibleToUser() && predicate.test(node)) found.add(node);
        require(found.size()==1,"Named target is missing or ambiguous; no input replayed");return found.get(0);
    }
    private AccessibilityNodeInfo waitFor(Predicate<AccessibilityNodeInfo> predicate,boolean scroll) {
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
    private void showTarget(AccessibilityNodeInfo target) {
        if (target.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId())) return;
        // Scroll only the named target's owned ancestor, in the direction of
        // its actual bounds. A target above a restored scroll offset must not
        // trigger an endless forward-only search. No tap uses guessed bounds.
        Rect bounds=new Rect();target.getBoundsInScreen(bounds);
        for (AccessibilityNodeInfo parent=target.getParent();parent!=null;parent=parent.getParent()) {
            if (!APP.equals(text(parent.getPackageName()))) return;
            if (parent.isScrollable()) {
                Rect viewport=new Rect();parent.getBoundsInScreen(viewport);
                parent.performAction(bounds.top<viewport.top || bounds.isEmpty()
                    ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
                return;
            }
        }
    }
    private boolean hasText(String value) { for (AccessibilityNodeInfo node:nodes()) if (label(node,value)) return true; return false; }
    private void waitText(String value) { waitFor(n->label(n,value),true); }
    private static boolean label(AccessibilityNodeInfo node,String value) {
        return value.equals(text(node.getText())) || value.equals(text(node.getContentDescription()))
            || (Build.VERSION.SDK_INT>=26 && value.equals(text(node.getHintText())));
    }
    private static boolean descendantLabel(AccessibilityNodeInfo node,String value,int depth) {
        if (label(node,value)) return true;if (depth>=4) return false;
        for (int i=0;i<node.getChildCount();i++) { AccessibilityNodeInfo child=node.getChild(i);if (child!=null && descendantLabel(child,value,depth+1)) return true; }
        return false;
    }
    private static int editableCount(AccessibilityNodeInfo node,int depth) {
        if (node.isEditable()) return 1;if (depth>=4) return 0;int count=0;
        for (int i=0;i<node.getChildCount();i++) { AccessibilityNodeInfo child=node.getChild(i);if (child!=null) count+=editableCount(child,depth+1); }
        return count;
    }
    private static boolean inputLabel(AccessibilityNodeInfo node,String name) {
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
    private List<AccessibilityNodeInfo> nodes() {
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
        String[] labels={"Settings","Sync account","Server connection and sign-in","Read current connection","Saved connections","Server URL","Account","Password",
            "Device name","Sign in","Refresh session","Check original operation","Read original ID","Confirm this action"};
        for (String label:labels) {
            boolean visible=false;for (AccessibilityNodeInfo node:nodes()) visible|=label(node,label) && node.isVisibleToUser();
            value.append(label).append(" present=").append(hasText(label)).append(" visible=").append(visible).append('\n');
        }
        if (ui!=null) for (AccessibilityNodeInfo node:nodes()) {
            value.append("editable=").append(node.isEditable()).append(" password=").append(node.isPassword())
                .append(" visible=").append(node.isVisibleToUser()).append(" clickable=").append(node.isClickable())
                .append(" enabled=").append(node.isEnabled()).append('\n');
        }
        return value.toString();
    }
    private void touch(long start,long now,int action,float x,float y) {
        MotionEvent event=MotionEvent.obtain(start,now,action,x,y,0);event.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        try { require(ui.injectInputEvent(event,true),"Owned touch rejected"); } finally { event.recycle(); }
    }
    private static String text(CharSequence value) { return value==null ? "" : value.toString(); }
    private static void require(boolean value,String message) { if (!value) throw new IllegalStateException(message); }
}
