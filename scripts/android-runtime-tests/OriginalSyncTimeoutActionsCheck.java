package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Real sequence, files and controlled monotonic clocks; no native timer or Android result. */
public final class OriginalSyncTimeoutActionsCheck {
    private static final String P="40000000-0000-4000-8000-000000000001",A="40000000-0000-4000-8000-000000000002";
    private static int checks;
    interface Task {void run() throws Exception;}
    private static void need(boolean value){if(!value)throw new AssertionError("original timeout contract changed");checks++;}
    private static void rejects(Task action) throws Exception {
        try {action.run();}catch(IllegalStateException expected){checks++;return;}
        throw new AssertionError("invalid timeout sequence accepted");
    }
    private static OriginalSyncTimeoutActions fresh(){return new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.TIMEOUT,P,A);}
    private static OriginalSyncTimeoutActions pending(){OriginalSyncTimeoutActions v=fresh();v.begin("cycle-start");return v;}
    private static OriginalSyncTimeoutActions finished(){OriginalSyncTimeoutActions v=pending();v.ready(100);v.finished("cycle-start");return v;}
    private static void bytes(File f,String value) throws Exception {Files.write(f.toPath(),value.getBytes(StandardCharsets.UTF_8));}
    public static void main(String[] ignored) throws Exception {
        for(String phase:new String[]{null,"","cycle-stop-original","cycle-timeout-original\n"})rejects(()->new OriginalSyncTimeoutActions(phase,P,A));
        for(String[] ids:new String[][]{{P,P},{P,null},{null,A},{P,"NOT-A-UUID"}})rejects(()->new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.TIMEOUT,ids[0],ids[1]));
        OriginalSyncTimeoutActions v=fresh();rejects(v::complete);rejects(()->v.ready(100));rejects(()->v.finished("cycle-start"));
        rejects(()->v.begin("cycle-stop"));need(A.equals(v.begin("cycle-start")));rejects(()->v.begin("cycle-start"));
        rejects(()->v.finished("cycle-start"));rejects(()->v.ready(-1));rejects(()->v.ready(Long.MAX_VALUE));v.ready(100);
        rejects(()->v.ready(100));rejects(()->v.finished("cycle-resume"));v.finished("cycle-start");rejects(v::complete);
        rejects(()->v.finished("cycle-start"));long[] clock={100};int[] guards={0};
        v.observeFailure(()->guards[0]++,()->clock[0]>=30100,()->clock[0],delay->clock[0]+=delay);
        need(clock[0]==30100 && guards[0]>0 && v.summary().contains("failed_cycle_UI_observed=true"));
        need(v.summary().contains("native_timeout_verified=false") && v.summary().contains("native_cycle_acknowledged=false"));rejects(v::complete);
        rejects(()->v.observeFailure(()->{},()->true,()->30100L,delay->{}));
        OriginalSyncTimeoutActions resume=new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.RESUME,P,A);
        rejects(()->resume.begin("cycle-start"));resume.begin("cycle-resume");resume.ready(100);resume.finished("cycle-resume");
        rejects(()->resume.observeFailure(()->{},()->true,()->100L,delay->{}));resume.complete();rejects(resume::complete);
        for(int step=0;step<3;step++) {
            OriginalSyncTimeoutActions unknown=fresh();if(step>=1)unknown.begin("cycle-start");if(step>=2)unknown.ready(100);
            unknown.close();rejects(()->unknown.begin("cycle-start"));rejects(()->unknown.finished("cycle-start"));rejects(unknown::complete);rejects(unknown::beforeProductUi);
        }
        need(OriginalSyncTimeoutActions.failedScene(1,1,1,1,0,0,0,0));
        for(int i=0;i<8;i++) {
            int[] state={1,1,1,1,0,0,0,0};state[i]=i<4?2:1;
            need(!OriginalSyncTimeoutActions.failedScene(state[0],state[1],state[2],state[3],state[4],state[5],state[6],state[7]));
        }
        for(int i=0;i<4;i++) {
            int[] state={1,1,1,1,0,0,0,0};state[i]=0;
            need(!OriginalSyncTimeoutActions.failedScene(state[0],state[1],state[2],state[3],state[4],state[5],state[6],state[7]));
        }
        // Finish arriving late never resets the36second clock from original ready.
        OriginalSyncTimeoutActions late=finished();clock[0]=36100;
        rejects(()->late.observeFailure(()->{},()->false,()->clock[0],delay->clock[0]+=delay));rejects(late::complete);
        OriginalSyncTimeoutActions ownerSlow=finished();clock[0]=36099;
        rejects(()->ownerSlow.observeFailure(()->clock[0]+=2,()->true,()->clock[0],delay->{}));
        OriginalSyncTimeoutActions stateSlow=finished();clock[0]=36099;
        rejects(()->stateSlow.observeFailure(()->{},()->{clock[0]+=2;return true;},()->clock[0],delay->{}));
        OriginalSyncTimeoutActions finalSlow=finished();clock[0]=36099;guards[0]=0;
        rejects(()->finalSlow.observeFailure(()->{if(++guards[0]==2)clock[0]+=2;},()->true,()->clock[0],delay->{}));
        OriginalSyncTimeoutActions changed=finished();
        rejects(()->changed.observeFailure(()->{throw new IllegalStateException();},()->true,()->100L,delay->{}));rejects(changed::beforeProductUi);
        OriginalSyncTimeoutActions rollback=finished();clock[0]=101;
        rejects(()->rollback.observeFailure(()->clock[0]-=2,()->true,()->clock[0],delay->{}));
        Path root=Files.createTempDirectory("opennexus-timeout-signals-");
        try {
            File release=root.resolve("released-"+P+".finish").toFile(),wrong=root.resolve("released-"+A+".finish").toFile();bytes(wrong,"finish\n");
            OriginalSyncTimeoutActions awaiting=finished();awaiting.observeFailure(()->{},()->true,()->100L,delay->{});clock[0]=100;
            rejects(()->awaiting.awaitRelease(release,()->{},()->clock[0],delay->clock[0]+=delay));
            need(!awaiting.summary().contains("host_release_signal_observed=true"));rejects(awaiting::complete);
            OriginalSyncTimeoutActions partial=finished();partial.observeFailure(()->{},()->true,()->100L,delay->{});clock[0]=100;guards[0]=0;int[] pauses={0};
            partial.awaitRelease(release,()->guards[0]++,()->clock[0],delay->{clock[0]+=delay;
                try {bytes(release,++pauses[0]==1?"fin":"finish\n");}catch(Exception error){throw new AssertionError(error);}});
            need(pauses[0]==2 && guards[0]==4);partial.complete();partial.close();need(partial.summary().contains("host_release_signal_observed=true"));
            rejects(()->partial.awaitRelease(release,()->{},()->100L,delay->{}));rejects(partial::complete);
            for(String invalid:new String[]{"finish!","finish\n!"}) {
                bytes(release,invalid);rejects(()->OriginalSyncTimeoutActions.awaitSignal(release,()->{},()->100L,delay->{},5000));
            }
            bytes(release,"finish\n");
            rejects(()->OriginalSyncTimeoutActions.awaitSignal(release,()->{throw new IllegalStateException();},()->100L,delay->{},5000));
            guards[0]=0;rejects(()->OriginalSyncTimeoutActions.awaitSignal(release,()->{if(++guards[0]==2)throw new IllegalStateException();},()->100L,delay->{},5000));
            for(long bound:new long[]{0,-1,5001,30001,36000})rejects(()->OriginalSyncTimeoutActions.awaitSignal(release,()->{},()->100L,delay->{},bound));
            File intent=root.resolve("intent").toFile();OriginalSyncStopActions.writeOnce(intent,new byte[]{1,2,3});
            rejects(()->OriginalSyncStopActions.writeOnce(intent,new byte[]{4}));need(Files.readAllBytes(intent.toPath())[0]==1);
        } finally {
            try(java.util.stream.Stream<Path> paths=Files.walk(root)) {
                for(Path path:paths.sorted(java.util.Comparator.reverseOrder()).toArray(Path[]::new))Files.delete(path);
            }
        }
        System.out.println("ORIGINAL_SYNC_TIMEOUT_ACTIONS_OK checks="+checks+" actual_Android_timeout_or_Native_ACK=0");
    }
}
