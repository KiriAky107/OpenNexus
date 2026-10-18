package cc.kronecker.opennexus.testdriver;

/** Bounded data and exact role/value admission for one new owned graph scene. */
final class OriginalCanvasGraph {
    static final String PATH="graph-original.canvas";
    static final String SEED_A="Original alpha", SEED_B="Original beta";
    static final String TEXT="实际中文输入 😀", GROUP="实际分组 😀";
    static final String FILE="资料/原笔记😀.md", URL="https://example.invalid/owned-canvas";
    static final String EDGE="实际连接 😀";
    static final String[] STEPS={"seed","open","add-text","add-file","add-link","add-group",
        "connect","edge-label","copy","duplicate","undo-duplicate","paste","undo","redo",
        "delete","undo-delete","redo-delete","fit","cold"};
    static boolean phase(String value) {
        for(String step:STEPS)if(("graph-"+step).equals(value))return true;
        return false;
    }
    // 0: read again; 1: activate the exact visible action; 2: open once.
    static int actionDecision(int actions,int triggers,int expanded,int stableAbsent) {
        if(actions<0||actions>1||triggers<0||triggers>1||expanded< -1||expanded>1||stableAbsent<0||stableAbsent>32)
            throw new IllegalArgumentException("OWNED_GRAPH_OBSERVATION_REFUSED");
        if(actions==1)return 1;
        return triggers==1&&expanded!=1&&stableAbsent>=3?2:0;
    }
    // Screen rectangles only. Choose a bounded interior point, never a child
    // button's centre; a short link node can have its Open button at the centre.
    static int[] interiorPoint(int[] node,int[] viewport,int[][] controls) {
        rectangle(node);rectangle(viewport);
        if(viewport[0]<0||viewport[1]<0||controls==null||controls.length>32)
            throw new IllegalArgumentException("OWNED_GRAPH_HIT_OBSERVATION_REFUSED");
        for(int[] control:controls)rectangle(control);
        int left=Math.max(node[0],viewport[0]),top=Math.max(node[1],viewport[1]);
        int right=Math.min(node[2],viewport[2]),bottom=Math.min(node[3],viewport[3]);
        if(right-left<12||bottom-top<12)throw new IllegalArgumentException("OWNED_GRAPH_NO_INTERIOR");
        int inset=Math.max(3,Math.min(right-left,bottom-top)/32);
        // Try lower-left first, away from header Open and bottom-right resize.
        int[][] fractions={{2,6},{4,6},{2,4},{4,4},{6,4},{2,2},{4,2},{6,2},{6,6}};
        for(int[] fraction:fractions) {
            int x=left+(right-left)*fraction[0]/8,y=top+(bottom-top)*fraction[1]/8;
            if(x<left+inset||x>=right-inset||y<top+inset||y>=bottom-inset)continue;
            boolean blocked=false;
            for(int[] control:controls)if(x>=control[0]-inset&&x<control[2]+inset
                &&y>=control[1]-inset&&y<control[3]+inset) { blocked=true;break; }
            if(!blocked)return new int[]{x,y};
        }
        throw new IllegalArgumentException("OWNED_GRAPH_NO_NONINTERACTIVE_POINT");
    }
    private static void rectangle(int[] bounds) {
        if(bounds==null||bounds.length!=4)throw new IllegalArgumentException("OWNED_GRAPH_RECT_REFUSED");
        for(int coordinate:bounds)if(coordinate< -131072||coordinate>131072)
            throw new IllegalArgumentException("OWNED_GRAPH_RECT_REFUSED");
        if(bounds[2]<=bounds[0]||bounds[3]<=bounds[1])throw new IllegalArgumentException("OWNED_GRAPH_RECT_REFUSED");
    }
    static boolean field(String name,String observed,boolean named) {
        if(name==null||observed==null||observed.length()>4096)return false;
        if(name.equals("Node content")||name.equals("Connection label"))return named;
        if(!name.equals("x")&&!name.equals("y")&&!name.equals("width")&&!name.equals("height"))return false;
        return named&&observed.matches("-?[0-9]+(?:\\.[0-9]+)?")
            || observed.matches("-?[0-9]+(?:\\.[0-9]+)?, "+name);
    }
}
