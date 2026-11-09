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
    private boolean created,started,closed,terminal,revoked,signedInEntered,uiObserved,uiSent;

    public ReleaseQuotaLoginRuntimeProbe() {super(true);}
    @Override protected String namespace() {return "SIGNED_QUOTA_LOGIN";}
    @Override protected boolean viewportAdmission() {return true;}
    @Override protected boolean productUiObservationClosed() {return true;}
    @Override public void onCreate(Bundle args) {
        try {
            if(created || closed || args==null || args.size()!=1
                || !(args.get("phase") instanceof String) || !"login-a".equals(args.get("phase"))) {
                refuse();return;
            }
            if(!DRIVER.equals(getContext().getPackageName())
                || !DRIVER.equals(getTargetContext().getPackageName())) {refuse();return;}
            created=true;
            Bundle original=new Bundle();original.putString("phase","login-a");
            super.onCreate(original);
        } catch(Throwable error) {refuse();}
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
    private void refuse() {revoked=true;closed=true;finish(1,null);}
    @Override public void sendStatus(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        boolean valid=false;
        try {
            valid=created && started && !closed && !revoked && uiObserved && !uiSent && code==1
                && value!=null && value.size()==1 && value.get("stream") instanceof String
                && UI.equals(value.get("stream"));
        } catch(RuntimeException error) {revoked=true;}
        if(!valid || revoked) {refuse();return;}
        uiSent=true;Bundle result=new Bundle();result.putString("stream",UI);
        try {super.sendStatus(1,result);} catch(RuntimeException error) {refuse();throw error;}
    }
    @Override public void finish(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        boolean original=created && started && !closed && !revoked && uiSent;
        terminal=true;closed=true;boolean success=false;
        try {
            success=original && code==0 && value!=null && value.size()==1
                && value.get("stream") instanceof String && OK.equals(value.get("stream"));
        } catch(RuntimeException error) {revoked=true;}
        success &= !revoked;
        Bundle result=new Bundle();result.putString("stream",success?OK:FAILED);
        try {super.finish(success?0:1,result);} catch(RuntimeException error) {revoked=true;throw error;}
    }
}
