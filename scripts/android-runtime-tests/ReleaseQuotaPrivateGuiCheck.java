package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.os.Bundle;
import java.util.ArrayList;
import java.util.List;

/** Executes actual new entries on controlled Android/base ports, never a device. */
public final class ReleaseQuotaPrivateGuiCheck {
    private static final List<String> ran=new ArrayList<>();
    private static void need(boolean value) {if(!value)throw new AssertionError("Controlled private GUI contract failed");}
    private static Bundle args(boolean login) {Bundle b=new Bundle();b.putString("phase",login?"login-a":"markdown");return b;}
    private static Instrumentation entry(boolean login) {
        Control.reset();return login?new ReleaseQuotaLoginRuntimeProbe():new ReleaseQuotaSeedRuntimeProbe();
    }
    private static void create(Instrumentation value,boolean login) {value.onCreate(args(login));}
    private static void privateOnly() {
        for(String value:Control.streams) need(!value.contains("SENSITIVE") && !value.contains("RAW_TREE")
            && !value.contains("stack") && !value.contains("endpoint") && !value.contains("password"));
    }
    private static void failed() {need(Control.finishCalls==1 && Control.exit==1);privateOnly();}
    private static void noLateUi(Instrumentation value) {
        int reads=Control.uiReads,finishes=Control.finishCalls,statuses=Control.statusCalls;
        value.onStart();value.onCreate(args(value instanceof ReleaseQuotaLoginRuntimeProbe));
        Bundle b=new Bundle();b.putString("stream","SENSITIVE late");value.sendStatus(1,b);value.finish(0,b);
        need(Control.uiReads==reads && Control.finishCalls==finishes && Control.statusCalls==statuses);
        try {
            java.lang.reflect.Method method=value.getClass().getDeclaredMethod("nodes");method.setAccessible(true);
            method.invoke(value);throw new AssertionError("Closed nodes returned");
        } catch(java.lang.reflect.InvocationTargetException expected) {
            need(expected.getCause() instanceof IllegalStateException);
        } catch(ReflectiveOperationException error) {throw new AssertionError(error);}
        need(Control.uiReads==reads);
    }
    private static void check(String name,Runnable body) {
        need(!ran.contains(name));ran.add(name);body.run();
    }
    public static void main(String[] ignored) {
        check("constructors-zero-ui",()->{
            entry(false);need(Control.uiReads==0 && Control.starts==0 && Control.finishCalls==0);
            entry(true);need(Control.uiReads==0 && Control.starts==0 && Control.finishCalls==0);
        });
        check("strict-arguments",()->{
            for(boolean login:new boolean[]{false,true})for(int mode=0;mode<6;mode++) {
                Instrumentation value=entry(login);Bundle b=args(login);
                if(mode==0)b=null;
                if(mode==1)b=new Bundle();
                if(mode==2)b.putString("phase",login?"refresh":"canvas");
                if(mode==3)b.put("phase",Integer.valueOf(1));
                if(mode==4)b.putString("private-argument","SENSITIVE");
                if(mode==5)b.put("phase",new Object(){public String toString(){throw new AssertionError("Coercion");}});
                value.onCreate(b);failed();need(Control.starts==0 && Control.uiReads==0);noLateUi(value);
            }
        });
        check("own-driver-context-and-target",()->{
            for(boolean login:new boolean[]{false,true})for(boolean target:new boolean[]{false,true}) {
                Instrumentation value=entry(login);
                if(target)Control.target="cc.kronecker.foreign";else Control.context="cc.kronecker.foreign";
                create(value,login);failed();need(Control.starts==0 && Control.uiReads==0);
            }
        });
        check("argument-alias-isolated",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Bundle caller=args(login);value.onCreate(caller);
                caller.putString("phase",login?"logout":"canvas");caller.putString("secret","SENSITIVE");
                need(Control.original!=caller && Control.original.size()==1);
                value.onStart();need(Control.exit==0 && Control.finishCalls==1);privateOnly();
            }
        });
        check("seed-success-static-and-late-closed",()->{
            Instrumentation value=entry(false);create(value,false);value.onStart();
            need(Control.starts==1 && Control.finishCalls==1 && Control.exit==0 && Control.statusCalls==0);
            need(Control.streams.size()==1 && Control.streams.get(0).equals(Control.SEED_OK));
            need("SIGNED_QUOTA_SEED".equals(Control.namespace));privateOnly();noLateUi(value);
        });
        check("login-success-ui-only-and-no-post-result-edit",()->{
            Instrumentation value=entry(true);create(value,true);value.onStart();
            need(Control.finishCalls==1 && Control.exit==0 && Control.statusCalls==1 && Control.signedReads==1);
            need(Control.streams.size()==2 && Control.streams.get(0).equals(ReleaseQuotaLoginRuntimeProbe.UI)
                && Control.streams.get(1).equals(ReleaseQuotaLoginRuntimeProbe.OK));
            need(Control.passwordEdits==0 && Control.shapeReads==0);privateOnly();noLateUi(value);
        });
        check("prephase-failure-no-ui-recovery",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Control.mode="pre-fail";create(value,login);value.onStart();
                failed();need(Control.passwordEdits==0 && Control.shapeReads==0 && Control.statusCalls==0);noLateUi(value);
            }
        });
        check("private-malformed-result-never-exported",()->{
            for(boolean login:new boolean[]{false,true})for(String mode:new String[]{"raw","extra","object","missing","throw-bundle"}) {
                Instrumentation value=entry(login);Control.mode=mode;create(value,login);value.onStart();failed();noLateUi(value);
            }
        });
        check("unexpected-status-closes-permission",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Control.mode="raw-status";create(value,login);value.onStart();
                failed();need(Control.statusCalls==0);noLateUi(value);
            }
        });
        check("success-before-original-ui-refused",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Bundle result=new Bundle();
                result.putString("stream",login?ReleaseQuotaLoginRuntimeProbe.OK:Control.SEED_OK);
                if(!login)result.putString("phase","markdown");
                value.finish(0,result);failed();need(Control.uiReads==0 && Control.starts==0);noLateUi(value);
            }
        });
        check("duplicate-create-and-early-start",()->{
            for(boolean login:new boolean[]{false,true})for(boolean early:new boolean[]{false,true}) {
                Instrumentation value=entry(login);
                if(early)value.onStart();else {create(value,login);create(value,login);}
                failed();need(Control.uiReads==0);noLateUi(value);
            }
        });
        check("reentrant-base-work-revokes",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Control.action=value::onStart;create(value,login);value.onStart();
                failed();need(Control.passwordEdits==0 && Control.shapeReads==0);noLateUi(value);
            }
        });
        check("reentrant-result-getter-cannot-complete",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Control.mode="reenter-result";
                Control.resultAction=value::onStart;create(value,login);value.onStart();failed();noLateUi(value);
            }
        });
        check("duplicate-login-ui-and-status-refused",()->{
            for(String mode:new String[]{"double-signed","double-status"}) {
                Instrumentation value=entry(true);Control.mode=mode;create(value,true);value.onStart();
                failed();need(Control.signedReads==1 && Control.statusCalls==1);noLateUi(value);
            }
        });
        check("framework-finish-or-status-unknown-never-retried",()->{
            for(boolean login:new boolean[]{false,true}) {
                Instrumentation value=entry(login);Control.throwFinish=true;create(value,login);value.onStart();
                need(Control.finishCalls==1);privateOnly();noLateUi(value);
            }
            Instrumentation value=entry(true);Control.throwStatus=true;create(value,true);value.onStart();
            failed();need(Control.statusCalls==1);noLateUi(value);
        });
        StringBuilder names=new StringBuilder("[");for(String name:ran){if(names.length()>1)names.append(',');names.append('"').append(name).append('"');}
        System.out.println("{\"passed\":true,\"checks\":"+ran.size()+",\"actual_bodies\":"+names+"]"
            +",\"controlled_Android_and_base\":true,\"actual_shared_UI_body_executed\":false,\"actual_device_or_Native\":false}");
    }
}
