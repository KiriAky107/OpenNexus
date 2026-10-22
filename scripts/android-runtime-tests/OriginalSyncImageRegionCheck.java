package cc.kronecker.opennexus.testdriver;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Real local admission/pipeline code with an in-memory backend, never Android pixels. */
public final class OriginalSyncImageRegionCheck {
    private static void need(boolean value) { if(!value)throw new AssertionError("image crop contract changed"); }
    private static final class Fixture {
        String app="cc.kronecker.opennexus",phase="release-sync-image",nonce="11111111-2222-4333-8444-555555555555";
        int uid=10216,pid=1234,window=88,display=0;
        int[] screen={0,0,1080,1920},canvas={0,200,1080,1800},image={200,600,600,900};
        long ready=1000,now=1500,stable=600;
        OriginalSyncImageRegion.Region admit() { return OriginalSyncImageRegion.admit(app,phase,nonce,uid,pid,window,display,screen,canvas,image,ready,now,stable); }
    }
    private static final class FakeBackend implements OriginalSyncImageRegion.Backend {
        final String fault;int arguments=0,captures=0,encodes=0;List<int[]> seen=new ArrayList<>();
        FakeBackend(String fault){this.fault=fault;}
        public Object arguments(int[] crop,int uid) { arguments++;need(uid==10216);seen.add(crop.clone());return crop; }
        public boolean exactArguments(Object args,int[] crop,int uid) {
            need(Arrays.equals((int[])args,crop));return !fault.equals("bad-built-crop");
        }
        public Object capture(Object args) throws Exception {
            captures++;
            if(fault.equals("unknown-capture"))throw new Exception("local synthetic unknown, no retry");
            return fault.equals("no-buffer")?null:"synthetic 8x8 buffer";
        }
        public byte[] png(Object buffer,int width,int height) throws Exception {
            encodes++;need(width==8 && height==8);
            if(fault.equals("encode"))throw new Exception("local synthetic encode failure");
            byte[] fake=new byte[fault.equals("too-large")?4097:32];
            byte[] signature={(byte)137,80,78,71,13,10,26,10};System.arraycopy(signature,0,fake,0,8);
            if(fault.equals("bad-png"))fake[0]=0;
            return fake; // Protocol-only bytes, not actual PNG decode or renderer evidence.
        }
    }
    public static void main(String[] ignored) throws Exception {
        int refused=0,flow=0;
        Fixture f=new Fixture();OriginalSyncImageRegion.Region region=f.admit();
        int[][] patches=region.patches();
        need(Arrays.deepEquals(patches,new int[][]{{321,671,329,679},{471,671,479,679},{321,821,329,829},{471,821,479,829}}));
        patches[0][0]=0;f.image[0]=0;need(region.patches()[0][0]==321);flow++;
        for(String mode:new String[]{"dev","foreign","null-app","phase","null-phase","nonce","nonce-newline","uid-low","uid-high","pid","window","display",
                "future","expired","clock","unstable","stable-overflow","screen-origin","canvas-outside","image-outside","image-empty","image-small","image-large","rect-shape","null-rect"}) {
            Fixture bad=new Fixture();
            switch(mode) {
                case "dev":bad.app+=".dev";break;
                case "foreign":bad.app+=".other";break;
                case "null-app":bad.app=null;break;
                case "phase":bad.phase="release-sync-read-canvas";break;
                case "null-phase":bad.phase=null;break;
                case "nonce":bad.nonce="../arbitrary";break;
                case "nonce-newline":bad.nonce+="\n";break;
                case "uid-low":bad.uid=9999;break;
                case "uid-high":bad.uid=20000;break;
                case "pid":bad.pid=0;break;
                case "window":bad.window=-1;break;
                case "display":bad.display=1;break;
                case "future":bad.now=999;break;
                case "expired":bad.now=3001;break;
                case "clock":bad.ready=-1;break;
                case "unstable":bad.stable=599;break;
                case "stable-overflow":bad.stable=10001;break;
                case "screen-origin":bad.screen[0]=1;break;
                case "canvas-outside":bad.canvas[2]=1081;break;
                case "image-outside":bad.image[1]=100;break;
                case "image-empty":bad.image[2]=200;break;
                case "image-small":bad.image[2]=263;break;
                case "image-large":bad.screen=new int[]{0,0,4096,4096};bad.canvas=bad.screen.clone();bad.image=new int[]{0,0,3000,3000};break;
                case "rect-shape":bad.image=new int[]{1,2,3};break;
                case "null-rect":bad.image=null;break;
            }
            boolean failed=false;try { bad.admit(); } catch(IllegalStateException expected) { failed=true; }
            need(failed);refused++;
        }
        FakeBackend ok=new FakeBackend("");byte[] packet=OriginalSyncImageRegion.capture(region,ok);
        need(ok.arguments==4 && ok.captures==4 && ok.encodes==4);
        DataInputStream input=new DataInputStream(new ByteArrayInputStream(packet));byte[] magic=new byte["OPENNEXUS_ROI1\n".getBytes(StandardCharsets.US_ASCII).length];input.readFully(magic);
        need(new String(magic,StandardCharsets.US_ASCII).equals("OPENNEXUS_ROI1\n"));need(input.readInt()==4);
        for(int i=0;i<4;i++){need(input.readInt()==32);byte[] bytes=new byte[32];input.readFully(bytes);need((bytes[0]&255)==137);}
        need(input.read()==-1);flow++;
        for(String fault:new String[]{"bad-built-crop","unknown-capture","no-buffer","encode","bad-png","too-large"}) {
            FakeBackend backend=new FakeBackend(fault);boolean failed=false;
            try { OriginalSyncImageRegion.capture(region,backend); } catch(Exception expected) { failed=true; }
            need(failed && backend.arguments==1 && backend.captures==(fault.equals("bad-built-crop")?0:1));
            need(backend.encodes==(fault.equals("bad-built-crop")||fault.equals("unknown-capture")||fault.equals("no-buffer")?0:1));flow++;
        }
        need(refused==25 && flow==8);
        System.out.println("ORIGINAL_SYNC_IMAGE_REGION_OK refused=25 flow_checks=8 original_crops=4 pixels_per_crop=64 no_fullscreen=1 actual_Android_pixels=0");
    }
}
