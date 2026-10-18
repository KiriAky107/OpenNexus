package cc.kronecker.opennexus.testdriver;

public final class OriginalCanvasPropertyCheck {
    private static int cases;
    public static void main(String[] args) {
        check(true,"x","48","48, x",false);
        check(true,"y","64","64, y",false);
        check(true,"x","96","96, x",false);
        check(true,"y","128","128, y",false);
        check(true,"x","48","48",true);
        check(true,"y","-2.5","-2.5, y",false);
        check(true,"Node content","Original 😀, x","Original 😀, x",true);
        check(false,"Node content","Original 😀, x","Original 😀, x",false);
        check(false,"Node content","Original 😀, x","Original 😀, y",true);
        check(false,"x","48","48",false);
        check(false,"x","48","48, y",true);
        check(false,"x","48","148, x",false);
        check(false,"x","48","48, width",false);
        check(false,"x","48"," 48, x",false);
        check(false,"x","48","48, x ",false);
        check(false,"x","48","48, x, extra",false);
        check(false,"x","48","48.0, x",false);
        check(false,"width","48","48",true);
        check(false,"x","not numeric","not numeric, x",true);
        check(false,null,"48","48, x",true);
        check(false,"x",null,"48, x",true);
        check(false,"x","48",null,true);
        int[] panel={21,1348,1063,2320},heading={889,1375,1008,1493};
        clip(false,new int[]{52,136,1008,220},panel,heading,true,true);
        clip(true,new int[]{52,1520,1008,1638},panel,heading,true,true);
        clip(false,new int[]{52,2360,1008,2440},panel,heading,true,true);
        clip(false,new int[]{52,1375,1008,1493},panel,heading,true,true);
        clip(false,new int[]{52,1490,1008,1496},panel,heading,true,true);
        clip(true,new int[]{52,1480,1008,1530},panel,heading,true,true);
        clip(false,new int[]{52,1600,52,1700},panel,heading,true,true);
        clip(false,new int[]{52,1600,1008,1600},panel,heading,true,true);
        clip(false,new int[]{52,1600,1008,1700},panel,heading,false,true);
        clip(false,new int[]{52,1600,1008,1700},panel,heading,true,false);
        clip(false,new int[]{52,1600,1008,1700},null,heading,true,true);
        clip(false,new int[]{52,1600,1008,1700},panel,null,true,true);
        clip(false,new int[]{52,1600,1008,1700},panel,new int[]{0,0,40,40},true,true);
        clip(false,new int[]{52,1600,1008,1700},new int[]{21,1348,1063,1348},heading,true,true);
        clip(false,new int[]{52,1600,1008,1700},panel,new int[]{889,1375,1008,2400},true,true);
        clip(false,new int[]{Integer.MIN_VALUE,1600,Integer.MAX_VALUE,1700},panel,heading,true,true);
        System.out.println("ORIGINAL_CANVAS_PROPERTY_OK cases=" + cases);
    }
    private static void clip(boolean wanted,int[] control,int[] panel,int[] heading,boolean owned,boolean visible) {
        cases++;
        if(OriginalCanvasProperty.visible(control,panel,heading,owned,visible)!=wanted)
            throw new AssertionError("Original Canvas property clip changed, case="+cases);
    }
    private static void check(boolean wanted,String name,String before,String actual,boolean named) {
        cases++;
        if (OriginalCanvasProperty.matches(name,before,actual,named)!=wanted)
            throw new AssertionError("Original Canvas property identity changed");
    }
}
