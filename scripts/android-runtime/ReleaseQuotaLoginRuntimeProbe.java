package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.List;

/** Source-only account-a GUI login. No Native operation ID, session bytes or ACK. */
public final class ReleaseQuotaLoginRuntimeProbe extends AuthRuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    static final String UI="OPENNEXUS_SIGNED_QUOTA_LOGIN_UI signed_in_UI_observed=true raw_UI_recorded=false native_original_state_verified=false\n";
    static final String OK="OPENNEXUS_SIGNED_QUOTA_LOGIN_PHASE_OK login-a\n";
    static final String FAILED="OPENNEXUS_SIGNED_QUOTA_LOGIN_PHASE_FAILED raw_UI_recorded=false native_original_state_verified=false\n";
    private boolean creating,created,started,closed,terminal,revoked,signedInEntered,uiObserved,uiSent,statusSending;

    public ReleaseQuotaLoginRuntimeProbe() {super(true);}
    @Override protected String namespace() {return "SIGNED_QUOTA_LOGIN";}
    @Override protected boolean viewportAdmission() {return true;}
    @Override protected boolean productUiObservationClosed() {return true;}
    private boolean creationOpen() {return creating && !created && !closed && !terminal && !revoked;}
    @Override public void onCreate(Bundle args) {
        if(creating || created || closed || terminal || revoked) {refuse();return;}
        creating=true;
        try {
            if(args==null) {refuse();return;}
            int size=args.size();if(!creationOpen() || size!=1) {refuse();return;}
            Object phase=args.get("phase");
            if(!creationOpen() || !(phase instanceof String) || !"login-a".equals(phase)) {refuse();return;}
            android.content.Context context=getContext();
            if(!creationOpen() || context==null) {refuse();return;}
            String owner=context.getPackageName();
            if(!creationOpen() || !DRIVER.equals(owner)) {refuse();return;}
            android.content.Context target=getTargetContext();
            if(!creationOpen() || target==null) {refuse();return;}
            String targetOwner=target.getPackageName();
            if(!creationOpen() || !DRIVER.equals(targetOwner)) {refuse();return;}
            Bundle original=new Bundle();original.putString("phase","login-a");
            created=true;creating=false;
            super.onCreate(original);
        } catch(Throwable error) {refuse();} finally {creating=false;}
    }
    @Override public void onStart() {
        if(!created || started || closed) {refuse();return;}
        started=true;
        try {super.onStart();} finally {closed=true;}
    }
    @Override protected List<AccessibilityNodeInfo> nodes() {
        if(!created || !started || closed) throw new IllegalStateException("Private login UI closed");
        return super.nodes();
    }
    @Override protected void signedIn() {
        if(!created || !started || closed || signedInEntered) {refuse();return;}
        signedInEntered=true;super.signedIn();
        if(closed || revoked) {refuse();return;}
        uiObserved=true;Bundle result=new Bundle();result.putString("stream",UI);sendStatus(1,result);
    }
    private void refuse() {creating=false;statusSending=false;revoked=true;closed=true;finish(1,null);}
    private boolean statusOpen() {
        return created && started && !closed && !terminal && !revoked
            && uiObserved && !uiSent && statusSending;
    }
    @Override public void sendStatus(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        if(!created || !started || closed || revoked || !uiObserved || uiSent || statusSending
            || code!=1 || value==null) {refuse();return;}
        statusSending=true;
        try {
            int size=value.size();if(!statusOpen() || size!=1) {refuse();return;}
            Object stream=value.get("stream");
            if(!statusOpen() || !(stream instanceof String) || !UI.equals(stream)) {refuse();return;}
            Bundle result=new Bundle();result.putString("stream",UI);
            super.sendStatus(1,result);
            if(!statusOpen()) {refuse();return;}
            uiSent=true;
        } catch(Throwable error) {refuse();} finally {statusSending=false;}
    }
    private boolean resultOpen() {return terminal && closed && !revoked;}
    @Override public void finish(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        boolean original=created && started && !closed && !revoked && uiSent;
        terminal=true;closed=true;boolean success=false;
        try {
            success=original && code==0 && value!=null;
            if(success) {int size=value.size();success=resultOpen() && size==1;}
            if(success) {
                Object stream=value.get("stream");
                success=resultOpen() && stream instanceof String && OK.equals(stream);
            }
        } catch(Throwable error) {revoked=true;}
        success &= !revoked;
        Bundle result=new Bundle();result.putString("stream",success?OK:FAILED);
        try {super.finish(success?0:1,result);} catch(RuntimeException error) {revoked=true;throw error;}
    }
}
