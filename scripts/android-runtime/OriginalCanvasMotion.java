package cc.kronecker.opennexus.testdriver;

/** Bounded coordinate plans for one new owned actual-touch scene, never product calls. */
final class OriginalCanvasMotion {
    static final String PATH="motion-original.canvas";
    static final String NODE="Original motion 😀";
    static final String OTHER="https://example.invalid/owned-motion";
    static final String EDGE="Original motion edge";
    static final String[] STEPS={"seed","open","move","resize","pan","pinch","cancel","takeover","background","cold","inspector"};
    static boolean phase(String value) {
        for(String step:STEPS)if(("motion-"+step).equals(value))return true;
        return false;
    }
    static void rect(int[] value) {
        if(value==null||value.length!=4)throw new IllegalArgumentException("MOTION_RECT_REFUSED");
        for(int n:value)if(n< -131072||n>131072)throw new IllegalArgumentException("MOTION_RECT_REFUSED");
        if(value[2]<=value[0]||value[3]<=value[1])throw new IllegalArgumentException("MOTION_RECT_REFUSED");
    }
    static boolean contains(int[] box,float x,float y,int margin) {
        rect(box);
        return Float.isFinite(x)&&Float.isFinite(y)&&margin>=0&&margin<=64
            &&x>=box[0]+margin&&x<box[2]-margin&&y>=box[1]+margin&&y<box[3]-margin;
    }
    static boolean clear(int[] clip,int[][] obstacles,float x,float y) {
        if(obstacles==null||obstacles.length>32)throw new IllegalArgumentException("MOTION_OBSTACLES_REFUSED");
        if(!contains(clip,x,y,16))return false;
        for(int[] obstacle:obstacles) {
            rect(obstacle);
            if(x>=obstacle[0]-12&&x<obstacle[2]+12&&y>=obstacle[1]-12&&y<obstacle[3]+12)return false;
        }
        return true;
    }
    static float[] drag(int[] node,int[] clip,int[][] controls,int[] resize) {
        rect(node);rect(clip);
        if(!contains(clip,node[0],node[1],4)||!contains(clip,node[2]-1,node[3]-1,4))
            throw new IllegalArgumentException("MOTION_CLIPPED_NODE_REFUSED");
        float x,y;
        if(resize==null) {
            int[] point=OriginalCanvasGraph.interiorPoint(node,clip,controls);x=point[0];y=point[1];
        } else {
            rect(resize);
            if(controls==null||controls.length>32)throw new IllegalArgumentException("MOTION_RESIZE_CONTROL_REFUSED");
            boolean original=false;
            for(int[] control:controls) { rect(control);if(java.util.Arrays.equals(control,resize))original=true; }
            if(!original)throw new IllegalArgumentException("MOTION_RESIZE_CONTROL_REFUSED");
            if(!contains(node,resize[0],resize[1],0)||!contains(node,resize[2]-1,resize[3]-1,0))
                throw new IllegalArgumentException("MOTION_RESIZE_OUTSIDE_NODE");
            x=(resize[0]+resize[2])/2f;y=(resize[1]+resize[3])/2f;
        }
        float endX=x+(node[2]-node[0])/4f,endY=y+(node[3]-node[1])/4f;
        if(!contains(clip,endX,endY,12))throw new IllegalArgumentException("MOTION_END_OUTSIDE_CLIP");
        return new float[]{x,y,endX,endY};
    }
    static float[] emptyPair(int[] clip,int[][] obstacles) {
        rect(clip);float half=Math.min(100,(clip[2]-clip[0])/8f);
        if(half<40)throw new IllegalArgumentException("MOTION_VIEW_TOO_SMALL");
        for(int row:new int[]{8,7,9,2,3,5,4,6,1})for(int column:new int[]{5,4,6,3,7,2,8}) {
            float x=clip[0]+(clip[2]-clip[0])*column/10f,y=clip[1]+(clip[3]-clip[1])*row/10f;
            if(clear(clip,obstacles,x-half,y)&&clear(clip,obstacles,x+half,y)
                &&clear(clip,obstacles,x-half*1.1f,y)&&clear(clip,obstacles,x+half*1.1f,y))
                return new float[]{x-half,y,x+half,y};
        }
        throw new IllegalArgumentException("MOTION_NO_OWNED_EMPTY_PAIR");
    }
    static float[] pan(int[] clip,int[][] obstacles) {
        float[] pair=emptyPair(clip,obstacles);float x=pair[0],y=pair[1];
        for(int dx:new int[]{32,-32})for(int dy:new int[]{24,-24})if(clear(clip,obstacles,x+dx,y+dy))
            return new float[]{x,y,x+dx,y+dy};
        throw new IllegalArgumentException("MOTION_NO_OWNED_EMPTY_PAN");
    }
    static boolean viewObservationStable(int[][] expected,int[][] current,boolean beforeInput) {
        if(expected==null||current==null||expected.length<2||current.length<2||expected.length>34||current.length>34)return false;
        try {
            for(int[] value:expected)rect(value);
            for(int[] value:current)rect(value);
        }catch(IllegalArgumentException refused){return false;}
        if(!java.util.Arrays.equals(expected[1],current[1]))return false;
        if(beforeInput)return java.util.Arrays.deepEquals(expected,current);
        // Native AX may publish a pre-cancellation node rectangle after Home.
        // After input, retain its new rectangle but require the same owned clip.
        // Actual graph/view effects are independently read from the renderer.
        for(int[][] sample:new int[][][]{expected,current}) {
            int[] node=sample[0],clip=sample[1];
            if(!contains(clip,node[0],node[1],0)||!contains(clip,node[2]-1,node[3]-1,0))return false;
        }
        return true;
    }
}
