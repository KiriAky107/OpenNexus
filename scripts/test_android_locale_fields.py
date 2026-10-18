"""Run the real Native locale selector against observed API36 numeric AX names."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
from test_android_editor_readiness import method

class LocaleFieldNames(unittest.TestCase):
    def test_observed_numeric_aliases_are_bound_to_only_reviewed_editable_properties(self):
        root=Path(__file__).parent;source=(root/'android-runtime/RuntimeProbe.java').read_text('utf-8')
        body=method(source,'private void candidateLanguageRead(String phase)')
        start='for(AccessibilityNodeInfo node:nodes())if(';offset=body.index(start)+len(start)
        expression=body[offset:body.index(')matches.add(node);',offset)]
        label=method(source,'private static boolean label(AccessibilityNodeInfo node, String text, boolean prefix)')
        home=os.environ.get('ANDROID_PROBE_JAVA_HOME') or os.environ.get('JAVA_HOME')
        executable=lambda name:str(Path(home)/'bin'/(name+('.exe' if os.name=='nt' else ''))) if home else shutil.which(name)
        javac,java=executable('javac'),executable('java');self.assertTrue(javac and java,'JDK required for bounded selector contract')
        code=r'''
package cc.kronecker.opennexus.testdriver;
public class LocaleFieldHarness {
    static final String APP="cc.kronecker.opennexus.dev";static int good,bad;
    static class Build {static class VERSION {static final int SDK_INT=36;}}
    static class AccessibilityNodeInfo {
        String owner=APP,role="android.widget.EditText",text="",hint="",description="";
        boolean enabled=true,editable=true,clickable=true,inspector=true;
        String getPackageName(){return owner;}String getClassName(){return role;}String getText(){return text;}
        String getHintText(){return hint;}String getContentDescription(){return description;}
        boolean isEnabled(){return enabled;}boolean isEditable(){return editable;}boolean isClickable(){return clickable;}
    }
    static String string(CharSequence s){return s==null?"":s.toString();}
    static boolean within(AccessibilityNodeInfo node,String region){return node.inspector;}
    LABEL
    static boolean selected(AccessibilityNodeInfo node,String name){String region="Canvas nodes and connections";return EXPRESSION;}
    static void need(boolean expected,AccessibilityNodeInfo n,String name){
        boolean actual=selected(n,name);if(actual!=expected)throw new IllegalStateException("Actual Native selector mismatch name="+name+" value="+n.text+" expected="+expected);
        if(expected)good++;else bad++;
    }
    public static void main(String[] args){
        String[] names={"x","y","width","height","横坐标","纵坐标","宽度","高度"};
        String[] values={"0","-2.5","300","140","0","-2.5","300","140"};
        for(int i=0;i<names.length;i++){AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.text=values[i]+", "+names[i];need(true,n,names[i]);}
        for(String name:new String[]{"Node content","Label","Node color","节点内容","标签","节点颜色"}){
            AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.text=name.contains("color")||name.equals("节点颜色")?"Default":"原文 😀";n.hint=name;need(true,n,name);
        }
        for(int i=0;i<15;i++){
            AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.text="0, x";String name="x";
            if(i==0)n.owner="other.application";
            if(i==1)n.inspector=false;
            if(i==2)n.enabled=false;
            if(i==3)n.editable=false;
            if(i==4)n.hint="Node content";
            if(i==5)n.description="Label";
            if(i==6)n.text="NaN, x";
            if(i==7)n.text="Infinity, x";
            if(i==8)n.text="original body, x";
            if(i==9)n.text="0, x extra";
            if(i==10)n.text="0, y";
            if(i==11)name="unreviewed";
            if(i==12)n.text=" 0, x";
            if(i==13)n.text="0, x ";
            if(i==14)n.text=new String(new char[65]).replace('\0','1')+", x";
            need(false,n,name);
        }
        System.out.println("LOCALE_FIELD_SELECTOR_OK positive="+good+" refused="+bad+" no_Native_UI_claim=1");
    }
}
'''.replace('    LABEL',label).replace('EXPRESSION',expression)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);target=path/'LocaleFieldHarness.java';target.write_text(code,'utf-8')
            result=subprocess.run([javac,'-J-Xmx64m','-encoding','UTF-8','-d',str(path),str(root/'android-runtime/OriginalCanvasProperty.java'),str(target)],capture_output=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            result=subprocess.run([java,'-Xmx64m','-cp',str(path),'cc.kronecker.opennexus.testdriver.LocaleFieldHarness'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'));self.assertIn(b'positive=14 refused=15',result.stdout)

if __name__=='__main__':unittest.main()
