"""Necessary API36 compile and controlled original flow/node paths, no live Android."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

from test_android_release_auth_scene import STUBS
from test_android_release_sync_timeout_alert_diagnostics import method

ROOT = Path(__file__).resolve().parent

STUBS_PREINPUT = {
    'android/app/Instrumentation.java': '''package android.app;
public class Instrumentation {
 public static UiAutomation sampleUi; public static android.content.Context sampleContext;
 public void onCreate(android.os.Bundle b){} public void onStart(){} public void start(){}
 public void finish(int c,android.os.Bundle b){} public void sendStatus(int c,android.os.Bundle b){}
 public UiAutomation getUiAutomation(){return sampleUi;}
 public android.content.Context getContext(){return sampleContext;} public android.content.Context getTargetContext(){return sampleContext;}
}''',
    'android/app/UiAutomation.java': '''package android.app;
public class UiAutomation {
 public android.view.accessibility.AccessibilityNodeInfo root;public int rootReads;
 public java.util.List<android.view.accessibility.AccessibilityWindowInfo> windows=new java.util.ArrayList<>();
 public boolean clearCache(){return true;}
 public android.view.accessibility.AccessibilityNodeInfo getRootInActiveWindow(){rootReads++;return root;}
 public java.util.List<android.view.accessibility.AccessibilityWindowInfo> getWindows(){return windows;}
}''',
    'android/content/Context.java': '''package android.content;
public class Context {
 public int appUid=12001;private final java.io.File root;public Context(java.io.File r){root=r;}
 public String getPackageName(){return "cc.kronecker.opennexus.testdriver";}
 public java.io.File getNoBackupFilesDir(){return root;}
 public android.content.pm.PackageManager getPackageManager(){return new android.content.pm.PackageManager(appUid);}
}''',
    'android/content/pm/PackageManager.java': '''package android.content.pm;
public class PackageManager {private final int uid;public PackageManager(int n){uid=n;}
 public ApplicationInfo getApplicationInfo(String p,int f){ApplicationInfo a=new ApplicationInfo();a.uid=uid;return a;}}
''',
    'android/content/pm/ApplicationInfo.java': 'package android.content.pm;public class ApplicationInfo {public int uid;}',
    'android/os/Build.java': 'package android.os;public class Build {public static class VERSION {public static int SDK_INT=36;}}',
    'android/os/SystemClock.java': '''package android.os;public class SystemClock {
 public static long time=1000;public static long uptimeMillis(){return time;}
 public static long elapsedRealtime(){throw new AssertionError("Unexpected elapsed ready clock in preinput node fixture");}
 public static void sleep(long n){time+=n;}}
''',
    'android/graphics/Rect.java': '''package android.graphics;
public class Rect {
 public int left,top,right,bottom;public Rect(){}public Rect(int l,int t,int r,int b){set(l,t,r,b);}public Rect(Rect r){set(r);}
 public void set(int l,int t,int r,int b){left=l;top=t;right=r;bottom=b;}public void set(Rect r){set(r.left,r.top,r.right,r.bottom);}
 public int centerX(){return (left+right)/2;}public int centerY(){return (top+bottom)/2;}
 public boolean isEmpty(){return right<=left||bottom<=top;}
 public boolean equals(Object o){if(!(o instanceof Rect))return false;Rect r=(Rect)o;return left==r.left&&top==r.top&&right==r.right&&bottom==r.bottom;}
 public static boolean intersects(Rect a,Rect b){return a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;}
 public String toShortString(){return "synthetic rectangle";}
}''',
    'android/view/accessibility/AccessibilityNodeInfo.java': '''package android.view.accessibility;
public final class AccessibilityNodeInfo {
 public String value,description="",packageName="cc.kronecker.opennexus",className="android.view.View";
 public int window=7,textReads,descriptionReads,actionCalls;public boolean editable,password,clickable,scrollable,poison,enabled=true,visible=true,actionResult=true;
 public AccessibilityNodeInfo parent;public android.graphics.Rect bounds=new android.graphics.Rect();
 public java.util.List<AccessibilityNodeInfo> children=new java.util.ArrayList<>();
 public AccessibilityNodeInfo(String text){value=text;}public void add(AccessibilityNodeInfo n){children.add(n);n.parent=this;}
 public CharSequence getPackageName(){return packageName;}public int getWindowId(){return window;}public CharSequence getClassName(){return className;}
 public boolean isEditable(){return editable;}public boolean isPassword(){return password;}public boolean isEnabled(){return enabled;}
 public boolean isClickable(){return clickable;}public boolean isScrollable(){return scrollable;}public boolean isVisibleToUser(){return visible;}
 public CharSequence getText(){textReads++;if(poison)throw new AssertionError("Private text read");return value;}
 public CharSequence getContentDescription(){descriptionReads++;if(poison)throw new AssertionError("Private description read");return description;}
 public AccessibilityNodeInfo getParent(){return parent;}public int getChildCount(){return children.size();}public AccessibilityNodeInfo getChild(int n){return children.get(n);}
 public void getBoundsInScreen(android.graphics.Rect out){out.set(bounds);}
 public boolean performAction(int n){if(n!=AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId())throw new AssertionError("Wrong preinput action");actionCalls++;return actionResult;}
 public static final class AccessibilityAction {public static final AccessibilityAction ACTION_SHOW_ON_SCREEN=new AccessibilityAction();public int getId(){return 16908342;}}
}''',
    'android/view/accessibility/AccessibilityWindowInfo.java': '''package android.view.accessibility;
public class AccessibilityWindowInfo {
 public static final int TYPE_APPLICATION=1,TYPE_INPUT_METHOD=2;public int id=7,type=1,layer=2;
 public android.graphics.Rect bounds=new android.graphics.Rect(0,0,1000,1000);
 public int getId(){return id;}public int getType(){return type;}public int getLayer(){return layer;}public int getDisplayId(){return 0;}
 public boolean isActive(){return true;}public boolean isFocused(){return true;}public void getBoundsInScreen(android.graphics.Rect out){out.set(bounds);}}
''',
}


class PreinputAPI(unittest.TestCase):
    def test_API36_original_writer_flow_and_owned_node_adaptor(self):
        old = subprocess.check_output(['git', 'show', 'e6204204e99cd8c2762f8ff6ef9dadb690c29b0a:scripts/android-runtime/ReleaseSyncTimeoutRuntimeProbe.java'], cwd=ROOT.parent).decode()
        new = (ROOT/'android-runtime/ReleaseSyncTimeoutRuntimeProbe.java').read_text('utf-8')
        unchanged = ['private void guardOwner(', 'private boolean failedState(', 'private void failedGuard(',
                     'static boolean networkAlert(String', 'static boolean networkAlertNode(AccessibilityNodeInfo node,ReleaseSyncTimeoutAlerts.Tally',
                     'private void failureObservation(', 'private void write(']
        for signature in unchanged:
            self.assertEqual(method(old, signature), method(new, signature))
        added = '            if(OriginalSyncTimeoutActions.TIMEOUT.equals(phase))prepareCurrentPublicRegion();\n'
        self.assertEqual(method(old, 'protected boolean additionalPhase('), method(new.replace(added, ''), 'protected boolean additionalPhase('))
        before, after = method(old, 'protected void awaitHostedCycleTouch('), method(new, 'protected void awaitHostedCycleTouch(')
        self.assertEqual(before[before.index('File finish='):], after[after.index('File finish='):])
        self.assertEqual(before.count('SystemClock.elapsedRealtime()'), 1)
        self.assertEqual(after.count('SystemClock.elapsedRealtime()'), 1)
        self.assertEqual(after.count('actions.ready(elapsed)'), 1)
        folder = Path(os.environ['ONX_PREINPUT_FLOW_ROOT'])
        self.assertTrue(folder.is_absolute() and folder.is_dir())
        actual, harness = folder / 'actual', folder / 'harness'
        actual.mkdir(); harness.mkdir(); cases = folder / 'writer-cases'; cases.mkdir()
        jdk = Path('E:/java21/bin'); sdk = Path('C:/Users/KiriAky/AppData/Local/Android/Sdk/platforms/android-36/android.jar')
        sources = sorted((ROOT / 'android-runtime').glob('*.java'))
        before = {f.relative_to(ROOT.parent).as_posix(): dict(bytes=f.stat().st_size, sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources}
        commands = []

        def run(argv):
            result = subprocess.run(argv, capture_output=True, timeout=60)
            commands.append(dict(argv=list(map(str, argv)), exit_code=result.returncode,
                stdout_sha256=hashlib.sha256(result.stdout).hexdigest(), stdout_bytes=len(result.stdout),
                stderr_base64=base64.b64encode(result.stderr).decode()))
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            return result.stdout

        run([str(jdk/'javac.exe'), '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath', str(sdk), '-d', str(actual), *map(str, sources)])
        stubs = dict(STUBS); stubs.update(STUBS_PREINPUT); paths = []
        for name, text in stubs.items():
            path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding='utf-8'); paths.append(str(path))
        checks = ['OriginalSyncTimeoutPreinputFlowCheck', 'ReleaseSyncTimeoutPreinputNodeCheck']
        run([str(jdk/'javac.exe'), '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath', str(actual)+os.pathsep+str(sdk),
             '-d', str(harness), *paths, *[str(ROOT/'android-runtime-tests'/(c+'.java')) for c in checks]])
        cp = os.pathsep.join(map(str, (harness, actual, sdk)))
        flow_raw = run([str(jdk/'java.exe'), '-Xmx64m', '-cp', cp, 'cc.kronecker.opennexus.testdriver.'+checks[0], str(cases)])
        flow = json.loads(flow_raw); self.assertTrue(flow['passed']); self.assertFalse(flow['actual_Android_or_Native'])
        (folder/'flow-vectors.json').write_bytes(flow_raw)
        node = json.loads(run([str(jdk/'java.exe'), '-Xmx64m', '-cp', cp, 'cc.kronecker.opennexus.testdriver.'+checks[1], str(cases)]))
        self.assertTrue(node['passed']); self.assertFalse(node['actual_Android_UI_or_Native'])
        self.assertEqual(before, {f.relative_to(ROOT.parent).as_posix(): dict(bytes=f.stat().st_size, sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources})
        summary = dict(passed=True, API36_inputs=before, API36_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(), commands=commands,
            flow_internal_assertions=flow['assertions'], node_internal_assertions=node['assertions'], actual_Android_or_Native=False,
            task_root=str(folder), cleanup_owned_by_outer_PowerShell=True, JVM_stubs_are_controlled=True,
            unchanged_Probe_methods=unchanged, original_post_ready_finish_tail_equal=True)
        (folder/'API-summary.json').write_text(json.dumps(summary), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
