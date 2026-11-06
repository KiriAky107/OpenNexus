"""New signed association producer: API36 closure and controlled JVM lifecycle.

The new producer, preview admission and exclusive intent writer execute. Base,
Android and JSON are controlled ports, not Android, Native or server evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from test_android_release_sync_timeout_preinput_api import STUBS_PREINPUT

ROOT = Path(__file__).resolve().parent
EXTRA = {
    'android/os/Bundle.java': '''package android.os;
public class Bundle {
 private final java.util.Map<String,Object> values=new java.util.HashMap<>();
 public void putString(String k,String v){values.put(k,v);}public void putInt(String k,int v){values.put(k,v);}
 public void putBoolean(String k,boolean v){values.put(k,v);}public Object get(String k){return values.get(k);}
 public int size(){return values.size();}public String getString(String k,String fallback){Object v=get(k);return v instanceof String?(String)v:fallback;}
}''',
    'android/app/Instrumentation.java': '''package android.app;
public class Instrumentation {
 public static UiAutomation sampleUi;public static android.content.Context sampleContext,sampleTarget;
 public static java.util.List<String> events=new java.util.ArrayList<>(),streams=new java.util.ArrayList<>();
 public int starts,finishes,exitCode=-1;
 public void onCreate(android.os.Bundle b){}public void onStart(){}public void start(){starts++;}
 public void finish(int c,android.os.Bundle b){finishes++;exitCode=c;streams.add(b.getString("stream",""));}
 public void sendStatus(int c,android.os.Bundle b){streams.add(b.getString("stream",""));}
 public UiAutomation getUiAutomation(){return sampleUi;}
 public android.content.Context getContext(){return sampleContext;}public android.content.Context getTargetContext(){return sampleTarget;}
}''',
    'android/os/SystemClock.java': '''package android.os;
public class SystemClock {
 public static long time=1000;public static Runnable sleepHook;public static int sleeps;
 public static long uptimeMillis(){return time;}
 public static void sleep(long n){if(n!=150)throw new AssertionError("Preview interval changed");time+=n;sleeps++;if(sleepHook!=null)sleepHook.run();}
}''',
    'cc/kronecker/opennexus/testdriver/SyncRuntimeProbe.java': r'''package cc.kronecker.opennexus.testdriver;
import android.os.Bundle;import android.view.accessibility.AccessibilityNodeInfo;
public class SyncRuntimeProbe extends android.app.Instrumentation {
 protected final String APP="cc.kronecker.opennexus";protected String stage="synthetic";private String phase;
 public static boolean prePhaseFailure;public static int passwordEdits,failureShapeReads;
 public static java.util.function.Consumer<String> clickHook;
 protected SyncRuntimeProbe(boolean release){if(!release)throw new AssertionError("Not signed release route");}
 @Override public void onCreate(Bundle args){phase=args.getString("phase","");start();}
 @Override public void onStart(){try{
  events.add("open-connection");nodes();if(prePhaseFailure)throw new IllegalStateException("synthetic-private-message");
  if(!additionalPhase(phase))throw new IllegalStateException("Unknown phase");
  if(!productUiObservationClosed()){passwordEdits++;nodes();}
  Bundle b=new Bundle();b.putString("stream","OPENNEXUS_"+namespace()+"_PHASE_OK "+phase+"\n");finish(0,b);
 }catch(Throwable error){if(!productUiObservationClosed()){passwordEdits++;failureShapeReads++;nodes();}
  Bundle b=new Bundle();b.putString("stream","CONTROLLED_PHASE_REFUSED "+error.getClass().getSimpleName()+" UI_closed="+productUiObservationClosed()+"\n");finish(1,b);}}
 protected String namespace(){return "SYNC";}protected boolean productUiObservationClosed(){return false;}
 protected boolean additionalPhase(String phase)throws Exception{return false;}
 protected void readCurrent(){events.add("read-current");}protected void signedIn(){events.add("signed-in");}
 protected void previewMerge(){click("Preview merge");}
 protected void click(String name){events.add(name);if(clickHook!=null)clickHook.accept(name);}
 protected AccessibilityNodeInfo waitFor(java.util.function.Predicate<AccessibilityNodeInfo> predicate,boolean scroll){
  for(AccessibilityNodeInfo n:nodes())if(predicate.test(n))return n;throw new IllegalStateException("Controlled element absent");}
 protected void waitPresent(java.util.function.Predicate<AccessibilityNodeInfo> predicate){waitFor(predicate,false);}
 protected java.util.List<AccessibilityNodeInfo> nodes(){
  java.util.List<AccessibilityNodeInfo> result=new java.util.ArrayList<>();AccessibilityNodeInfo root=sampleUi.getRootInActiveWindow();
  if(root!=null&&APP.equals(text(root.getPackageName())))result.add(root);
  for(int i=0;i<result.size()&&result.size()<2500;i++){AccessibilityNodeInfo n=result.get(i);for(int j=0;j<n.getChildCount();j++){AccessibilityNodeInfo c=n.getChild(j);if(c!=null)result.add(c);}}
  return result;
 }
 protected String[] diagnosticCodes(){return new String[]{"SYNC_NETWORK_ERROR","QUOTA_EXCEEDED"};}
 protected final void admitReviewPackage(CharSequence p,boolean password){require(APP.equals(text(p))&&!password,"Review owner changed");}
 protected static String text(CharSequence value){return value==null?"":value.toString();}
 protected static boolean label(AccessibilityNodeInfo n,String s){return s.equals(text(n.getText()))||s.equals(text(n.getContentDescription()));}
 protected static boolean descendantLabel(AccessibilityNodeInfo n,String s,int depth){if(label(n,s))return true;if(depth>=4)return false;
  for(int i=0;i<n.getChildCount();i++)if(descendantLabel(n.getChild(i),s,depth+1))return true;return false;}
 protected static void require(boolean value,String message){if(!value)throw new IllegalStateException(message);}
}''',
    'org/json/JSONObject.java': r'''package org.json;
public class JSONObject {
 private final java.util.Map<String,Object> values=new java.util.TreeMap<>();
 public JSONObject(){}
 public JSONObject(String source){String rest=source.trim();if(!rest.startsWith("{")||!rest.endsWith("}"))throw new IllegalArgumentException("Controlled JSON");
  rest=rest.substring(1,rest.length()-1).trim();if(rest.isEmpty())return;
  java.util.regex.Pattern pair=java.util.regex.Pattern.compile("\\s*\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\"\\s*(,|$)");
  while(!rest.isEmpty()){java.util.regex.Matcher m=pair.matcher(rest);if(!m.lookingAt())throw new IllegalArgumentException("Controlled flat JSON");
   values.put(m.group(1),m.group(2));rest=rest.substring(m.end());}
 }
 public int length(){return values.size();}public String getString(String key){Object value=values.get(key);if(!(value instanceof String))throw new IllegalArgumentException("Controlled string");return (String)value;}
 public String optString(String key){Object value=values.get(key);return value instanceof String?(String)value:"";}
 public JSONObject put(String key,Object value){values.put(key,value);return this;}
 private static String quoted(String value){return "\""+value.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n")+"\"";}
 public String toString(){StringBuilder out=new StringBuilder("{");for(java.util.Map.Entry<String,Object> e:values.entrySet()){
  if(out.length()>1)out.append(',');out.append(quoted(e.getKey())).append(':').append(quoted((String)e.getValue()));}return out.append('}').toString();}
}''',
}


class AssociationBootstrapProbeAPI(unittest.TestCase):
    def test_API36_and_controlled_association_lifecycle(self):
        task = Path(os.environ['ONX_ASSOCIATION_PROBE_ROOT'])
        self.assertTrue(task.is_absolute() and task.is_dir())
        actual, harness, cases = (task/name for name in ('actual', 'harness', 'cases'))
        for path in (actual, harness, cases):
            path.mkdir()
        jdk = Path('E:/java21/bin')
        sdk = Path('C:/Users/KiriAky/AppData/Local/Android/Sdk/platforms/android-36/android.jar')
        self.assertTrue(sdk.is_file() and (jdk/'javac.exe').is_file())
        sources = ROOT/'android-runtime'
        available = {p.stem: p for p in sources.glob('*.java')}
        needed = {'ReleaseSyncAssociationBootstrapRuntimeProbe'}
        pending, frozen = list(needed), {}
        while pending:
            name = pending.pop()
            raw = available[name].read_bytes()
            frozen[name] = raw
            for token in re.findall(r'\b[A-Z][A-Za-z0-9_]*\b', raw.decode('utf-8')):
                if token in available and token not in needed:
                    needed.add(token)
                    pending.append(token)
        self.assertLessEqual(len(needed), 80)
        commands = []

        def run(argv):
            result = subprocess.run(list(map(str, argv)), capture_output=True, timeout=90)
            commands.append(dict(argv=list(map(str, argv)), exit_code=result.returncode,
                stdout_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                stderr_bytes=len(result.stderr), stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
                diagnostic_tail=result.stderr.decode('utf-8', 'replace')[-5000:]))
            (task/'command-receipts.json').write_text(json.dumps(commands), encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[-10000:])
            return result

        compiler = run([jdk/'javac.exe', '-J-Xmx128m', '-J-Duser.language=en', '-J-Duser.country=US',
            '-J-Dfile.encoding=UTF-8', '-encoding', 'UTF-8', '-verbose', '-classpath', sdk,
            '-d', actual, *[available[n] for n in sorted(needed)]])
        compiler_paths = compiler.stderr.decode('utf-8', 'replace').replace(chr(92), '/').lower()
        parsed = [available[n] for n in sorted(needed)
                  if str(available[n].resolve()).replace(chr(92), '/').lower() in compiler_paths]
        self.assertEqual(set(parsed), {available[n] for n in needed})
        stubs = dict(STUBS_PREINPUT)
        stubs.update(EXTRA)
        paths = []
        for relative, value in stubs.items():
            path = task/'stubs'/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8')
            paths.append(path)
        run([jdk/'javac.exe', '-J-Xmx128m', '-J-Duser.language=en', '-J-Duser.country=US',
             '-J-Dfile.encoding=UTF-8', '-encoding', 'UTF-8', '-classpath', str(actual)+os.pathsep+str(sdk),
             '-d', harness, *paths, ROOT/'android-runtime-tests/ReleaseSyncAssociationBootstrapProbeCheck.java'])
        result = run([jdk/'java.exe', '-Xmx64m', '-cp', os.pathsep.join(map(str, (harness, actual, sdk))),
                      'cc.kronecker.opennexus.testdriver.ReleaseSyncAssociationBootstrapProbeCheck', cases])
        vector = json.loads(result.stdout)
        self.assertTrue(vector['passed'])
        self.assertFalse(vector['actual_Android_UI_or_Native'])
        self.assertEqual(vector['remaining_case_entries'], 0)
        self.assertTrue(all(raw == available[name].read_bytes() for name, raw in frozen.items()))
        summary = dict(passed=True, unique_checks=1, Java_commands=len(commands), commands=commands, JVM=vector,
            API36_inputs=[dict(path=p.relative_to(ROOT.parent).as_posix(), bytes=len(frozen[p.stem]),
                              sha256=hashlib.sha256(frozen[p.stem]).hexdigest()) for p in parsed],
            sdk_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),
            controlled_Base_Android_JSON=True, actual_new_producer_preview_and_exclusive_writer=True,
            actual_device_Native_stop_or_host_admission_verified=False, cleanup_owned_by_outer_PowerShell=True)
        (task/'summary.json').write_text(json.dumps(summary), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
