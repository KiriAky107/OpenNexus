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
    static boolean field(String name,String observed,boolean named) {
        if(name==null||observed==null||observed.length()>4096)return false;
        if(name.equals("Node content")||name.equals("Connection label"))return named;
        if(!name.equals("x")&&!name.equals("y")&&!name.equals("width")&&!name.equals("height"))return false;
        return named&&observed.matches("-?[0-9]+(?:\\.[0-9]+)?")
            || observed.matches("-?[0-9]+(?:\\.[0-9]+)?, "+name);
    }
}
