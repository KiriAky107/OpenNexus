package cc.kronecker.opennexus.testdriver;

/** Read the exact original property value; never coerce arbitrary AX text. */
final class OriginalCanvasProperty {
    static boolean visible(int[] control,int[] panel,int[] heading,boolean owned,boolean axVisible) {
        if(!owned||!axVisible||!rect(control)||!rect(panel)||!rect(heading))return false;
        if(heading[0]<panel[0]||heading[1]<panel[1]||heading[2]>panel[2]||heading[3]>panel[3])return false;
        long x=((long)control[0]+control[2])/2,y=((long)control[1]+control[3])/2;
        return x>panel[0]&&x<panel[2]&&y>heading[3]&&y<panel[3];
    }

    private static boolean rect(int[] value) {
        if(value==null||value.length!=4||value[0]>=value[2]||value[1]>=value[3])return false;
        for(int coordinate:value)if(coordinate< -65536||coordinate>65536)return false;
        return true;
    }

    static boolean matches(String name, String expected, String observed, boolean named) {
        if (name == null || expected == null || observed == null) return false;
        if (name.equals("x") || name.equals("y")) {
            if (!expected.matches("-?[0-9]+(?:\\.[0-9]+)?")) return false;
            // Chromium's number control exposes its value and associated label
            // together, e.g. the actual owned API36 field was "48, x".
            return named && observed.equals(expected) || observed.equals(expected + ", " + name);
        }
        return name.equals("Node content") && named && observed.equals(expected);
    }
}
