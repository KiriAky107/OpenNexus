package cc.kronecker.opennexus.testdriver;

import java.util.Arrays;
import java.util.function.LongSupplier;

public final class OriginalSyncQuotaActionsCheck {
    private static final String P="a0000000-0000-0000-0000-000000000001",I="b0000000-0000-0000-0000-000000000002";
    private static final int[] FAILED={1,1,1,1,0,0,0,0};
    private static int assertions;
    interface Work {void run() throws Exception;}
    private static void check(boolean value) {assertions++;if(!value)throw new AssertionError("Quota original sequence contract");}
    private static void rejected(Work work) throws Exception {
        try {work.run();throw new AssertionError("Rejection missing");}
        catch(IllegalStateException expected) {assertions++;}
    }
    private static void same(Throwable expected,Work work) throws Exception {
        try {work.run();throw new AssertionError("Original exception missing");}
        catch(Throwable actual) {check(actual==expected);}
    }
    private static OriginalSyncQuotaActions fresh() {return new OriginalSyncQuotaActions(OriginalSyncQuotaActions.PHASE,P,I);}
    private static OriginalSyncQuotaActions context(long uptime) throws Exception {
        OriginalSyncQuotaActions a=fresh();
        a.awaitContext(()->{},()->a.contextSample(a.contextName(),true),()->uptime,ms->{throw new AssertionError("Unexpected pause");});
        return a;
    }
    private static OriginalSyncQuotaActions ready(long at) throws Exception {
        OriginalSyncQuotaActions a=context(0);check(a.begin("cycle-start").equals(I));a.ready(at);return a;
    }
    private static OriginalSyncQuotaActions finished(long at) throws Exception {
        OriginalSyncQuotaActions a=ready(at);
        a.awaitInputFinish(()->{},()->a.inputFinishSample(a.inputFinishName(),true),()->at,ms->{throw new AssertionError("Unexpected pause");});
        return a;
    }
    private static void poisoned(OriginalSyncQuotaActions a) throws Exception {rejected(a::beforeProductUi);}
    private static final class Clock implements LongSupplier {
        long now;final StringBuilder events;
        Clock(long now,StringBuilder events) {this.now=now;this.events=events;}
        public long getAsLong() {if(events!=null)events.append('C');return now;}
    }
    private static final class Samples implements LongSupplier {
        final long[] samples;int index;final StringBuilder events;
        Samples(StringBuilder events,long...samples) {this.samples=samples;this.events=events;}
        public long getAsLong() {events.append('C');check(index<samples.length);return samples[index++];}
    }
    private static void constructorsAndOrder() throws Exception {
        for(String phase:new String[]{null,"cycle-timeout-original","cycle-after-timeout-original","sync-quota"})
            rejected(()->new OriginalSyncQuotaActions(phase,P,I));
        for(String parent:new String[]{null,"",P.toUpperCase()," "+P})
            rejected(()->new OriginalSyncQuotaActions(OriginalSyncQuotaActions.PHASE,parent,I));
        rejected(()->new OriginalSyncQuotaActions(OriginalSyncQuotaActions.PHASE,P,P));
        OriginalSyncQuotaActions a=fresh();rejected(()->a.begin("cycle-start"));poisoned(a);
        OriginalSyncQuotaActions b=context(0);rejected(()->b.begin("cycle-resume"));poisoned(b);
        OriginalSyncQuotaActions c=context(0);rejected(()->c.ready(0));poisoned(c);
        OriginalSyncQuotaActions d=ready(1000);rejected(()->d.ready(1001));check(d.originalReadyAt()==1000);poisoned(d);
        OriginalSyncQuotaActions e=fresh();rejected(e::uiSuccessLine);poisoned(e);
        OriginalSyncQuotaActions f=ready(1000);rejected(f::complete);poisoned(f);
    }
    private static void completeSequenceAndKnownPendingSignals() throws Exception {
        OriginalSyncQuotaActions a=fresh();Clock contextClock=new Clock(100,null);int[] reads={0},pauses={0};
        a.awaitContext(()->{},()->a.contextSample(a.contextName(),++reads[0]==2),contextClock,ms->{check(ms==50);pauses[0]++;contextClock.now+=ms;});
        check(reads[0]==2 && pauses[0]==1);check(a.begin("cycle-start").equals(I));a.ready(1000);
        Clock inputClock=new Clock(1000,null);reads[0]=0;pauses[0]=0;
        a.awaitInputFinish(()->{},()->a.inputFinishSample(a.inputFinishName(),++reads[0]==2),inputClock,ms->{check(ms==50);pauses[0]++;inputClock.now+=ms;});
        check(reads[0]==2 && pauses[0]==1 && a.originalInputFinished());
        StringBuilder events=new StringBuilder();Clock failureClock=new Clock(1100,events);
        a.awaitFailure(()->events.append('O'),()->{events.append('S');return a.sceneSample(FAILED,failureClock.now);},
            failureClock,ms->{throw new AssertionError("Unexpected pause");});
        check(events.toString().equals("COCSCOC"));check(a.originalReadyAt()==1000 && a.failureAt()==1100);
        check(Arrays.equals(a.lastScene(),FAILED));a.complete();
        check(a.uiSuccessLine().equals("OPENNEXUS_SIGNED_QUOTA_UI input_finished=true quota_error_UI_observed=true raw_UI_recorded=false native_original_state_verified=false"));
        check(a.phaseSuccessLine().equals("OPENNEXUS_SIGNED_QUOTA_PHASE_OK cycle-quota-signed-original"));
        a.close();check(a.uiSuccessLine().contains("native_original_state_verified=false"));
        rejected(a::complete);poisoned(a);
    }
    private static void contextBoundsScopeAndUnknown() throws Exception {
        OriginalSyncQuotaActions a=fresh();StringBuilder events=new StringBuilder();
        a.awaitContext(()->events.append('O'),()->a.contextSample(a.contextName(),true),
            new Samples(events,0,10000,10000,10000,10000),ms->{throw new AssertionError("Unexpected pause");});
        check(a.begin("cycle-start").equals(I));a.ready(0);check(a.originalReadyAt()==0);
        for(long bad:new long[]{-1,Long.MAX_VALUE-9999}) {
            OriginalSyncQuotaActions b=fresh();int[] calls={0};
            rejected(()->b.awaitContext(()->calls[0]++,()->{calls[0]++;return b.contextSample(b.contextName(),true);},()->bad,ms->{}));
            check(calls[0]==0);poisoned(b);
        }
        for(boolean complete:new boolean[]{false,true}) {
            OriginalSyncQuotaActions b=fresh();StringBuilder log=new StringBuilder();
            LongSupplier clock=complete?new Samples(log,0,10001):new Samples(log,0,10000,10000,10000);
            rejected(()->b.awaitContext(()->log.append('O'),()->{log.append('R');return b.contextSample(b.contextName(),complete);},clock,ms->log.append('P')));
            check(log.toString().equals(complete?"CC":"CCOCRC"));poisoned(b);
        }
        OriginalSyncQuotaActions reverse=fresh();StringBuilder log=new StringBuilder();
        rejected(()->reverse.awaitContext(()->log.append('O'),()->reverse.contextSample(reverse.contextName(),true),new Samples(log,5,4),ms->{}));
        check(log.toString().equals("CC"));poisoned(reverse);
        for(int kind=0;kind<3;kind++) {
            final int test=kind;OriginalSyncQuotaActions b=fresh();
            rejected(()->b.awaitContext(()->{},()->test==0?null:b.contextSample(test==1?"foreign.finish":b.contextName(),test==2?null:true),()->0,ms->{}));
            poisoned(b);
        }
        // A very large uptime is unrelated to the later elapsedRealtime epoch.
        OriginalSyncQuotaActions separate=fresh();StringBuilder unused=new StringBuilder();
        separate.awaitContext(()->{},()->separate.contextSample(separate.contextName(),true),
            new Samples(unused,Long.MAX_VALUE-10000,Long.MAX_VALUE,Long.MAX_VALUE,Long.MAX_VALUE,Long.MAX_VALUE),ms->{});
        separate.begin("cycle-start");separate.ready(0);
        separate.awaitInputFinish(()->{},()->separate.inputFinishSample(separate.inputFinishName(),true),()->0,ms->{});
        separate.awaitFailure(()->{},()->separate.sceneSample(FAILED,0),()->0,ms->{});
        separate.complete();check(separate.originalReadyAt()==0 && separate.failureAt()==0);
    }
    private static void reusedSignalAndStalledClock() throws Exception {
        OriginalSyncQuotaActions a=fresh();OriginalSyncQuotaActions.SignalSample[] cached={null};int[] reads={0};
        rejected(()->a.awaitContext(()->{},()->{reads[0]++;if(cached[0]==null)cached[0]=a.contextSample(a.contextName(),false);return cached[0];},()->0,ms->{}));
        check(reads[0]==2);poisoned(a);
        OriginalSyncQuotaActions owner=fresh();OriginalSyncQuotaActions.SignalSample[] foreign={null};
        owner.awaitContext(()->{},()->{foreign[0]=owner.contextSample(owner.contextName(),false);return owner.contextSample(owner.contextName(),true);},()->0,ms->{});
        OriginalSyncQuotaActions other=fresh();
        rejected(()->other.awaitContext(()->{},()->foreign[0],()->0,ms->{}));poisoned(other);
        OriginalSyncQuotaActions stalled=fresh();int[] counts={0,0,0};
        rejected(()->stalled.awaitContext(()->counts[0]++,()->{counts[1]++;return stalled.contextSample(stalled.contextName(),false);},()->0,ms->counts[2]++));
        check(counts[0]==OriginalSyncQuotaActions.MAX_OBSERVATIONS && counts[1]==counts[0] && counts[2]==counts[0]-1);poisoned(stalled);
    }
    private static void inputFinishBoundAndNoReset() throws Exception {
        OriginalSyncQuotaActions a=ready(1000);
        a.awaitInputFinish(()->{},()->a.inputFinishSample(a.inputFinishName(),true),()->31000,ms->{});
        check(a.originalReadyAt()==1000 && a.originalInputFinished());
        a.awaitFailure(()->{},()->a.sceneSample(FAILED,31000),()->31000,ms->{});a.complete();
        for(boolean complete:new boolean[]{false,true}) {
            OriginalSyncQuotaActions b=ready(1000);
            rejected(()->b.awaitInputFinish(()->{},()->b.inputFinishSample(b.inputFinishName(),complete),()->complete?31001:31000,ms->{}));
            check(b.originalReadyAt()==1000 && !b.originalInputFinished());poisoned(b);
        }
        OriginalSyncQuotaActions early=context(0);
        rejected(()->early.awaitInputFinish(()->{},()->null,()->0,ms->{}));poisoned(early);
        OriginalSyncQuotaActions duplicate=finished(1000);
        rejected(()->duplicate.awaitInputFinish(()->{},()->null,()->1000,ms->{}));poisoned(duplicate);
        OriginalSyncQuotaActions wrongChannel=fresh();OriginalSyncQuotaActions.SignalSample[] contextOnly={null};
        wrongChannel.awaitContext(()->{},()->{contextOnly[0]=wrongChannel.contextSample(wrongChannel.contextName(),true);
            return wrongChannel.contextSample(wrongChannel.contextName(),true);},()->0,ms->{});
        wrongChannel.begin("cycle-start");wrongChannel.ready(1000);
        rejected(()->wrongChannel.awaitInputFinish(()->{},()->contextOnly[0],()->1000,ms->{}));poisoned(wrongChannel);
        for(boolean wrongName:new boolean[]{false,true}) {
            OriginalSyncQuotaActions unknown=ready(1000);
            rejected(()->unknown.awaitInputFinish(()->{},()->unknown.inputFinishSample(
                wrongName?unknown.contextName():unknown.inputFinishName(),wrongName?true:null),()->1000,ms->{}));poisoned(unknown);
        }
        OriginalSyncQuotaActions maximum=finished(Long.MAX_VALUE-36000);
        maximum.awaitFailure(()->{},()->maximum.sceneSample(FAILED,Long.MAX_VALUE),()->Long.MAX_VALUE,ms->{});
        maximum.complete();check(maximum.failureAt()==Long.MAX_VALUE);
        OriginalSyncQuotaActions overflow=context(0);overflow.begin("cycle-start");
        rejected(()->overflow.ready(Long.MAX_VALUE-35999));check(overflow.originalReadyAt()==-1);poisoned(overflow);
        OriginalSyncQuotaActions negative=context(0);negative.begin("cycle-start");
        rejected(()->negative.ready(-1));check(negative.originalReadyAt()==-1);poisoned(negative);
    }
    private static void failureBoundAndCallbackOrder() throws Exception {
        long[][] cases={{37001},{1000,37001},{1000,1000,37001},{1000,1000,1000,37001},{1000,999},{1000,1000,999},{1000,1000,1000,999}};
        String[] logs={"C","COC","COCSC","COCSCOC","COC","COCSC","COCSCOC"};
        for(int i=0;i<cases.length;i++) {
            OriginalSyncQuotaActions a=finished(1000);StringBuilder events=new StringBuilder();
            Samples clock=new Samples(events,cases[i]);
            rejected(()->a.awaitFailure(()->events.append('O'),()->{events.append('S');return a.sceneSample(FAILED,1000);},clock,ms->events.append('P')));
            check(events.toString().equals(logs[i]));check(a.failureAt()==-1 && a.originalReadyAt()==1000);poisoned(a);
        }
        OriginalSyncQuotaActions exact=finished(1000);
        exact.awaitFailure(()->{},()->exact.sceneSample(FAILED,37000),()->37000,ms->{});exact.complete();
        check(exact.failureAt()==37000);
        OriginalSyncQuotaActions falseAtEnd=finished(1000);int[] pauses={0};
        rejected(()->falseAtEnd.awaitFailure(()->{},()->falseAtEnd.sceneSample(new int[8],37000),()->37000,ms->pauses[0]++));
        check(pauses[0]==0);poisoned(falseAtEnd);
    }
    private static void sceneCopiesFreshnessAndClosedCounts() throws Exception {
        OriginalSyncQuotaActions a=finished(1000);int[] source=FAILED.clone();
        a.awaitFailure(()->{},()->{OriginalSyncQuotaActions.SceneSample result=a.sceneSample(source,1000);Arrays.fill(source,0);return result;},()->1000,ms->{});
        check(Arrays.equals(a.lastScene(),FAILED));int[] exposed=a.lastScene();Arrays.fill(exposed,0);
        check(Arrays.equals(a.lastScene(),FAILED));check(a.lastSceneAt()==1000);a.complete();
        OriginalSyncQuotaActions saturated=finished(1000);int[] large=new int[8];Arrays.fill(large,Integer.MAX_VALUE);
        RuntimeException pauseError=new RuntimeException("controlled");
        same(pauseError,()->saturated.awaitFailure(()->{},()->saturated.sceneSample(large,1000),()->1000,ms->{throw pauseError;}));
        check(Arrays.equals(saturated.lastScene(),new int[]{2,2,2,2,2,2,2,2}));check(saturated.failureAt()==-1);poisoned(saturated);
        for(int[] bad:new int[][]{null,new int[7],new int[9],new int[]{1,1,-1,1,0,0,0,0}}) {
            OriginalSyncQuotaActions b=finished(1000);rejected(()->b.sceneSample(bad,1000));poisoned(b);
        }
        for(long bad:new long[]{999,37001}) {
            OriginalSyncQuotaActions b=finished(1000);rejected(()->b.sceneSample(FAILED,bad));poisoned(b);
        }
        OriginalSyncQuotaActions stale=finished(1000);OriginalSyncQuotaActions.SceneSample[] old={null};int[] calls={0};
        rejected(()->stale.awaitFailure(()->{},()->{calls[0]++;if(old[0]==null)old[0]=stale.sceneSample(new int[8],1000);return old[0];},()->1000,ms->{}));
        check(calls[0]==2);poisoned(stale);
        OriginalSyncQuotaActions foreign=finished(1000),other=finished(1000);
        OriginalSyncQuotaActions.SceneSample sample=foreign.sceneSample(FAILED,1000);
        rejected(()->other.awaitFailure(()->{},()->sample,()->1000,ms->{}));poisoned(other);
        OriginalSyncQuotaActions nullScene=finished(1000);
        rejected(()->nullScene.awaitFailure(()->{},()->null,()->1000,ms->{}));poisoned(nullScene);
        OriginalSyncQuotaActions future=finished(1000);
        rejected(()->future.awaitFailure(()->{},()->future.sceneSample(FAILED,1001),()->1000,ms->{}));poisoned(future);
    }
    private static void originalExceptionsAndCallbackClose() throws Exception {
        Exception ownerError=new Exception("controlled");OriginalSyncQuotaActions a=finished(1000);
        same(ownerError,()->a.awaitFailure(()->{throw ownerError;},()->a.sceneSample(FAILED,1000),()->1000,ms->{}));poisoned(a);
        Exception sceneError=new Exception("controlled");OriginalSyncQuotaActions b=finished(1000);
        same(sceneError,()->b.awaitFailure(()->{},()->{throw sceneError;},()->1000,ms->{}));poisoned(b);
        AssertionError readError=new AssertionError("controlled");OriginalSyncQuotaActions c=fresh();
        same(readError,()->c.awaitContext(()->{},()->{throw readError;},()->0,ms->{}));poisoned(c);
        OriginalSyncQuotaActions closed=fresh();
        rejected(()->closed.awaitContext(()->{},()->null,()->{closed.close();return 0;},ms->{}));poisoned(closed);
        OriginalSyncQuotaActions reentrant=finished(1000);
        rejected(()->reentrant.awaitFailure(()->{},()->{OriginalSyncQuotaActions.SceneSample result=reentrant.sceneSample(FAILED,1000);reentrant.close();return result;},()->1000,ms->{}));
        check(reentrant.lastScene()==null);poisoned(reentrant);
        OriginalSyncQuotaActions finalOwner=finished(1000);int[] calls={0};
        same(ownerError,()->finalOwner.awaitFailure(()->{if(++calls[0]==2)throw ownerError;},()->finalOwner.sceneSample(FAILED,1000),()->1000,ms->{}));
        check(finalOwner.failureAt()==-1);poisoned(finalOwner);
        OriginalSyncQuotaActions ownerClosed=finished(1000);StringBuilder ownerEvents=new StringBuilder();
        rejected(()->ownerClosed.awaitFailure(()->{ownerEvents.append('O');ownerClosed.close();},
            ()->{ownerEvents.append('S');return null;},new Clock(1000,ownerEvents),ms->{}));
        check(ownerEvents.toString().equals("CO"));poisoned(ownerClosed);
        OriginalSyncQuotaActions pauseClosed=finished(1000);StringBuilder pauseEvents=new StringBuilder();
        rejected(()->pauseClosed.awaitFailure(()->pauseEvents.append('O'),
            ()->{pauseEvents.append('S');return pauseClosed.sceneSample(new int[8],1000);},new Clock(1000,pauseEvents),
            ms->{pauseEvents.append('P');pauseClosed.close();}));
        check(pauseEvents.toString().equals("COCSCP"));poisoned(pauseClosed);
        OriginalSyncQuotaActions finalClosed=finished(1000);StringBuilder finalEvents=new StringBuilder();int[] finalCalls={0};
        rejected(()->finalClosed.awaitFailure(()->{finalEvents.append('O');if(++finalCalls[0]==2)finalClosed.close();},
            ()->{finalEvents.append('S');return finalClosed.sceneSample(FAILED,1000);},new Clock(1000,finalEvents),ms->{}));
        check(finalEvents.toString().equals("COCSCO"));check(finalClosed.failureAt()==-1);poisoned(finalClosed);
    }
    private static void waitUntilFreshCompleteSceneAndStalledLimit() throws Exception {
        OriginalSyncQuotaActions a=finished(1000);Clock time=new Clock(1000,null);int[] calls={0};
        a.awaitFailure(()->{},()->a.sceneSample(++calls[0]==1?new int[8]:FAILED,time.now),time,ms->{check(ms==50);time.now+=ms;});
        check(calls[0]==2 && a.failureAt()==1050);a.complete();
        OriginalSyncQuotaActions stalled=finished(1000);int[] counts={0,0,0};
        rejected(()->stalled.awaitFailure(()->counts[0]++,()->{counts[1]++;return stalled.sceneSample(new int[8],1000);},()->1000,ms->counts[2]++));
        check(counts[0]==OriginalSyncQuotaActions.MAX_OBSERVATIONS && counts[1]==counts[0] && counts[2]==counts[0]-1);poisoned(stalled);
    }
    public static void main(String[] args) throws Exception {
        constructorsAndOrder();completeSequenceAndKnownPendingSignals();contextBoundsScopeAndUnknown();
        reusedSignalAndStalledClock();inputFinishBoundAndNoReset();failureBoundAndCallbackOrder();
        sceneCopiesFreshnessAndClosedCounts();originalExceptionsAndCallbackClose();waitUntilFreshCompleteSceneAndStalledLimit();
        System.out.println("QUOTA_ACTIONS_CHECK_OK assertions="+assertions);
    }
}
