"""Execute the original Java readonly readiness gate against real AX-shape cases."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
from test_android_editor_readiness import method

class FilePageReadiness(unittest.TestCase):
    def test_document_without_back_is_ready_but_landing_foreign_and_ambiguous_nodes_are_not(self):
        home=os.environ.get('ANDROID_PROBE_JAVA_HOME') or os.environ.get('JAVA_HOME')
        executable=lambda name:str(Path(home)/'bin'/(name+('.exe' if os.name=='nt' else ''))) if home else shutil.which(name)
        javac,java=executable('javac'),executable('java');self.assertTrue(javac and java,'JDK required for bounded driver contract')
        source=(Path(__file__).parent/'android-runtime/RuntimeProbe.java').read_text('utf-8')
        actual=method(source,'private void waitFilePageReady(String path)')
        code=r'''
import java.util.*;
public class FilePageReadinessHarness {
    static final String APP="cc.kronecker.opennexus.dev";final StringBuilder progress=new StringBuilder();
    static class SystemClock {static long now;static long uptimeMillis(){return now;}static void sleep(long n){now+=10000;}}
    static class Rect {int left=0,top=0,right=100,bottom=100;boolean isEmpty(){return right<=left||bottom<=top;}int width(){return right-left;}int height(){return bottom-top;}}
    static class AccessibilityNodeInfo {
        String owner=APP,text="",role="android.widget.TextView",name="";boolean visible=true,enabled=true,clickable=false,editable=false,header=false;int width=100,height=100;
        String getPackageName(){return owner;}String getClassName(){return role;}String getText(){return text;}
        boolean isVisibleToUser(){return visible;}boolean isEnabled(){return enabled;}boolean isClickable(){return clickable;}boolean isEditable(){return editable;}
        void getBoundsInScreen(Rect r){r.right=width;r.bottom=height;}
    }
    final List<AccessibilityNodeInfo> data=new ArrayList<>();
    class Ui {AccessibilityNodeInfo getRootInActiveWindow(){return new AccessibilityNodeInfo();}}final Ui ui=new Ui();
    List<AccessibilityNodeInfo> nodes(){return data;}
    static String string(CharSequence s){return s==null?"":s.toString();}
    boolean label(AccessibilityNodeInfo n,String value,boolean prefix){return value.equals(n.text)||value.equals(n.name);}
    boolean titleBarControl(AccessibilityNodeInfo n){return n.header;}
    static AccessibilityNodeInfo title(){AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.text="reference-target-中文.canvas";n.header=true;return n;}
    static AccessibilityNodeInfo scene(){AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.role="android.widget.Image";n.text="Node connections";return n;}
    static void need(boolean b,String message){if(!b)throw new IllegalStateException(message);}
    METHOD
    public static void main(String[] args){
        FilePageReadinessHarness canvas=new FilePageReadinessHarness();canvas.data.add(title());canvas.data.add(scene());canvas.waitFilePageReady("reference-source.canvas");
        need(canvas.progress.toString().contains("ORIGINAL_CURRENT_DOCUMENT_READY"),"no-Back Canvas was not accepted through actual editor evidence");
        FilePageReadinessHarness markdown=new FilePageReadinessHarness();AccessibilityNodeInfo body=new AccessibilityNodeInfo();body.role="android.widget.EditText";body.editable=true;body.name="Markdown note body";
        AccessibilityNodeInfo t=title();t.text="original.md";markdown.data.add(t);markdown.data.add(body);markdown.waitFilePageReady("next.md");
        for(int mode=0;mode<8;mode++){
            FilePageReadinessHarness bad=new FilePageReadinessHarness();AccessibilityNodeInfo header=title(),content=scene();bad.data.add(header);bad.data.add(content);
            if(mode==0)header.text="OpenNexus";
            if(mode==1)header.owner="other.application";
            if(mode==2)header.header=false;
            if(mode==3)header.clickable=true;
            if(mode==4)content.visible=false;
            if(mode==5)content.height=0;
            if(mode==6)bad.data.add(title());
            if(mode==7)content.owner="other.application";
            SystemClock.now=0;boolean refused=false;try{bad.waitFilePageReady("missing.md");}catch(IllegalStateException error){refused=true;}
            need(refused,"unready/foreign/ambiguous scene accepted: "+mode);
        }
        FilePageReadinessHarness cold=new FilePageReadinessHarness();AccessibilityNodeInfo row=new AccessibilityNodeInfo();row.role="android.widget.Button";row.clickable=true;row.text="new.md";cold.data.add(row);cold.waitFilePageReady("new.md");
        System.out.println("FILE_PAGE_READINESS_OK positive=3 refused=8 no_ui_input=1");
    }
}
'''.replace('    METHOD',actual)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'FilePageReadinessHarness.java';path.write_text(code,'utf-8')
            result=subprocess.run([javac,'-J-Xmx64m','-encoding','UTF-8','-d',str(root),str(path)],capture_output=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            result=subprocess.run([java,'-Xmx64m','-cp',str(root),'FilePageReadinessHarness'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'));self.assertIn(b'FILE_PAGE_READINESS_OK',result.stdout)

if __name__=='__main__':unittest.main()
