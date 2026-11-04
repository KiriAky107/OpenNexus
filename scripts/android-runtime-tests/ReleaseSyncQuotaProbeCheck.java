package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.content.Context;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

/** Actual new adaptor + original writer/signal reader, controlled Android ports. */
public final class ReleaseSyncQuotaProbeCheck {
    private static final String UNKNOWN="The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.";
    private static final String PREFIX="owned-sync-quota-"+OriginalSyncQuotaActions.PHASE+"-";
    private static int assertions;
    private static void check(boolean value){assertions++;if(!value)throw new AssertionError("Owned quota probe contract");}
    private static Object field(Object object,String name) throws Exception {
        Field f=object.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(object);
    }
    private static void field(Object object,String name,Object value) throws Exception {
        Field f=object.getClass().getDeclaredField(name);f.setAccessible(true);f.set(object,value);
    }
    private static Object call(Object object,String name,Class<?>[] types,Object... args) throws Exception {
        Method m=object.getClass().getDeclaredMethod(name,types);m.setAccessible(true);
        try{return m.invoke(object,args);}catch(InvocationTargetException error){
            if(error.getCause() instanceof Exception)throw (Exception)error.getCause();
            if(error.getCause() instanceof Error)throw (Error)error.getCause();throw error;
        }
    }
    private static AccessibilityNodeInfo node(String value){AccessibilityNodeInfo n=new AccessibilityNodeInfo(value);n.bounds=new Rect(10,10,100,30);return n;}
    private static final class Fixture {
        final File directory;
        final ReleaseSyncQuotaRuntimeProbe probe=new ReleaseSyncQuotaRuntimeProbe();
        final UiAutomation ui=new UiAutomation();final Context context;
        final AccessibilityNodeInfo root=node(""),button=node("Sync one cycle now"),alert=node(ReleaseSyncQuotaScene.QUOTA);
        final List<String> events=new ArrayList<>();int pauses;boolean malformedContext,wrongInput,reverseReady,lateScene,unknownLabel;
        Fixture(File parent,String name) throws Exception {
            directory=new File(parent,name);check(directory.mkdir());context=new Context(directory);
            Instrumentation.sampleUi=ui;Instrumentation.sampleContext=context;Instrumentation.events=events;Instrumentation.streams.clear();
            SystemClock.up=1000;SystemClock.elapsed=5000;SystemClock.upHook=null;SystemClock.elapsedHook=null;SystemClock.sleepHook=null;
            root.bounds=new Rect(0,0,1000,1000);root.add(button);root.add(node("Cycle did not complete"));root.add(node(UNKNOWN));root.add(alert);
            button.clickable=true;ui.root=root;ui.windows.add(new AccessibilityWindowInfo());
            SystemClock.upHook=()->{
                try {
                    File parentFile=new File(directory,PREFIX+"intent.json");check(parentFile.isFile());
                    check(!new File(directory,PREFIX+"cycle-start-ready.json").exists());
                    if(malformedContext){OriginalSyncQuotaActions a=(OriginalSyncQuotaActions)field(probe,"actions");Files.write(new File(directory,a.contextName()).toPath(),"private".getBytes(StandardCharsets.UTF_8));}
                }catch(Exception error){throw new AssertionError(error);}
            };
            SystemClock.sleepHook=()->{
                try {
                    OriginalSyncQuotaActions a=(OriginalSyncQuotaActions)field(probe,"actions");
                    if(!new File(directory,PREFIX+"cycle-start-ready.json").exists()) {
                        pauses++;check(!events.contains("Sync one cycle now"));
                        Files.write(new File(directory,a.contextName()).toPath(),(pauses==1?"fin":"finish\n").getBytes(StandardCharsets.UTF_8));
                    } else {
                        check(pauses==2);
                        Files.write(new File(directory,a.inputFinishName()).toPath(),(wrongInput?"wrong!!":"finish\n").getBytes(StandardCharsets.UTF_8));
                    }
                }catch(Exception error){throw new AssertionError(error);}
            };
            SystemClock.elapsedHook=()->{
                if(reverseReady)return -1;
                if(lateScene && !button.enabled)return 42000;
                return SystemClock.elapsed;
            };
        }
        void run() throws Exception {probe.additionalPhase(OriginalSyncQuotaActions.PHASE);}
        void refused() throws Exception {
            try{run();throw new AssertionError("Expected quota refusal");}catch(IllegalStateException expected){}
            check(!new File(directory,PREFIX+"failed-ready.json").exists());
            int reads=ui.rootReads;
            try{probe.nodes();throw new AssertionError();}catch(IllegalStateException closed){}
            check(ui.rootReads==reads && probe.productUiObservationClosed());
        }
    }
    public static void main(String[] args) throws Exception {
        File parent=new File(args[0]);check(parent.isDirectory());
        for(String phase:new String[]{"login-a","cycle-timeout-original","",OriginalSyncQuotaActions.PHASE}) {
            ReleaseSyncQuotaRuntimeProbe probe=new ReleaseSyncQuotaRuntimeProbe();
            Bundle bundle=new Bundle();bundle.putString("phase",phase);
            if(OriginalSyncQuotaActions.PHASE.equals(phase))bundle.putString("private","forbidden");
            int reads=Instrumentation.sampleUi==null?0:Instrumentation.sampleUi.rootReads;
            probe.onCreate(bundle);check(probe.starts==0 && probe.finishes==1 && probe.exitCode==1);
            check(Instrumentation.sampleUi==null || Instrumentation.sampleUi.rootReads==reads);
        }
        ReleaseSyncQuotaRuntimeProbe admitted=new ReleaseSyncQuotaRuntimeProbe();Bundle argsOnly=new Bundle();argsOnly.putString("phase",OriginalSyncQuotaActions.PHASE);
        admitted.onCreate(argsOnly);check(admitted.starts==1 && admitted.finishes==0 && admitted.productUiObservationClosed());
        Fixture good=new Fixture(parent,"success");
        for(int kind=0;kind<5;kind++){
            AccessibilityNodeInfo privateNode=node("never export");privateNode.poison=true;
            if(kind==0)privateNode.password=true;else if(kind==1)privateNode.editable=true;
            else if(kind==2)privateNode.visible=false;else if(kind==3)privateNode.packageName="foreign";else privateNode.window=8;
            good.root.add(privateNode);
        }
        good.run();check(good.pauses==2);
        check(good.events.size()==4 && good.events.get(0).equals("read-current") && good.events.get(1).equals("signed-in")
            && good.events.get(2).equals("Read queue and original records") && good.events.get(3).equals("Sync one cycle now"));
        for(AccessibilityNodeInfo n:good.root.children)if(n.poison)check(n.textReads==0 && n.descriptionReads==0);
        check(good.alert.textReads==1 && good.alert.descriptionReads==1);
        check(Instrumentation.streams.size()==1 && Instrumentation.streams.get(0).equals(
            "OPENNEXUS_SIGNED_QUOTA_UI input_finished=true quota_error_UI_observed=true raw_UI_recorded=false native_original_state_verified=false\n"));
        File parentFile=new File(good.directory,PREFIX+"intent.json"),readyFile=new File(good.directory,PREFIX+"cycle-start-ready.json"),failedFile=new File(good.directory,PREFIX+"failed-ready.json");
        check(parentFile.isFile() && readyFile.isFile() && failedFile.isFile());
        int reads=good.ui.rootReads;try{good.probe.nodes();throw new AssertionError();}catch(IllegalStateException closed){}check(reads==good.ui.rootReads);
        Fixture existing=new Fixture(parent,"existing");File old=new File(existing.directory,PREFIX+"intent.json");Files.write(old.toPath(),new byte[]{1,2,3});existing.refused();
        check(java.util.Arrays.equals(Files.readAllBytes(old.toPath()),new byte[]{1,2,3}) && existing.events.isEmpty());
        Fixture wrong=new Fixture(parent,"context-unknown");wrong.malformedContext=true;wrong.refused();check(!wrong.events.contains("Sync one cycle now"));
        Fixture finish=new Fixture(parent,"finish-unknown");finish.wrongInput=true;finish.refused();
        Fixture backwards=new Fixture(parent,"backwards");backwards.reverseReady=true;backwards.refused();check(!new File(backwards.directory,PREFIX+"cycle-start-ready.json").exists());
        Fixture late=new Fixture(parent,"late-scene");late.lateScene=true;late.refused();check(late.alert.textReads==0);
        Fixture unknown=new Fixture(parent,"unknown-channel");unknown.alert.poison=true;
        try{unknown.run();throw new AssertionError();}catch(AssertionError expected){check(expected.getMessage().equals("Private text read"));}
        check(!new File(unknown.directory,PREFIX+"failed-ready.json").exists());
        int unknownReads=unknown.ui.rootReads;try{unknown.probe.nodes();throw new AssertionError();}catch(IllegalStateException closed){}check(unknown.ui.rootReads==unknownReads);
        Fixture ime=new Fixture(parent,"ime");AccessibilityWindowInfo keyboard=new AccessibilityWindowInfo();keyboard.id=9;keyboard.type=AccessibilityWindowInfo.TYPE_INPUT_METHOD;ime.ui.windows.add(keyboard);ime.refused();
        check(!ime.events.contains("Sync one cycle now"));
        Fixture scan=new Fixture(parent,"scan-gates");
        OriginalSyncQuotaActions a=new OriginalSyncQuotaActions(OriginalSyncQuotaActions.PHASE,"11111111-1111-1111-1111-111111111111","22222222-2222-2222-2222-222222222222");
        field(scan.probe,"actions",a);field(scan.probe,"originalWindow",7);field(scan.probe,"originalUid",12001);
        a.awaitContext(()->{},()->a.contextSample(a.contextName(),true),()->0L,n->{});a.begin("cycle-start");a.ready(5000);
        a.awaitInputFinish(()->{},()->a.inputFinishSample(a.inputFinishName(),true),()->5000L,n->{});
        scan.button.enabled=false;scan.alert.description="conflicting private channel";
        OriginalSyncQuotaActions.SceneSample sample=(OriginalSyncQuotaActions.SceneSample)call(scan.probe,"readScene",new Class<?>[0]);
        try{a.awaitFailure(()->{},()->sample,()->5000L,n->{throw new IllegalStateException("No fallback scan");});throw new AssertionError();}
        catch(IllegalStateException expected){check(expected.getMessage().equals("No fallback scan"));}
        check(a.lastScene()[3]==0 && scan.alert.textReads==1 && scan.alert.descriptionReads==1);
        Fixture wrongRoot=new Fixture(parent,"wrong-root");field(wrongRoot.probe,"actions",a);field(wrongRoot.probe,"originalWindow",7);
        // Use a fresh valid action instance; an UNKNOWN kernel must refuse even
        // before reading a root, not supply facts for this separate case.
        OriginalSyncQuotaActions fresh=new OriginalSyncQuotaActions(OriginalSyncQuotaActions.PHASE,"33333333-3333-3333-3333-333333333333","44444444-4444-4444-4444-444444444444");
        fresh.awaitContext(()->{},()->fresh.contextSample(fresh.contextName(),true),()->0L,n->{});fresh.begin("cycle-start");fresh.ready(5000);
        fresh.awaitInputFinish(()->{},()->fresh.inputFinishSample(fresh.inputFinishName(),true),()->5000L,n->{});
        field(wrongRoot.probe,"actions",fresh);wrongRoot.root.window=8;
        try{call(wrongRoot.probe,"readScene",new Class<?>[0]);throw new AssertionError();}catch(IllegalStateException expected){}
        check(wrongRoot.root.textReads==0 && wrongRoot.alert.textReads==0);
        Fixture partial=new Fixture(parent,"partial-traversal");field(partial.probe,"actions",fresh);field(partial.probe,"originalWindow",7);
        for(int i=0;i<2500;i++){AccessibilityNodeInfo n=node("never read");n.poison=true;partial.root.add(n);}
        try{call(partial.probe,"readScene",new Class<?>[0]);throw new AssertionError();}catch(IllegalStateException expected){}
        check(partial.root.textReads==0 && partial.alert.textReads==0);
        String vector="{\"parent\":"+new String(Files.readAllBytes(parentFile.toPath()),StandardCharsets.UTF_8)
            +",\"ready\":"+new String(Files.readAllBytes(readyFile.toPath()),StandardCharsets.UTF_8)
            +",\"failed\":"+new String(Files.readAllBytes(failedFile.toPath()),StandardCharsets.UTF_8)+"}";
        System.out.println("{\"passed\":true,\"assertions\":"+assertions+",\"actual_Android_UI_or_Native\":false,\"synthetic_records\":"+vector+"}");
    }
}
