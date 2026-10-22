package cc.kronecker.opennexus.testdriver;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.nio.charset.StandardCharsets;

/** Four tiny compositor source crops, never a full-display buffer or post-crop. */
final class OriginalSyncImageRegion {
    static final String PHASE = "release-sync-image";
    private static final String APP = "cc.kronecker.opennexus";
    private static final String UUID = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
    private OriginalSyncImageRegion() {}
    static final class Region {
        final int uid;
        private final int[][] patches;
        private Region(int uid, int[][] patches) { this.uid=uid;this.patches=patches; }
        int[][] patches() { int[][] copy=new int[4][];for(int i=0;i<4;i++)copy[i]=patches[i].clone();return copy; }
    }
    static Region admit(String app, String phase, String nonce, int uid, int pid, int window, int display,
                        int[] screen, int[] canvas, int[] image, long ready, long now, long stableMillis) {
        need(APP.equals(app) && PHASE.equals(phase) && nonce!=null && nonce.matches(UUID));
        need(uid>=10000 && uid<=19999 && pid>0 && pid<=10000000 && window>=0 && window<=65535 && display==0);
        need(ready>=0 && now>=ready && now-ready<=2000 && stableMillis>=600 && stableMillis<=10000);
        rect(screen);rect(canvas);rect(image);
        need(screen[0]==0 && screen[1]==0 && inside(screen,canvas) && inside(canvas,image));
        int side=Math.min(image[2]-image[0], image[3]-image[1]);need(side>=64 && side<=2048);
        // img object-fit:contain centers the square 2x2 source in its box.
        // Capture only 8x8 neighborhoods of the four source-pixel centers.
        int left=image[0]+((image[2]-image[0])-side)/2;
        int top=image[1]+((image[3]-image[1])-side)/2;
        int[][] patches=new int[4][];
        for(int i=0;i<4;i++) {
            int x=left+(i%2==0?side/4:side*3/4), y=top+(i<2?side/4:side*3/4);
            patches[i]=new int[]{x-4,y-4,x+4,y+4};rect(patches[i]);
            need(inside(image,patches[i]) && inside(canvas,patches[i]) && inside(screen,patches[i]));
        }
        return new Region(uid,patches);
    }
    interface Backend {
        Object arguments(int[] sourceCrop, int uid) throws Exception;
        boolean exactArguments(Object args, int[] sourceCrop, int uid) throws Exception;
        Object capture(Object args) throws Exception;
        byte[] png(Object buffer, int width, int height) throws Exception;
    }
    static byte[] capture(Region region, Backend backend) throws Exception {
        need(region!=null && backend!=null);
        byte[][] frames=new byte[4][];int[][] patches=region.patches();
        for(int i=0;i<4;i++) {
            Object args=backend.arguments(patches[i].clone(),region.uid);
            // Verify the built request before a compositor can allocate pixels.
            need(backend.exactArguments(args,patches[i].clone(),region.uid));
            Object buffer=backend.capture(args);
            need(buffer!=null);
            frames[i]=backend.png(buffer,8,8);
            need(frames[i]!=null && frames[i].length>=8 && frames[i].length<=4096);
            byte[] signature={(byte)137,80,78,71,13,10,26,10};
            for(int j=0;j<signature.length;j++)need(frames[i][j]==signature[j]);
        }
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        DataOutputStream packet=new DataOutputStream(bytes);
        packet.write("OPENNEXUS_ROI1\n".getBytes(StandardCharsets.US_ASCII));packet.writeInt(4);
        for(byte[] frame:frames){packet.writeInt(frame.length);packet.write(frame);}
        packet.flush();return bytes.toByteArray();
    }
    private static void rect(int[] r) {
        need(r!=null && r.length==4 && r[0]>=0 && r[1]>=0 && r[0]<r[2] && r[1]<r[3] && r[2]<=16384 && r[3]<=16384);
    }
    private static boolean inside(int[] outer,int[] inner) {
        return outer[0]<=inner[0] && outer[1]<=inner[1] && inner[2]<=outer[2] && inner[3]<=outer[3];
    }
    private static void need(boolean value) { if(!value)throw new IllegalStateException("OWNED_IMAGE_REGION_REFUSED"); }
}
