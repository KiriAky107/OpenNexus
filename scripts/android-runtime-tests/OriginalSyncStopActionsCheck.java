package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

/** Real original writers, bounded clock and signal handling; no Android input. */
public final class OriginalSyncStopActionsCheck {
    private static final String P="10000000-0000-0000-0000-000000000001",A="10000000-0000-0000-0000-000000000002",B="10000000-0000-0000-0000-000000000003";
    private static int checks;
    interface Task {void run() throws Exception;}
    private static void need(boolean value){if(!value)throw new AssertionError("original Stop contract changed");}
    private static void rejects(Task action) throws Exception {
        try{action.run();}catch(IllegalStateException expected){checks++;return;}
        throw new AssertionError("invalid original Stop sequence accepted");
    }
    private static OriginalSyncStopActions stop(){return new OriginalSyncStopActions(OriginalSyncStopActions.STOP,P,A,B);}
    private static void bytes(File file,String value) throws Exception {Files.write(file.toPath(),value.getBytes(StandardCharsets.UTF_8));}
    public static void main(String[] ignored) throws Exception {
        for(String phase:new String[]{null,"","cycle-stop","cycle-stop-original\n"})rejects(()->new OriginalSyncStopActions(phase,P,A,B));
        for(String[] ids:new String[][]{{P,P,B},{P,A,A},{P,A,P},{P,"NOT-A-UUID",B},{P,A,null}})
            rejects(()->new OriginalSyncStopActions(OriginalSyncStopActions.STOP,ids[0],ids[1],ids[2]));
        rejects(()->new OriginalSyncStopActions(OriginalSyncStopActions.RESUME,P,A,B));

        OriginalSyncStopActions s=stop();rejects(s::complete);rejects(s::stopped);rejects(s::released);
        rejects(()->s.begin("cycle-stop"));need(A.equals(s.begin("cycle-start")));checks++;
        rejects(()->s.begin("cycle-start"));rejects(()->s.finished("cycle-stop"));
        s.finished("cycle-start");rejects(()->s.finished("cycle-start"));rejects(s::complete);
        need(B.equals(s.begin("cycle-stop")));checks++;rejects(()->s.begin("cycle-stop"));
        s.finished("cycle-stop");rejects(()->s.begin("cycle-start"));rejects(s::complete);
        s.stopped();rejects(s::stopped);rejects(s::complete);s.released();rejects(s::released);
        s.complete();rejects(()->s.begin("cycle-start"));rejects(s::complete);
        need(s.summary().contains("inputs_finished=2 stopped_UI_observed=true release_signal_observed=true")
            &&s.summary().contains("native_cycle_acknowledged=false"));checks++;

        OriginalSyncStopActions unknown=stop();unknown.begin("cycle-start");
        rejects(()->unknown.begin("cycle-start"));rejects(()->unknown.begin("cycle-stop"));
        unknown.close();rejects(()->unknown.finished("cycle-start"));rejects(unknown::complete);
        OriginalSyncStopActions unknownStop=stop();unknownStop.begin("cycle-start");unknownStop.finished("cycle-start");unknownStop.begin("cycle-stop");
        rejects(()->unknownStop.begin("cycle-stop"));unknownStop.close();rejects(unknownStop::stopped);
        OriginalSyncStopActions resume=new OriginalSyncStopActions(OriginalSyncStopActions.RESUME,P,A,null);
        rejects(()->resume.begin("cycle-start"));need(A.equals(resume.begin("cycle-resume")));checks++;
        rejects(()->resume.begin("cycle-resume"));resume.finished("cycle-resume");rejects(resume::stopped);rejects(resume::released);
        resume.complete();rejects(()->resume.begin("cycle-resume"));

        Path root=Files.createTempDirectory("opennexus-stop-original-");
        try {
            File intent=root.resolve("original-intent.json").toFile();byte[] original="{\"phase\":1}".getBytes(StandardCharsets.UTF_8);
            OriginalSyncStopActions.writeOnce(intent,original);
            rejects(()->OriginalSyncStopActions.writeOnce(intent,new byte[]{1}));
            need(Arrays.equals(original,Files.readAllBytes(intent.toPath())));checks++;
            File empty=root.resolve("empty.json").toFile();rejects(()->OriginalSyncStopActions.writeOnce(empty,new byte[0]));
            rejects(()->OriginalSyncStopActions.writeOnce(empty,new byte[8193]));need(!empty.exists());checks++;
            File signal=root.resolve("finish").toFile();long[] clock={100};int[] guards={0},pauses={0};
            OriginalSyncStopActions.await(signal,()->guards[0]++,()->clock[0],delay->{
                try {clock[0]+=delay;pauses[0]++;bytes(signal,pauses[0]==1?"fin":"finish\n");}
                catch(Exception failure){throw new AssertionError(failure);}
            },200);
            need(pauses[0]==2&&guards[0]==4&&clock[0]==200);checks++;
            // An existing complete signal cannot bypass the owner guard before
            // or after the seven-byte read, nor a failed release observation.
            rejects(()->OriginalSyncStopActions.await(signal,()->{throw new IllegalStateException();},()->100L,delay->{throw new AssertionError();},200));
            int[] finalGuard={0};rejects(()->OriginalSyncStopActions.await(signal,()->{if(++finalGuard[0]==2)throw new IllegalStateException();},()->100L,delay->{throw new AssertionError();},200));
            bytes(signal,"finish!");rejects(()->OriginalSyncStopActions.await(signal,()->{},()->100L,delay->{throw new AssertionError();},200));
            bytes(signal,"finish\n!");rejects(()->OriginalSyncStopActions.await(signal,()->{},()->100L,delay->{throw new AssertionError();},200));
            Files.delete(signal.toPath());clock[0]=100;
            rejects(()->OriginalSyncStopActions.await(signal,()->{},()->clock[0],delay->clock[0]+=delay,100));
            long[] rollback={100};rejects(()->OriginalSyncStopActions.await(signal,()->{},()->rollback[0]--,delay->{},100));
            for(long bound:new long[]{0,-1,30001})rejects(()->OriginalSyncStopActions.await(signal,()->{},()->100L,delay->{},bound));
            rejects(()->OriginalSyncStopActions.await(signal,()->{},()->-1L,delay->{},100));
            rejects(()->OriginalSyncStopActions.await(signal,()->{},()->Long.MAX_VALUE,delay->{},100));
            // The last known input remains pending after an unknown finish.
            OriginalSyncStopActions partial=stop();partial.begin("cycle-start");bytes(signal,"fin");clock[0]=100;
            rejects(()->OriginalSyncStopActions.await(signal,()->{},()->clock[0],delay->clock[0]+=delay,100));
            rejects(()->partial.begin("cycle-start"));partial.close();rejects(partial::complete);
        } finally {
            try(java.util.stream.Stream<Path> paths=Files.walk(root)) {
                for(Path path:paths.sorted(java.util.Comparator.reverseOrder()).toArray(Path[]::new))Files.delete(path);
            }
        }
        System.out.println("ORIGINAL_SYNC_STOP_ACTIONS_OK checks="+checks+" actual_Android_Stop_or_Native_ACK=0");
    }
}
