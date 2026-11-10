package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.os.Bundle;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Controlled private result/status getters on actual API36-compiled entries. */
public final class ReleaseQuotaPrivateResultCheck {
    private static final List<String> ran=new ArrayList<>();
    private static void need(boolean value) {if(!value)throw new AssertionError("Private result chronology failed");}
    private static Bundle args(boolean login) {Bundle b=new Bundle();b.putString("phase",login?"login-a":"markdown");return b;}
    private static Bundle ui() {Bundle b=new Bundle();b.putString("stream",ReleaseQuotaLoginRuntimeProbe.UI);return b;}
    private static Bundle ok() {Bundle b=new Bundle();b.putString("stream",ReleaseQuotaLoginRuntimeProbe.OK);return b;}
    private static Instrumentation entry(boolean login) {
        Control.reset();Instrumentation p=login?new ReleaseQuotaLoginRuntimeProbe():new ReleaseQuotaSeedRuntimeProbe();
        p.onCreate(args(login));return p;
    }
    private static void failed(Instrumentation p) {
        need(Control.finishCalls==1 && Control.exit==1);
        for(String s:Control.streams)need(!s.contains("SENSITIVE"));
        int reads=Control.uiReads,status=Control.statusCalls;
        p.onStart();p.finish(0,ok());p.sendStatus(1,ui());
        need(Control.finishCalls==1 && Control.uiReads==reads && Control.statusCalls==status);
    }
    private static void check(String name,Runnable body) {
        String selected=System.getProperty("onx.private_create_checks");
        if(selected!=null && !Arrays.asList(selected.split(",",-1)).contains(name))return;
        need(!ran.contains(name));ran.add(name);body.run();
    }
    public static void main(String[] ignored) {
        check("status-size-reserves-before-reentry",()->{
            Instrumentation p=entry(true);
            Control.action=()->{Control.markStatus=true;Control.nextSizeHook=()->p.sendStatus(1,ui());};
            p.onStart();failed(p);need(Control.statusCalls==0 && Control.lastStatus.gets==0);
        });
        check("status-stream-reentry-stops-next-read",()->{
            Instrumentation p=entry(true);
            Control.action=()->{Control.markStatus=true;Control.nextGetHook=()->p.sendStatus(1,ui());};
            p.onStart();failed(p);need(Control.statusCalls==0 && Control.lastStatus.gets==1);
        });
        check("framework-status-return-precedes-uiSent",()->{
            Instrumentation p=entry(true);Control.statusAction=()->p.finish(0,ok());
            p.onStart();failed(p);need(Control.statusCalls==1);
        });
        check("finish-size-reentry-stops-getters",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.mode="close-finish-size";Control.resultAction=p::onStart;
                p.onStart();failed(p);need(Control.lastResult.gets==0);
            }
        });
        check("finish-stream-reentry-stops-getters",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.mode="close-finish-stream";Control.resultAction=p::onStart;
                p.onStart();failed(p);need(Control.lastResult.gets==1);
            }
        });
        check("finish-phase-reentry-stops-getters",()->{
            Instrumentation p=entry(false);Control.mode="close-finish-phase";Control.resultAction=p::onStart;
            p.onStart();failed(p);need(Control.lastResult.gets==2);
        });
        check("private-result-getter-error-static",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.mode="private-result-error";
                p.onStart();failed(p);need(Control.lastResult.gets==1);
            }
        });
        check("private-fields-read-once-positive",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.action=()->Control.markStatus=login;
                p.onStart();need(Control.finishCalls==1 && Control.exit==0);
                need(Control.lastResult.gets==(login?1:2));
                if(login)need(Control.statusCalls==1 && Control.lastStatus.gets==1);
            }
        });
        System.out.println("{\"passed\":true,\"checks\":"+ran.size()+",\"actual_bodies\":[\""
            +String.join("\",\"",ran)+"\"],\"actual_shared_UI_body_executed\":false,\"actual_device_or_Native\":false}");
        if(System.getProperty("onx.private_gui_checks")!=null)ReleaseQuotaPrivateGuiCheck.main(new String[0]);
    }
}
