package cc.kronecker.opennexus.testdriver;

import java.util.Arrays;

/** One scoped pre-input preparation. The port never receives an arbitrary command/path. */
final class OriginalSyncTimeoutPreinputFlow {
    enum Record {INTENT,ATTEMPT}
    interface Port {
        Frame read() throws Exception;
        long uptime() throws Exception;
        void pause(long millis) throws Exception;
        void write(Record record,byte[] bytes) throws Exception;
        boolean revealSection(long notBeforeUptime,long deadlineUptime) throws Exception;
    }
    static final class Frame {
        final int uid,window;
        final int[] ownWindow,button,buttonClip,section,sectionClip;
        Frame(int uid,int window,int[] own,int[] button,int[] buttonClip,int[] section,int[] sectionClip) {
            this.uid=uid;this.window=window;ownWindow=copy(own);this.button=copy(button);
            this.buttonClip=copy(buttonClip);this.section=copy(section);this.sectionClip=copy(sectionClip);
        }
        OriginalSyncTimeoutPreinputGeometry.Scope scope() {
            return new OriginalSyncTimeoutPreinputGeometry.Scope(true,true,false,false,uid,window);
        }
        boolean same(Frame other) {
            return other!=null && uid==other.uid && window==other.window && Arrays.equals(ownWindow,other.ownWindow)
                && Arrays.equals(button,other.button) && Arrays.equals(buttonClip,other.buttonClip)
                && Arrays.equals(section,other.section) && Arrays.equals(sectionClip,other.sectionClip);
        }
        private static int[] copy(int[] value) {return value==null?null:value.clone();}
    }
    private final Port port;
    private final OriginalSyncTimeoutPreinputMetadata.Parent parent;
    private final OriginalSyncTimeoutPreinputGeometry planner;
    private final OriginalSyncTimeoutPreinputMetadata.Written intent;
    private final long deadline;
    private OriginalSyncTimeoutPreinputMetadata.Written attempt;
    private Boolean revealed;
    private long finalUptime,lastUptime;
    private boolean finalAttempted,checked,wireCreated;

    private OriginalSyncTimeoutPreinputFlow(Port port,OriginalSyncTimeoutPreinputMetadata.Parent parent,
            OriginalSyncTimeoutPreinputGeometry planner,OriginalSyncTimeoutPreinputMetadata.Written intent,long start) {
        this.port=port;this.parent=parent;this.planner=planner;this.intent=intent;
        lastUptime=start;deadline=start+5000;
    }
    static OriginalSyncTimeoutPreinputFlow prepare(String parentId,String inputId,Port port) throws Exception {
        need(port!=null);
        OriginalSyncTimeoutPreinputMetadata.Parent parent=OriginalSyncTimeoutPreinputMetadata.parent(parentId,inputId);
        need(parent!=null);
        long start=port.uptime();need(start>=0 && start<=Long.MAX_VALUE-5000);
        Frame first=port.read();need(first!=null);
        long discovered=port.uptime();need(discovered>=start && discovered<=start+5000);
        OriginalSyncTimeoutPreinputGeometry planner=new OriginalSyncTimeoutPreinputGeometry(start,first.uid,first.window);
        OriginalSyncTimeoutPreinputMetadata.WriteRecord record=OriginalSyncTimeoutPreinputMetadata.intent(parent,first.uid,first.window,start);
        need(record!=null);byte[] bytes=record.bytes();port.write(Record.INTENT,bytes);
        OriginalSyncTimeoutPreinputMetadata.Written written=OriginalSyncTimeoutPreinputMetadata.reportedWritten(record,bytes,true);
        need(written!=null);
        OriginalSyncTimeoutPreinputFlow flow=new OriginalSyncTimeoutPreinputFlow(port,parent,planner,written,start);
        flow.lastUptime=discovered;
        for(int observations=0;observations<=100;observations++) {
            flow.clock();
            Frame frame=port.read();need(frame!=null);
            OriginalSyncTimeoutPreinputGeometry.Decision decision=flow.observe(frame,flow.clock());
            if(decision==OriginalSyncTimeoutPreinputGeometry.Decision.CURRENT_PAIR_STABLE)return flow;
            if(decision==OriginalSyncTimeoutPreinputGeometry.Decision.REQUEST_ONE_SECTION_REVEAL) {
                OriginalSyncTimeoutPreinputMetadata.WriteRecord action=OriginalSyncTimeoutPreinputMetadata.attempt(written);
                need(action!=null);byte[] raw=action.bytes();port.write(Record.ATTEMPT,raw);
                flow.attempt=OriginalSyncTimeoutPreinputMetadata.reportedWritten(action,raw,true);need(flow.attempt!=null);
                flow.clock();boolean result=port.revealSection(flow.lastUptime,flow.deadline);flow.revealed=result;
                need(planner.revealReturned(frame.scope(),result,flow.clock())
                    ==OriginalSyncTimeoutPreinputGeometry.Decision.WAIT_WITHIN_PREINPUT_BUDGET);
            } else need(decision==OriginalSyncTimeoutPreinputGeometry.Decision.WAIT_WITHIN_PREINPUT_BUDGET);
            port.pause(50);
        }
        need(false);return null;
    }
    void finalCheck(int[] admitted,int[] visible,int window) throws Exception {
        need(!finalAttempted && !wireCreated);finalAttempted=true;
        need(admitted!=null && visible!=null && admitted.length==4 && visible.length==4 && window>=0 && window<=65535);
        clock();
        Frame frame=port.read();need(frame!=null);
        long sampled=clock();
        need(observe(frame,sampled)==OriginalSyncTimeoutPreinputGeometry.Decision.CURRENT_PAIR_STABLE
            && frame.window==window && Arrays.equals(frame.button,admitted) && Arrays.equals(admitted,visible));
        finalUptime=sampled;checked=true;
    }
    /** No port calls after the original ready clock/state transition. */
    byte[] readyBytes(int uid,int window,int[] original,int[] visible,int x,int y,long elapsed) {
        need(checked && !wireCreated);wireCreated=true;
        OriginalSyncTimeoutPreinputMetadata.Ready ready=OriginalSyncTimeoutPreinputMetadata.ready(parent,uid,window,original,visible,x,y,elapsed);
        OriginalSyncTimeoutPreinputMetadata.Wire wire=OriginalSyncTimeoutPreinputMetadata.project(parent,ready,intent,attempt,revealed,finalUptime,true,true);
        need(wire!=null);return wire.bytes();
    }
    private OriginalSyncTimeoutPreinputGeometry.Decision observe(Frame frame,long sampled) {
        return planner.observe(frame.scope(),sampled,frame.ownWindow,frame.button,frame.buttonClip,frame.section,frame.sectionClip);
    }
    private long clock() throws Exception {
        long sampled=port.uptime();need(sampled>=lastUptime && sampled<=deadline);lastUptime=sampled;return sampled;
    }
    private static void need(boolean value) {if(!value)throw new IllegalStateException("OWNED_SYNC_TIMEOUT_PREINPUT_REFUSED");}
}
