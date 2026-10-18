package cc.kronecker.opennexus.testdriver;

public final class OriginalCanvasViewFrameCheck {
    private static int count;
    private static void need(boolean yes){count++;if(!yes)throw new AssertionError("VIEW_FRAME_GUARD_FAILED");}
    public static void main(String[] args) {
        int[][] before={{100,100,420,380},{0,0,1000,1000},{332,292,420,380}};
        int[][] after={{180,170,500,450},{0,0,1000,1000},{412,362,500,450}};
        need(OriginalCanvasMotion.viewObservationStable(before,before,true));
        need(OriginalCanvasMotion.viewObservationStable(before,before,false));
        need(!OriginalCanvasMotion.viewObservationStable(before,after,true));
        need(OriginalCanvasMotion.viewObservationStable(before,after,false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{180,170,500,450},{1,0,1000,1000}},false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{-1,170,500,450},{0,0,1000,1000}},false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{180,170,1001,450},{0,0,1000,1000}},false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{180,170,500,1001},{0,0,1000,1000}},false));
        need(!OriginalCanvasMotion.viewObservationStable(before,null,false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[35][],false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{180,170,500,450},{0,0,1000,1000},{0,0,0,1}},false));
        need(!OriginalCanvasMotion.viewObservationStable(before,new int[][]{{180,170,500,450}},false));
        System.out.println("ORIGINAL_CANVAS_VIEW_FRAME_OK "+count);
    }
}
