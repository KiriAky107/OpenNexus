package cc.kronecker.opennexus.testdriver;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;

public final class OriginalCanvasGraphCheck {
    private static int checks;
    private static void check(boolean value){if(!value)throw new AssertionError("Original graph contract");checks++;}
    private static void refuses(int[] node,int[] viewport,int[][] controls) {
        try { OriginalCanvasGraph.interiorPoint(node,viewport,controls);throw new AssertionError("Unsafe graph hit accepted"); }
        catch(IllegalArgumentException expected){checks++;}
    }
    public static void main(String[] ignored)throws Exception {
        check(OriginalCanvasGraph.STEPS.length==19);
        HashSet<String> steps=new HashSet<>();
        for(String step:OriginalCanvasGraph.STEPS){check(OriginalCanvasGraph.phase("graph-"+step));steps.add(step);}
        check(steps.size()==19);
        for(String invalid:new String[]{null,"","graph-save","graph-","Graph-seed"})check(!OriginalCanvasGraph.phase(invalid));
        for(String name:new String[]{"x","y","width","height"}) {
            check(OriginalCanvasGraph.field(name,"120",true));check(OriginalCanvasGraph.field(name,"120, "+name,false));
            check(!OriginalCanvasGraph.field(name,"120",false));check(!OriginalCanvasGraph.field(name,"120 extra",true));
        }
        check(OriginalCanvasGraph.field("Node content",OriginalCanvasGraph.TEXT,true));
        check(!OriginalCanvasGraph.field("Node content",OriginalCanvasGraph.TEXT,false));
        check(!OriginalCanvasGraph.field("Other",OriginalCanvasGraph.TEXT,true));
        check(!OriginalCanvasGraph.field("Connection label","x".repeat(4097),true));
        check(OriginalCanvasGraph.actionDecision(1,0,-1,0)==1);
        check(OriginalCanvasGraph.actionDecision(0,1,-1,0)==0);
        check(OriginalCanvasGraph.actionDecision(0,1,-1,3)==2);
        check(OriginalCanvasGraph.actionDecision(0,1,1,3)==0);
        check(OriginalCanvasGraph.actionDecision(0,0,-1,3)==0);
        check(OriginalCanvasGraph.actionDecision(0,1,0,3)==2);
        check(OriginalCanvasGraph.actionDecision(1,1,1,0)==1);
        try { OriginalCanvasGraph.actionDecision(2,1,0,0);throw new AssertionError(); } catch(IllegalArgumentException expected){checks++;}
        try { OriginalCanvasGraph.actionDecision(0,1,0,33);throw new AssertionError(); } catch(IllegalArgumentException expected){checks++;}
        int[] viewport={0,0,1080,2340},node={0,0,300,300};
        check(java.util.Arrays.equals(OriginalCanvasGraph.interiorPoint(node,viewport,new int[][]{{150,0,300,180}}),new int[]{75,225}));
        // Preserved original6d5 pre-action AX geometry, not a claim about its later tap frame.
        check(java.util.Arrays.equals(OriginalCanvasGraph.interiorPoint(new int[]{0,1359,315,1624},viewport,
            new int[][]{{162,1385,280,1503}}),new int[]{78,1557}));
        check(java.util.Arrays.equals(OriginalCanvasGraph.interiorPoint(new int[]{-100,0,300,300},new int[]{50,50,250,250},
            new int[0][]),new int[]{100,200}));
        int[] avoided=OriginalCanvasGraph.interiorPoint(node,viewport,new int[][]{{60,210,90,240}});
        check(!java.util.Arrays.equals(avoided,new int[]{75,225}));
        refuses(null,viewport,new int[0][]);
        refuses(new int[]{0,0,0,300},viewport,new int[0][]);
        refuses(new int[]{0,0,131073,300},viewport,new int[0][]);
        refuses(node,new int[]{-1,0,1080,2340},new int[0][]);
        refuses(node,viewport,null);
        refuses(node,viewport,new int[33][]);
        refuses(node,viewport,new int[][]{{0,0,0,1}});
        refuses(node,viewport,new int[][]{{0,0,300,300}});
        refuses(new int[]{0,0,11,300},viewport,new int[0][]);
        refuses(new int[]{1100,0,1400,300},viewport,new int[0][]);
        System.out.println("ORIGINAL_CANVAS_GRAPH_DATA_OK "+checks);
        java.util.List<String> values=new java.util.ArrayList<>(java.util.List.of(OriginalCanvasGraph.PATH,OriginalCanvasGraph.SEED_A,
            OriginalCanvasGraph.SEED_B,OriginalCanvasGraph.TEXT,OriginalCanvasGraph.GROUP,OriginalCanvasGraph.FILE,OriginalCanvasGraph.URL,OriginalCanvasGraph.EDGE));
        for(String step:OriginalCanvasGraph.STEPS)values.add("graph-"+step);
        java.util.List<String> hashes=new java.util.ArrayList<>();
        for(String value:values)hashes.add(java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))));
        System.out.println(String.join(" ",hashes));
    }
}
