package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.List;

/** Source-only fixed Note GUI seed. UI output is never a Native Save ID or ACK. */
public final class ReleaseQuotaSeedRuntimeProbe extends RuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private boolean created,started,closed,terminal,revoked;

    public ReleaseQuotaSeedRuntimeProbe() {super(true);}
    @Override protected String privatePhaseNamespace() {return PRIVATE_QUOTA_SEED_NAMESPACE;}
    @Override public void onCreate(Bundle args) {
        try {
            if(created || closed || args==null || args.size()!=1
                || !(args.get("phase") instanceof String) || !"markdown".equals(args.get("phase"))) {
                refuse();return;
            }
            if(!DRIVER.equals(getContext().getPackageName())
                || !DRIVER.equals(getTargetContext().getPackageName())) {refuse();return;}
            created=true;
            Bundle original=new Bundle();original.putString("phase","markdown");
            super.onCreate(original);
        } catch(Throwable error) {refuse();}
    }
    @Override public void onStart() {
        if(!created || started || closed) {refuse();return;}
        started=true;
        try {super.onStart();} finally {closed=true;}
    }
    @Override protected List<AccessibilityNodeInfo> nodes() {
        if(!created || !started || closed) throw new IllegalStateException("Private Note UI closed");
        return super.nodes();
    }
    private void refuse() {revoked=true;closed=true;finish(1,null);}
    @Override public void sendStatus(int code,Bundle value) {refuse();}
    @Override public void finish(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        boolean original=created && started && !closed && !revoked;
        terminal=true;closed=true;
        boolean success=false;
        try {
            success=original && code==0 && value!=null && value.size()==2
                && value.get("stream") instanceof String && PRIVATE_QUOTA_SEED_OK.equals(value.get("stream"))
                && value.get("phase") instanceof String && "markdown".equals(value.get("phase"));
        } catch(RuntimeException error) {revoked=true;}
        success &= !revoked;
        Bundle result=new Bundle();result.putString("stream",success?PRIVATE_QUOTA_SEED_OK:PRIVATE_QUOTA_SEED_FAILED);
        if(success)result.putString("phase","markdown");
        try {super.finish(success?0:1,result);} catch(RuntimeException error) {revoked=true;throw error;}
    }
}
