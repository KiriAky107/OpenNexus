package cc.kronecker.opennexus.testdriver;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/** Actual local gate/finish reader, using owned temporary files and immutable frame fixtures. */
public final class OriginalSyncImageGateCheck {
    private static void need(boolean value){if(!value)throw new AssertionError("image GUI gate changed");}
    private static OriginalSyncImageGate.Frame frame(int uid,int window,int x){
        return new OriginalSyncImageGate.Frame(uid,window,0,new int[]{0,0,1080,1920},new int[]{0,200,1080,1800},new int[]{x,600,x+300,900});
    }
    private static OriginalSyncImageGate ready(OriginalSyncImageGate.Frame frame){
        OriginalSyncImageGate gate=new OriginalSyncImageGate();need(!gate.observe(frame,0));need(gate.observe(frame,600));gate.publish(600);return gate;
    }
    public static void main(String[] ignored) throws Exception {
        int cases=0,identity=0;
        OriginalSyncImageGate.Frame a=frame(10216,88,200),b=frame(10216,88,201);
        OriginalSyncImageGate stable=new OriginalSyncImageGate();need(!stable.observe(a,1000));need(!stable.observe(a,1599));
        need(stable.observe(a,1600));need(stable.publish(1600).same(a));need(stable.stableMillis()==600);cases++;
        int[] image=a.image();image[0]=0;need(a.image()[0]==200);cases++;
        OriginalSyncImageGate moving=new OriginalSyncImageGate();need(!moving.observe(a,0));need(!moving.observe(b,500));
        need(!moving.observe(b,1099));need(moving.observe(b,1100));need(moving.publish(1100).same(b));cases++;
        OriginalSyncImageGate missing=new OriginalSyncImageGate();need(!missing.observe(a,0));need(!missing.observe(null,500));
        need(!missing.observe(a,600));need(!missing.observe(a,1199));need(missing.observe(a,1200));missing.publish(1200);cases++;
        OriginalSyncImageGate rollback=new OriginalSyncImageGate();need(!rollback.observe(a,1000));need(!rollback.observe(a,900));
        need(!rollback.observe(a,1499));need(rollback.observe(a,1500));rollback.publish(1500);cases++;
        for(String mode:new String[]{"uid","window","image","missing","time","publish-again","observe-after-publish"}) {
            OriginalSyncImageGate gate=ready(a);boolean failed=false;
            try {
                if(mode.equals("publish-again"))gate.publish(600);
                else if(mode.equals("observe-after-publish"))gate.observe(a,700);
                else gate.guard(mode.equals("uid")?frame(10217,88,200):mode.equals("window")?frame(10216,89,200):mode.equals("image")?b:mode.equals("missing")?null:a,mode.equals("time")?599:700);
            }catch(IllegalStateException expected){failed=true;}need(failed);cases++;
        }
        for(String mode:new String[]{"changed","missing","clock","unknown-finish","timeout"}) {
            OriginalSyncImageGate gate=ready(a);AtomicLong clock=new AtomicLong(600);AtomicInteger calls=new AtomicInteger();boolean failed=false;
            try {
                gate.hold(()->clock.get()==600?a:mode.equals("missing")?null:b,clock::get,wait->{
                    need(wait==50);clock.set(mode.equals("clock")?599:mode.equals("timeout")?90601:650);
                },()->{calls.incrementAndGet();if(mode.equals("unknown-finish"))throw new Exception("unknown original result");return false;});
            }catch(Exception expected){failed=true;}need(failed && calls.get()==1);cases++;
        }
        Path temporary=Files.createTempDirectory("owned-image-finish-");
        try {
            Path partial=temporary.resolve("partial.finish");Files.write(partial,"fin".getBytes(StandardCharsets.US_ASCII));
            OriginalSyncImageGate gate=ready(a);AtomicLong clock=new AtomicLong(600);AtomicInteger pauses=new AtomicInteger(),frames=new AtomicInteger();
            gate.hold(()->{frames.incrementAndGet();return a;},clock::get,wait->{
                clock.addAndGet(wait);pauses.incrementAndGet();try{Files.write(partial,"finish\n".getBytes(StandardCharsets.US_ASCII));}catch(Exception error){throw new AssertionError(error);}
            },()->OriginalAuthSignal.complete(partial.toFile()));
            need(pauses.get()==1 && frames.get()>=3);cases++;
            Path changed=temporary.resolve("changed.finish");Files.write(changed,"wrong!!".getBytes(StandardCharsets.US_ASCII));
            OriginalSyncImageGate bad=ready(a);AtomicInteger attempts=new AtomicInteger();boolean failed=false;
            try {bad.hold(()->a,()->600,wait->{throw new AssertionError("no wait/retry after wrong whole finish");},()->{attempts.incrementAndGet();return OriginalAuthSignal.complete(changed.toFile());});}
            catch(IllegalStateException expected){failed=true;}need(failed && attempts.get()==1);cases++;
            Files.delete(partial);Files.delete(changed);
        }finally{Files.delete(temporary);}
        need(OriginalSyncImageGate.imageIdentity("cc.kronecker.opennexus","android.widget.Image",true,88,88,true,false,false,true));identity++;
        for(String mode:new String[]{"dev","foreign","null","class","alt","window","negative-window","hidden","editable","password","outside"}) {
            need(!OriginalSyncImageGate.imageIdentity(mode.equals("dev")?"cc.kronecker.opennexus.dev":mode.equals("foreign")?"other":mode.equals("null")?null:"cc.kronecker.opennexus",
                mode.equals("class")?"android.view.View":"android.widget.Image",!mode.equals("alt"),mode.equals("window")?89:mode.equals("negative-window")?-1:88,88,
                !mode.equals("hidden"),mode.equals("editable"),mode.equals("password"),!mode.equals("outside")));identity++;
        }
        int occlusion=0;
        for(boolean[] values:new boolean[][]{{true,true,false,false,true},{true,true,true,false,false},{true,true,false,true,false},
            {false,true,false,false,false},{true,false,false,false,false},{false,false,false,false,false},
            {true,true,true,true,false},{true,true,false,false,true}}) {
            need(OriginalSyncImageGate.occluded(values[0],values[1],values[2],values[3])==values[4]);occlusion++;
        }
        need(cases==19 && identity==12 && occlusion==8);
        System.out.println("ORIGINAL_SYNC_IMAGE_GUI_GATE_OK cases=19 identity_checks=12 occlusion_checks=8 real_finish_files=2 actual_GUI_or_pixels=0");
    }
}
