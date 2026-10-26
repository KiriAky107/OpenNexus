package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Real state transitions, private files and inherited cleanup; device operations remain unverified. */
public final class OriginalSyncProcessActionsCheck {
    private static final String P="20000000-0000-0000-0000-000000000001",A="20000000-0000-0000-0000-000000000002";
    private static int checks;
    interface Task {void run() throws Exception;}
    private static void need(boolean value){if(!value)throw new AssertionError("original process boundary changed");}
    private static void rejects(Task action) throws Exception {
        try{action.run();}catch(IllegalStateException expected){checks++;return;}
        throw new AssertionError("invalid process sequence accepted");
    }
    private static OriginalSyncProcessActions loss(){return new OriginalSyncProcessActions(OriginalSyncProcessActions.LOSS,P,A);}
    private static void bytes(File file,String value) throws Exception {Files.write(file.toPath(),value.getBytes(StandardCharsets.UTF_8));}
    private static void invoke(Method method,Object target) throws Exception {
        try{method.invoke(target);}catch(InvocationTargetException error) {
            if(error.getCause() instanceof IllegalStateException)throw (IllegalStateException)error.getCause();
            throw error;
        }
    }
    public static void main(String[] ignored) throws Exception {
        for(String phase:new String[]{null,"","cycle-stop-original","cycle-process-loss-original\n"})
            rejects(()->new OriginalSyncProcessActions(phase,P,A));
        for(String[] ids:new String[][]{{P,P},{P,null},{P,"NOT-A-UUID"},{null,A}})
            rejects(()->new OriginalSyncProcessActions(OriginalSyncProcessActions.LOSS,ids[0],ids[1]));
        OriginalSyncProcessActions s=loss();rejects(s::authorized);rejects(s::closeProductUi);rejects(s::released);rejects(s::complete);
        rejects(()->s.begin("cycle-stop"));need(A.equals(s.begin("cycle-start")));checks++;
        rejects(()->s.begin("cycle-start"));rejects(()->s.finished("cycle-resume"));rejects(s::authorized);
        s.finished("cycle-start");rejects(()->s.finished("cycle-start"));rejects(s::complete);rejects(s::closeProductUi);
        s.beforeProductUi();s.authorized();rejects(s::authorized);rejects(s::released);s.beforeProductUi();
        s.closeProductUi();rejects(s::closeProductUi);rejects(s::beforeProductUi);rejects(s::complete);
        s.released();rejects(s::released);s.complete();s.close();rejects(s::beforeProductUi);
        need(s.productUiClosed() && s.summary().contains("process_absence_verified=false")
            && s.summary().contains("host_release_signal_observed=true") && s.summary().contains("native_cycle_acknowledged=false"));checks++;
        rejects(s::complete);rejects(()->s.begin("cycle-start"));
        OriginalSyncProcessActions resume=new OriginalSyncProcessActions(OriginalSyncProcessActions.RESUME,P,A);
        rejects(()->resume.begin("cycle-start"));resume.begin("cycle-resume");resume.finished("cycle-resume");
        rejects(resume::authorized);rejects(resume::closeProductUi);rejects(resume::released);resume.complete();
        need(!resume.productUiClosed());checks++;
        for(int boundary=0;boundary<5;boundary++) {
            OriginalSyncProcessActions unknown=loss();
            if(boundary>=1)unknown.begin("cycle-start");
            if(boundary>=2)unknown.finished("cycle-start");
            if(boundary>=3)unknown.authorized();
            if(boundary>=4)unknown.closeProductUi();
            unknown.close();rejects(unknown::authorized);rejects(unknown::released);rejects(unknown::complete);
            rejects(()->unknown.begin("cycle-start"));need(unknown.productUiClosed()==(boundary==4));checks++;
        }
        Path root=Files.createTempDirectory("opennexus-process-signals-");
        try {
            File expected=root.resolve("expect-loss-"+P+".finish").toFile(),released=root.resolve("released-"+P+".finish").toFile();
            File wrongParent=root.resolve("expect-loss-"+A+".finish").toFile();bytes(wrongParent,"finish\n");
            long[] clock={100};int[] guard={0};
            OriginalSyncProcessActions waiting=loss();waiting.begin("cycle-start");waiting.finished("cycle-start");
            rejects(()->OriginalSyncStopActions.await(expected,waiting::beforeProductUi,()->clock[0],delay->clock[0]+=delay,100));
            need(!waiting.productUiClosed());checks++;
            clock[0]=100;int[] pauses={0};
            OriginalSyncStopActions.await(expected,()->{waiting.beforeProductUi();guard[0]++;},()->clock[0],delay->{
                clock[0]+=delay;try{bytes(expected,++pauses[0]==1?"fin":"finish\n");}catch(Exception error){throw new AssertionError(error);}
            },200);
            need(guard[0]==4 && pauses[0]==2);checks++;waiting.authorized();waiting.closeProductUi();
            // A complete stale/wrong-parent signal cannot stand in for release.
            clock[0]=100;rejects(()->OriginalSyncStopActions.await(released,()->{},()->clock[0],delay->clock[0]+=delay,100));
            need(!waiting.summary().contains("host_release_signal_observed=true"));checks++;
            bytes(released,"finish!");rejects(()->OriginalSyncStopActions.await(released,()->{},()->100L,delay->{},100));
            bytes(released,"finish\n");
            rejects(()->OriginalSyncStopActions.await(released,waiting::beforeProductUi,()->100L,delay->{},100));
            int[] selfGuard={0};OriginalSyncStopActions.await(released,()->selfGuard[0]++,()->100L,delay->{},100);
            need(selfGuard[0]==2);checks++;waiting.released();waiting.complete();
            // Actual inherited cleanup and error reporting must stop before AX
            // after this boundary. The fixture UI throws on every observation.
            ReleaseSyncProcessRuntimeProbe entry=new ReleaseSyncProcessRuntimeProbe();
            Field actions=ReleaseSyncProcessRuntimeProbe.class.getDeclaredField("actions");actions.setAccessible(true);actions.set(entry,waiting);
            Field ui=AuthRuntimeProbe.class.getDeclaredField("ui");ui.setAccessible(true);ui.set(entry,new android.app.UiAutomation());
            Method clear=AuthRuntimeProbe.class.getDeclaredMethod("clearPassword"),shape=AuthRuntimeProbe.class.getDeclaredMethod("shape");
            clear.setAccessible(true);shape.setAccessible(true);android.app.UiAutomation.observations=0;
            clear.invoke(entry);String result=(String)shape.invoke(entry);
            need(android.app.UiAutomation.observations==0 && result.equals("product_UI_observation_closed=true raw_UI_recorded=false\n"));checks++;
            Method nodes=ReleaseSyncProcessRuntimeProbe.class.getDeclaredMethod("nodes");nodes.setAccessible(true);
            rejects(()->invoke(nodes,entry));need(android.app.UiAutomation.observations==0);checks++;
            waiting.close();clear.invoke(entry);shape.invoke(entry);need(android.app.UiAutomation.observations==0);checks++;
            // Before the boundary and for the old default path, cleanup still
            // reaches its original UI traversal; the guard did not disable it.
            for(AuthRuntimeProbe original:new AuthRuntimeProbe[]{new AuthRuntimeProbe(),new ReleaseSyncStopRuntimeProbe(),new ReleaseSyncProcessRuntimeProbe()}) {
                ui.set(original,new android.app.UiAutomation());android.app.UiAutomation.observations=0;clear.invoke(original);
                need(android.app.UiAutomation.observations==1);checks++;
            }
        } finally {
            try(java.util.stream.Stream<Path> paths=Files.walk(root)) {
                for(Path path:paths.sorted(java.util.Comparator.reverseOrder()).toArray(Path[]::new))Files.delete(path);
            }
        }
        System.out.println("ORIGINAL_SYNC_PROCESS_ACTIONS_OK checks="+checks+" actual_Android_process_loss_or_Native_ACK=0");
    }
}
