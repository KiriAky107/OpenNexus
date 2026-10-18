package cc.kronecker.opennexus.testdriver;

/** Read the exact original property value; never coerce arbitrary AX text. */
final class OriginalCanvasProperty {
    /** API36 number controls can expose value+name without a separate hint. */
    static boolean localizedControl(String name,String text,String hint,String description,
                                    boolean editable,boolean clickable,boolean spinner) {
        if(name==null||text==null||hint==null||description==null||text.length()>8192)return false;
        boolean numeric="x".equals(name)||"y".equals(name)||"width".equals(name)||"height".equals(name)
            ||"横坐标".equals(name)||"纵坐标".equals(name)||"宽度".equals(name)||"高度".equals(name);
        boolean body="Node content".equals(name)||"Label".equals(name)||"节点内容".equals(name)||"标签".equals(name);
        boolean color="Node color".equals(name)||"节点颜色".equals(name);
        if(!numeric&&!body&&!color||numeric&&!editable||body&&!editable||color&&!editable&&!clickable&&!spinner)return false;
        if(!hint.isEmpty()&&!hint.equals(name)||!description.isEmpty()&&!description.equals(name))return false;
        if(name.equals(text)||name.equals(hint)||name.equals(description))return true;
        if(!numeric||!text.endsWith(", "+name))return false;
        String number=text.substring(0,text.length()-name.length()-2);
        return number.length()<=64&&number.matches("-?[0-9]+(?:\\.[0-9]+)?");
    }

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
