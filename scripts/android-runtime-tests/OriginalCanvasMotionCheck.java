package cc.kronecker.opennexus.testdriver;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public final class OriginalCanvasMotionCheck {
    private static int count;
    private static void need(boolean yes) { count++;if(!yes)throw new AssertionError("MOTION_DATA_FAILED"); }
    private static void refuse(Runnable call) { boolean refused=false;try{call.run();}catch(IllegalArgumentException yes){refused=true;}need(refused); }
    private static String hash(String value)throws Exception {
        byte[] raw=MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));StringBuilder out=new StringBuilder();
        for(byte b:raw)out.append(String.format("%02x",b&255));return out.toString();
    }
    public static void main(String[] args)throws Exception {
        for(String step:OriginalCanvasMotion.STEPS)need(OriginalCanvasMotion.phase("motion-"+step));
        for(String wrong:new String[]{"seed","motion-repeat","graph-open","motion-move-again","../motion-open"})need(!OriginalCanvasMotion.phase(wrong));
        int[] clip={0,0,1000,1000},node={100,100,420,380},resize={332,292,420,380};
        float[] plan=OriginalCanvasMotion.drag(node,clip,new int[][]{resize},null);
        need(plan[2]-plan[0]==80&&plan[3]-plan[1]==70);
        need(!(plan[0]>=resize[0]&&plan[1]>=resize[1]));
        plan=OriginalCanvasMotion.drag(node,clip,new int[][]{resize},resize);
        need(plan[0]==376&&plan[1]==336&&plan[2]==456&&plan[3]==406);
        refuse(()->OriginalCanvasMotion.drag(new int[]{-1,0,319,280},clip,new int[0][],null));
        refuse(()->OriginalCanvasMotion.drag(new int[]{700,700,999,999},clip,new int[0][],new int[]{955,955,999,999}));
        refuse(()->OriginalCanvasMotion.drag(node,clip,new int[0][],new int[]{0,0,44,44}));
        refuse(()->OriginalCanvasMotion.drag(node,clip,new int[0][],resize));
        refuse(()->OriginalCanvasMotion.drag(node,clip,null,resize));
        refuse(()->OriginalCanvasMotion.rect(new int[]{0,0,0,1}));
        refuse(()->OriginalCanvasMotion.rect(new int[]{0,0,1,131073}));
        refuse(()->OriginalCanvasMotion.rect(null));
        need(!OriginalCanvasMotion.contains(clip,Float.NaN,100,12));
        need(!OriginalCanvasMotion.contains(clip,100,Float.POSITIVE_INFINITY,12));
        int[][] obstacles={{100,100,420,380},{600,100,900,400}};
        float[] pair=OriginalCanvasMotion.emptyPair(clip,obstacles);
        need(pair[2]-pair[0]>=80&&pair[1]==pair[3]);
        for(int i=0;i<4;i+=2)need(OriginalCanvasMotion.clear(clip,obstacles,pair[i],pair[i+1]));
        plan=OriginalCanvasMotion.pan(clip,obstacles);
        need(Math.abs(plan[2]-plan[0])==32&&Math.abs(plan[3]-plan[1])==24);
        need(!OriginalCanvasMotion.clear(clip,obstacles,110,110));
        need(!OriginalCanvasMotion.clear(clip,obstacles,95,110));
        refuse(()->OriginalCanvasMotion.emptyPair(clip,new int[][]{{0,0,1000,1000}}));
        refuse(()->OriginalCanvasMotion.emptyPair(new int[]{0,0,100,100},new int[0][]));
        refuse(()->OriginalCanvasMotion.clear(clip,new int[33][],20,20));
        refuse(()->OriginalCanvasMotion.clear(clip,new int[][]{{0,0,0,0}},20,20));
        System.out.println("ORIGINAL_CANVAS_MOTION_DATA_OK "+count);
        StringBuilder fingerprints=new StringBuilder(hash(OriginalCanvasMotion.PATH)+" "+hash(OriginalCanvasMotion.NODE)+" "+hash(OriginalCanvasMotion.OTHER)+" "+hash(OriginalCanvasMotion.EDGE));
        for(String step:OriginalCanvasMotion.STEPS)fingerprints.append(' ').append(hash("motion-"+step));
        System.out.println(fingerprints);
    }
}
