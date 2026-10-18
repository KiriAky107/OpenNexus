"""Execute the real Java navigation/focus methods against a changing AX tree.

This is a bounded JVM contract test, not an actual Android input result.
"""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest

def method(source,signature):
    start=source.index(signature);opening=source.index('{',start);depth=1;end=opening+1
    while depth:
        depth+=int(source[end]=='{')-int(source[end]=='}');end+=1
    return source[start:end]

class EditorReadiness(unittest.TestCase):
    def test_actual_methods_wait_for_current_title_and_reacquire_before_one_focus(self):
        home=os.environ.get('ANDROID_PROBE_JAVA_HOME') or os.environ.get('JAVA_HOME')
        executable=lambda name:str(Path(home)/'bin'/(name+('.exe' if os.name=='nt' else ''))) if home else shutil.which(name)
        javac,java=executable('javac'),executable('java')
        self.assertTrue(javac and java,'A JDK is required for the bounded Android driver contract')
        source=(Path(__file__).parent/'android-runtime/RuntimeProbe.java').read_text('utf-8')
        methods=method(source,'private void create(String button, String path)')+'\n'+method(source,'private void activateNoteBody(AccessibilityNodeInfo observed)')
        harness=r'''
import java.util.*;
import java.util.function.Predicate;
public class EditorReadinessHarness {
    static final String APP="cc.kronecker.opennexus.dev";
    final StringBuilder progress=new StringBuilder();
    static class AccessibilityNodeInfo {
        static final int ACTION_FOCUS=1;
        String owner=APP,text="",name="Markdown note body";boolean editable=true,visible=true,enabled=true,focused=false,clickable=false,title=false,stale=false;
        int focuses=0;
        String getPackageName(){return owner;} String getText(){return text;}
        boolean isEditable(){return editable;} boolean isVisibleToUser(){return visible;}
        boolean isEnabled(){return enabled;} boolean isFocused(){return focused;} boolean isClickable(){return clickable;}
        boolean performAction(int action){if(stale)throw new IllegalStateException("stale editor was focused");require(action==ACTION_FOCUS,"foreign action");focuses++;return true;}
    }
    boolean idle=false,titleValidated=false;int touches=0;String created="";
    final AccessibilityNodeInfo fresh=new AccessibilityNodeInfo();
    static String string(CharSequence s){return s==null?"":s.toString();}
    static void require(boolean b,String message){if(!b)throw new IllegalStateException(message);}
    boolean label(AccessibilityNodeInfo n,String value,boolean prefix){return n.name.equals(value)||n.text.equals(value);}
    boolean titleBarControl(AccessibilityNodeInfo n){return n.title;}
    void click(String name,boolean prefix){} void hideKeyboard(){}
    void setText(AccessibilityNodeInfo n,String value){created=value;}
    void waitLabel(String value){}
    AccessibilityNodeInfo waitNode(Predicate<AccessibilityNodeInfo> filter,String description){
        AccessibilityNodeInfo field=new AccessibilityNodeInfo();field.focused=true;
        if(filter.test(field))return field;
        List<AccessibilityNodeInfo> candidates=new ArrayList<>();
        for(int i=0;i<5;i++){
            AccessibilityNodeInfo n=new AccessibilityNodeInfo();n.editable=false;n.name="";n.text=created;n.title=true;
            if(i==0)n.text="runtime-note.md";
            if(i==1)n.owner="other.application";
            if(i==2)n.clickable=true;
            if(i==3)n.title=false;
            candidates.add(n);
        }
        for(int i=0;i<candidates.size();i++)if(filter.test(candidates.get(i))){
            require(i==4,"accepted old, foreign, clickable or non-title document name");titleValidated=true;return candidates.get(i);
        }
        throw new IllegalStateException("new document title was not observed");
    }
    void settleUi(){idle=true;}
    AccessibilityNodeInfo uniqueTarget(Predicate<AccessibilityNodeInfo> filter){require(idle,"read before idle");require(filter.test(fresh),"fresh editor refused");return fresh;}
    void tapUniqueTarget(Predicate<AccessibilityNodeInfo> filter){require(filter.test(fresh),"tap outside owned current editor");touches++;}
    METHODS
    public static void main(String[] args){
        EditorReadinessHarness create=new EditorReadinessHarness();create.create("New note","runtime-rich.md");
        require(create.titleValidated,"create returned while only the old document Back control existed");
        EditorReadinessHarness focus=new EditorReadinessHarness();AccessibilityNodeInfo stale=new AccessibilityNodeInfo();stale.stale=true;
        focus.activateNoteBody(stale);require(focus.fresh.focuses==1&&focus.touches==1,"focus/touch duplicated or missing");
        EditorReadinessHarness foreign=new EditorReadinessHarness();AccessibilityNodeInfo bad=new AccessibilityNodeInfo();bad.owner="other.application";
        boolean refused=false;try{foreign.activateNoteBody(bad);}catch(IllegalStateException error){refused=true;}
        require(refused&&foreign.fresh.focuses==0&&foreign.touches==0,"foreign editor received input");
        System.out.println("EDITOR_READINESS_CONTRACT_OK title-negative-cases=4 focus=1 touch=1 foreign-input=0");
    }
}
'''.replace('    METHODS',methods)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);target=path/'EditorReadinessHarness.java';target.write_text(harness,'utf-8')
            result=subprocess.run([javac,'-J-Xmx64m','-encoding','UTF-8','-d',str(path),str(target)],capture_output=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            result=subprocess.run([java,'-Xmx64m','-cp',str(path),'EditorReadinessHarness'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            self.assertIn(b'EDITOR_READINESS_CONTRACT_OK',result.stdout)

if __name__=='__main__':unittest.main()
