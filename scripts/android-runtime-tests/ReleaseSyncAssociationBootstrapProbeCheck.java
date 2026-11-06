package cc.kronecker.opennexus.testdriver;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.content.Context;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/** New producer and preview helper execute; Base/Android/JSON remain controlled. */
public final class ReleaseSyncAssociationBootstrapProbeCheck {
    private static final String VAULT="1234567890abcdef1234567890abcdef";
    private static final String ID="12345678-1234-1234-1234-123456789abc";
    private static final String SHA="a".repeat(64);
    private static int assertions,cases;
    private static void check(boolean value){assertions++;if(!value)throw new AssertionError("Association assertion "+assertions);}
    private static AccessibilityNodeInfo node(String value){return new AccessibilityNodeInfo(value);}
    private static Bundle phase(){Bundle b=new Bundle();b.putString("phase",ReleaseSyncAssociationBootstrapRuntimeProbe.PHASE);return b;}
    private static final class Fixture implements AutoCloseable {
        final File directory,intent;
        final ReleaseSyncAssociationBootstrapRuntimeProbe probe=new ReleaseSyncAssociationBootstrapRuntimeProbe();
        final UiAutomation ui=new UiAutomation();
        final AccessibilityNodeInfo preview=node(""),confirmPreview=node("Confirm merge and bind"),review=node(""),finalButton=node("Confirm this association");
        final List<String> events=new ArrayList<>();
        boolean failFinal;
        Fixture(File parent,String name)throws Exception {
            cases++;directory=new File(parent,name);check(directory.mkdir());intent=new File(directory,"owned-sync-association-intent.json");
            Instrumentation.sampleUi=ui;Instrumentation.sampleContext=new Context(directory);Instrumentation.sampleTarget=Instrumentation.sampleContext;
            Instrumentation.events=events;Instrumentation.streams=new ArrayList<>();
            SyncRuntimeProbe.prePhaseFailure=false;SyncRuntimeProbe.passwordEdits=0;SyncRuntimeProbe.failureShapeReads=0;
            SystemClock.time=1000;SystemClock.sleeps=0;SystemClock.sleepHook=null;
            config("{\"vault_id\":\""+VAULT+"\",\"vault_name\":\"Owned Android Sync "+VAULT+"\"}");
            preview.add(node("Initial merge preview"));preview.add(confirmPreview);ui.root=preview;
            review.add(node("Original ID"));review.add(node("Preview digest"));review.add(node(VAULT));
            review.add(node(ID));review.add(node(SHA));review.add(finalButton);
            SyncRuntimeProbe.clickHook=nameClicked->{
                if(nameClicked.equals("Confirm merge and bind"))ui.root=review;
                if(nameClicked.equals("Confirm this association")) {
                    try {
                        check(intent.isFile());String raw=Files.readString(intent.toPath(),StandardCharsets.UTF_8);
                        JSONObject value=new JSONObject(raw);check(value.length()==3);
                        check(value.getString("operation_id").equals(ID));check(value.getString("preview_fingerprint").equals(SHA));
                        check(value.getString("remote_vault").equals(VAULT));check(Files.size(intent.toPath())<=2048);
                    }catch(Exception error){throw new AssertionError(error);}
                    if(failFinal)throw new IllegalStateException("synthetic-private-confirm-error");
                    ui.root=node("Associated remote vault · "+VAULT);
                }
            };
        }
        void config(String raw)throws Exception{Files.writeString(new File(directory,"owned-sync.json").toPath(),raw,StandardCharsets.UTF_8);}
        void run(){probe.onCreate(phase());probe.onStart();}
        long clicks(String name){return events.stream().filter(name::equals).count();}
        void closed(){int reads=ui.rootReads;try{probe.nodes();throw new AssertionError("Closed observation accepted");}catch(IllegalStateException expected){}
            check(ui.rootReads==reads);check(probe.productUiObservationClosed());check(SyncRuntimeProbe.passwordEdits==0&&SyncRuntimeProbe.failureShapeReads==0);}
        void failed(){check(probe.exitCode==1);check(clicks("Confirm this association")==0);check(Instrumentation.streams.stream().noneMatch(s->s.contains("_UI association_confirmed")));closed();}
        @Override public void close()throws Exception {
            SyncRuntimeProbe.clickHook=null;SystemClock.sleepHook=null;
            File[] files=directory.listFiles();check(files!=null);
            for(File file:files){check(file.getParentFile().getCanonicalFile().equals(directory.getCanonicalFile()));
                check(!Files.isSymbolicLink(file.toPath())&&file.isFile());check(file.delete());}
            check(directory.delete());
        }
    }
    public static void main(String[] args)throws Exception {
        File parent=new File(args[0]);check(parent.isAbsolute()&&parent.isDirectory());
        try(Fixture f=new Fixture(parent,"success")) {
            f.run();check(f.probe.starts==1&&f.probe.finishes==1&&f.probe.exitCode==0);
            check(f.clicks("Confirm merge and bind")==1&&f.clicks("Confirm this association")==1);
            check(Instrumentation.streams.equals(List.of(ReleaseSyncAssociationBootstrapRuntimeProbe.UI_LINE,
                "OPENNEXUS_SIGNED_ASSOCIATION_BOOTSTRAP_PHASE_OK associate-bootstrap-signed-original\n")));
            check(Instrumentation.streams.stream().noneMatch(s->s.contains(VAULT)||s.contains(ID)||s.contains(SHA)||s.contains("PREVIEW_OBSERVATION")));
            check(f.events.equals(List.of("open-connection","read-current","signed-in","Read remote vaults","Choose a vault",
                "Owned Android Sync "+VAULT+" · 0","Preview merge","Confirm merge and bind","Confirm this association")));
            f.closed();int size=f.events.size();f.probe.onStart();check(size==f.events.size());
            try{f.probe.additionalPhase(ReleaseSyncAssociationBootstrapRuntimeProbe.PHASE);throw new AssertionError("Replay accepted");}catch(IllegalStateException expected){}
            check(size==f.events.size());
        }
        List<Bundle> invalid=new ArrayList<>();invalid.add(null);invalid.add(new Bundle());
        Bundle wrong=phase();wrong.putString("phase","associate");invalid.add(wrong);
        Bundle extra=phase();extra.putString("vault_id",VAULT);invalid.add(extra);
        Bundle number=new Bundle();number.putInt("phase",1);invalid.add(number);
        Bundle bool=new Bundle();bool.putBoolean("phase",true);invalid.add(bool);
        for(int i=0;i<invalid.size();i++)try(Fixture f=new Fixture(parent,"invalid"+i)) {
            f.probe.onCreate(invalid.get(i));check(f.probe.starts==0&&f.probe.exitCode==1);check(f.ui.rootReads==0&&f.events.isEmpty());f.closed();
        }
        try(Fixture f=new Fixture(parent,"noCreate")){f.probe.onStart();f.failed();check(f.ui.rootReads==0);}
        try(Fixture f=new Fixture(parent,"duplicateCreate")){f.probe.onCreate(phase());f.probe.onCreate(phase());check(f.probe.starts==1);f.probe.onStart();f.failed();check(f.events.isEmpty());}
        for(int target=0;target<2;target++)try(Fixture f=new Fixture(parent,"wrongOwner"+target)) {
            Context other=new Context(f.directory){@Override public String getPackageName(){return "synthetic.other";}};
            if(target==0)Instrumentation.sampleContext=other;else Instrumentation.sampleTarget=other;
            f.probe.onCreate(phase());check(f.probe.starts==0);f.failed();check(f.ui.rootReads==0);
        }
        try(Fixture f=new Fixture(parent,"prePhaseFailure")) {
            SyncRuntimeProbe.prePhaseFailure=true;f.run();f.failed();check(f.events.equals(List.of("open-connection")));
            check(Instrumentation.streams.stream().noneMatch(s->s.contains("synthetic-private")));check(!f.intent.exists());
        }
        try(Fixture f=new Fixture(parent,"postIntentFailure")) {
            f.failFinal=true;f.run();check(f.probe.exitCode==1&&f.intent.isFile());check(f.clicks("Confirm this association")==1);f.closed();
            f.probe.onStart();check(f.clicks("Confirm this association")==1);check(Instrumentation.streams.stream().noneMatch(s->s.contains("synthetic-private")));
        }
        try(Fixture f=new Fixture(parent,"existingIntent")) {
            Files.writeString(f.intent.toPath(),"partial",StandardCharsets.UTF_8);f.run();f.failed();check(Files.readString(f.intent.toPath()).equals("partial"));
        }
        for(int type=0;type<4;type++)try(Fixture f=new Fixture(parent,"badConfig"+type)) {
            if(type==0)f.config("{\"vault_id\":\"wrong\",\"vault_name\":\"Owned Android Sync "+VAULT+"\"}");
            if(type==1)f.config("{\"vault_id\":\""+VAULT+"\"}");
            if(type==2)f.config(" ".repeat(2049));
            if(type==3)check(new File(f.directory,"owned-sync.json").delete());
            f.run();f.failed();check(!f.intent.exists());check(!f.events.contains("Read remote vaults"));
        }
        try(Fixture f=new Fixture(parent,"previewPending")) {
            f.confirmPreview.enabled=false;SystemClock.sleepHook=()->f.confirmPreview.enabled=true;
            f.run();check(f.probe.exitCode==0&&SystemClock.sleeps==1&&SystemClock.time==1150);f.closed();
            check(Instrumentation.streams.size()==2);
        }
        for(int type=0;type<4;type++)try(Fixture f=new Fixture(parent,"badPreview"+type)) {
            if(type==0)f.preview.add(node("Failure (SYNC_NETWORK_ERROR)"));
            if(type==1)f.confirmPreview.packageName="synthetic.other";
            if(type==2)f.ui.root=null;
            if(type==3)for(int n=0;n<2497;n++)f.preview.add(node(""));
            f.run();f.failed();check(!f.intent.exists());check(f.clicks("Confirm merge and bind")==0);
        }
        for(int type=0;type<5;type++)try(Fixture f=new Fixture(parent,"badReview"+type)) {
            if(type==0)f.review.add(node("22345678-1234-1234-1234-123456789abc"));
            if(type==1)f.review.add(node("b".repeat(64)));
            if(type==2){AccessibilityNodeInfo foreign=node("private");foreign.packageName="synthetic.other";f.review.add(foreign);}
            if(type==3){AccessibilityNodeInfo password=node("private");password.password=true;f.review.add(password);}
            if(type==4)for(int n=0;n<121;n++)f.review.add(node(""));
            f.run();f.failed();check(!f.intent.exists());check(f.clicks("Confirm merge and bind")==1);
        }
        File[] remaining=parent.listFiles();check(remaining!=null&&remaining.length==0);
        System.out.println("{\"passed\":true,\"assertions\":"+assertions+",\"cases\":"+cases+
            ",\"remaining_case_entries\":0,\"actual_Android_UI_or_Native\":false}");
    }
}
