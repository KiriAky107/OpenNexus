package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.os.Bundle;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Actual entry bytecode, controlled getters/base only; no Android GUI or Native. */
public final class ReleaseQuotaPrivateCreateCheck {
    private static final List<String> ran=new ArrayList<>();
    private static void need(boolean value) {if(!value)throw new AssertionError("Private create chronology failed");}
    private static Instrumentation entry(boolean login) {
        Control.reset();return login?new ReleaseQuotaLoginRuntimeProbe():new ReleaseQuotaSeedRuntimeProbe();
    }
    private static Bundle args(boolean login) {Bundle b=new Bundle();b.putString("phase",login?"login-a":"markdown");return b;}
    private static void closed(Instrumentation value) {
        need(Control.original==null && Control.starts==0 && Control.uiReads==0);
        need(Control.finishCalls==1 && Control.exit==1 && Control.statusCalls==0);
        for(String line:Control.streams)need(!line.contains("SENSITIVE"));
        value.onCreate(args(value instanceof ReleaseQuotaLoginRuntimeProbe));value.onStart();
        need(Control.original==null && Control.starts==0 && Control.uiReads==0 && Control.finishCalls==1);
    }
    private static void check(String name,Runnable body) {
        String selected=System.getProperty("onx.private_create_checks");
        if(selected!=null && !Arrays.asList(selected.split(",",-1)).contains(name))return;
        need(!ran.contains(name));ran.add(name);body.run();
    }
    public static void main(String[] ignored) {
        check("phase-getter-reentry",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Bundle b=args(login);b.hook=p::onStart;
                p.onCreate(b);closed(p);need(Control.contextReads==0 && Control.targetReads==0 && b.gets==1);
            }
        });
        check("size-getter-reentry",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Bundle b=args(login);b.sizeHook=()->p.onCreate(args(login));
                p.onCreate(b);closed(p);need(b.gets==0 && Control.contextReads==0);
            }
        });
        check("context-getter-reentry",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.contextAction=p::onStart;
                p.onCreate(args(login));closed(p);need(Control.packageReads==0 && Control.targetReads==0);
            }
        });
        check("target-getter-reentry",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Control.targetAction=p::onStart;
                p.onCreate(args(login));closed(p);need(Control.packageReads==1);
            }
        });
        check("package-getter-reentry",()->{
            for(boolean login:new boolean[]{false,true})for(int index:new int[]{1,2}) {
                Instrumentation p=entry(login);Control.packageAt=index;Control.packageAction=p::onStart;
                p.onCreate(args(login));closed(p);need(Control.packageReads==index);
                if(index==1)need(Control.targetReads==0);
            }
        });
        check("private-getter-failure-aborts",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Bundle b=args(login);b.fail=true;
                p.onCreate(b);closed(p);need(Control.contextReads==0 && b.gets==0);
                p=entry(login);Control.contextAction=()->{throw new IllegalStateException("SENSITIVE");};
                p.onCreate(args(login));closed(p);need(Control.targetReads==0);
            }
        });
        check("phase-captured-once-and-success",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation p=entry(login);Bundle b=args(login);p.onCreate(b);need(b.gets==1);
                p.onStart();need(Control.exit==0 && Control.finishCalls==1 && Control.starts==1);
            }
        });
        System.out.println("{\"passed\":true,\"checks\":"+ran.size()+",\"actual_bodies\":[\""
            +String.join("\",\"",ran)+"\"],\"actual_shared_UI_body_executed\":false,\"actual_device_or_Native\":false}");
        if(System.getProperty("onx.private_gui_checks")!=null)ReleaseQuotaPrivateGuiCheck.main(new String[0]);
    }
}
