package cc.kronecker.opennexus.testdriver;

import android.os.Bundle;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.List;

/** Source-only fixed Note GUI seed. UI output is never a Native Save ID or ACK. */
public final class ReleaseQuotaSeedRuntimeProbe extends RuntimeProbe {
    private static final String DRIVER="cc.kronecker.opennexus.testdriver";
    private boolean creating,created,started,closed,terminal,revoked;

    public ReleaseQuotaSeedRuntimeProbe() {super(true);}
    @Override protected String privatePhaseNamespace() {return PRIVATE_QUOTA_SEED_NAMESPACE;}
    private boolean creationOpen() {return creating && !created && !closed && !terminal && !revoked;}
    @Override public void onCreate(Bundle args) {
        if(creating || created || closed || terminal || revoked) {refuse();return;}
        creating=true;
        try {
            if(args==null) {refuse();return;}
            int size=args.size();if(!creationOpen() || size!=1) {refuse();return;}
            Object phase=args.get("phase");
            if(!creationOpen() || !(phase instanceof String) || !"markdown".equals(phase)) {refuse();return;}
            android.content.Context context=getContext();
            if(!creationOpen() || context==null) {refuse();return;}
            String owner=context.getPackageName();
            if(!creationOpen() || !DRIVER.equals(owner)) {refuse();return;}
            android.content.Context target=getTargetContext();
            if(!creationOpen() || target==null) {refuse();return;}
            String targetOwner=target.getPackageName();
            if(!creationOpen() || !DRIVER.equals(targetOwner)) {refuse();return;}
            Bundle original=new Bundle();original.putString("phase","markdown");
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
        if(!created || !started || closed) throw new IllegalStateException("Private Note UI closed");
        return super.nodes();
    }
    private void refuse() {creating=false;revoked=true;closed=true;finish(1,null);}
    @Override public void sendStatus(int code,Bundle value) {refuse();}
    private boolean resultOpen() {return terminal && closed && !revoked;}
    @Override public void finish(int code,Bundle value) {
        if(terminal) {revoked=true;return;}
        boolean original=created && started && !closed && !revoked;
        terminal=true;closed=true;
        boolean success=false;
        try {
            success=original && code==0 && value!=null;
            if(success) {int size=value.size();success=resultOpen() && size==2;}
            if(success) {
                Object stream=value.get("stream");
                success=resultOpen() && stream instanceof String && PRIVATE_QUOTA_SEED_OK.equals(stream);
            }
            if(success) {
                Object phase=value.get("phase");
                success=resultOpen() && phase instanceof String && "markdown".equals(phase);
            }
        } catch(Throwable error) {revoked=true;}
        success &= !revoked;
        Bundle result=new Bundle();result.putString("stream",success?PRIVATE_QUOTA_SEED_OK:PRIVATE_QUOTA_SEED_FAILED);
        if(success)result.putString("phase","markdown");
        try {super.finish(success?0:1,result);} catch(RuntimeException error) {revoked=true;throw error;}
    }
}
