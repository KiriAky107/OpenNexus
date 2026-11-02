package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.content.Context;
import android.graphics.Rect;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.File;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

public final class ReleaseSyncTimeoutPreinputNodeCheck {
    private static int assertions;
    private static final String FOOTER="A successful cycle does not mean the queue is empty. Receiving download bytes does not mean verification succeeded; review conflicts separately.";
    private static void check(boolean value) {assertions++;if(!value)throw new AssertionError("Owned preinput node contract");}
    private static void field(Object value,String name,Object data) throws Exception {
        Field f=value.getClass().getDeclaredField(name);f.setAccessible(true);f.set(value,data);
    }
    private static Object call(Object value,String name,Class<?>[] types,Object... args) throws Exception {
        Method method=value.getClass().getDeclaredMethod(name,types);method.setAccessible(true);
        try {return method.invoke(value,args);}catch(InvocationTargetException error) {
            if(error.getCause() instanceof Exception)throw (Exception)error.getCause();throw error;
        }
    }
    private static final class Fixture {
        final ReleaseSyncTimeoutRuntimeProbe probe=new ReleaseSyncTimeoutRuntimeProbe();
        final UiAutomation ui=new UiAutomation();final Context context;
        final AccessibilityNodeInfo root=node("",0,0,1000,1000),button=node("Sync one cycle now",10,10,20,20),
            section=node("Sync details",10,30,900,900),footer=node(FOOTER,10,800,900,900);
        Fixture(File directory) throws Exception {
            context=new Context(directory);Instrumentation.sampleContext=context;Instrumentation.sampleUi=ui;
            Field active=AuthRuntimeProbe.class.getDeclaredField("ui");active.setAccessible(true);active.set(probe,ui);
            field(probe,"actions",new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.TIMEOUT,
                "11111111-1111-1111-1111-111111111111","22222222-2222-2222-2222-222222222222"));
            root.className="android.webkit.WebView";root.scrollable=true;root.add(button);root.add(section);section.add(footer);button.clickable=true;
            ui.root=root;ui.windows.add(new AccessibilityWindowInfo());SystemClock.time=1000;
        }
        OriginalSyncTimeoutPreinputFlow.Frame frame() throws Exception {
            return (OriginalSyncTimeoutPreinputFlow.Frame)call(probe,"preinputFrame",new Class<?>[0]);
        }
        boolean reveal() throws Exception {return (Boolean)call(probe,"revealCurrentSection",new Class<?>[]{long.class,long.class},1000L,6000L);}
    }
    private static AccessibilityNodeInfo node(String value,int l,int t,int r,int b) {
        AccessibilityNodeInfo node=new AccessibilityNodeInfo(value);node.bounds=new Rect(l,t,r,b);return node;
    }
    private static void refused(Fixture f) throws Exception {
        try {f.frame();throw new AssertionError();}catch(IllegalStateException expected) {check(f.section.actionCalls==0);}
    }
    public static void main(String[] args) throws Exception {
        File root=new File(args[0]);Fixture f=new Fixture(root);
        OriginalSyncTimeoutPreinputFlow.Frame frame=f.frame();
        check(frame.uid==12001 && frame.window==7 && frame.section[3]==900 && frame.sectionClip[3]==1000);
        check(f.section.textReads==1 && f.section.descriptionReads==1 && f.button.textReads==1);
        AccessibilityNodeInfo editable=node("",0,0,1,1);editable.editable=true;editable.poison=true;f.root.add(editable);
        AccessibilityNodeInfo password=node("",0,0,1,1);password.password=true;password.poison=true;f.root.add(password);
        f.frame();check(editable.textReads==0 && password.textReads==0);
        field(f.probe,"preinputClosed",true);int rootReads=f.ui.rootReads;
        refused(f);check(f.ui.rootReads==rootReads);
        f=new Fixture(root);f.section.children.clear();refused(f); // A label alone is not the whole section.
        f=new Fixture(root);f.root.children.remove(f.section);f.root.add(f.footer);f.root.add(node("Sync details",10,30,900,900));refused(f);
        f=new Fixture(root);AccessibilityNodeInfo second=node("Sync details",10,30,900,900);second.add(node(FOOTER,10,800,900,900));f.root.add(second);refused(f);
        f=new Fixture(root);f.section.description="private conflicting channel";refused(f);
        f=new Fixture(root);AccessibilityWindowInfo ime=new AccessibilityWindowInfo();ime.id=8;ime.type=AccessibilityWindowInfo.TYPE_INPUT_METHOD;f.ui.windows.add(ime);refused(f);
        f=new Fixture(root);f.frame();f.context.appUid++;refused(f);
        f=new Fixture(root);f.section.bounds.bottom=1100;f.section.visible=false;f.frame();f.section.actionResult=false;
        check(!f.reveal() && f.section.actionCalls==1);
        try {f.reveal();throw new AssertionError();}catch(IllegalStateException repeated) {check(f.section.actionCalls==1);}
        f=new Fixture(root);f.section.bounds.bottom=1100;f.frame();f.section.bounds.bottom++;
        try {f.reveal();throw new AssertionError();}catch(IllegalStateException moved) {check(f.section.actionCalls==0);}
        f=new Fixture(root);f.section.bounds.bottom=1100;f.frame();SystemClock.time=6001;
        try {f.reveal();throw new AssertionError();}catch(IllegalStateException late) {check(f.section.actionCalls==0);}
        f=new Fixture(root);AccessibilityWindowInfo overlay=new AccessibilityWindowInfo();overlay.id=8;overlay.layer=3;overlay.bounds=new Rect(0,0,1000,1000);f.ui.windows.add(overlay);refused(f);
        f=new Fixture(root);f.section.visible=false;refused(f); // Invisible but allegedly fitting is unknown.
        System.out.println("{\"passed\":true,\"assertions\":"+assertions+",\"actual_Android_UI_or_Native\":false}");
    }
}
