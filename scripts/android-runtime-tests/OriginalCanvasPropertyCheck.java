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
        System.out.println("ORIGINAL_CANVAS_PROPERTY_OK cases=" + cases);
    }
    private static void check(boolean wanted,String name,String before,String actual,boolean named) {
        cases++;
        if (OriginalCanvasProperty.matches(name,before,actual,named)!=wanted)
            throw new AssertionError("Original Canvas property identity changed");
    }
}
