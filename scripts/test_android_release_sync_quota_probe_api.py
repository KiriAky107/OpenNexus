"""Compile only the new quota adaptor's API36 closure; run controlled JVM ports."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from test_android_release_auth_scene import STUBS
from test_android_release_sync_timeout_preinput_api import STUBS_PREINPUT

ROOT = Path(__file__).resolve().parent
EXTRA = {
    'android/app/Instrumentation.java': '''package android.app;
public class Instrumentation {
 public static UiAutomation sampleUi;public static android.content.Context sampleContext;
 public static java.util.List<String> events=new java.util.ArrayList<>(),streams=new java.util.ArrayList<>();
 public int starts,finishes,exitCode=-1;
 public void onCreate(android.os.Bundle b){}public void onStart(){}public void start(){starts++;}
 public void finish(int c,android.os.Bundle b){finishes++;exitCode=c;}
 public void sendStatus(int c,android.os.Bundle b){streams.add(b.getString("stream",""));}
 public UiAutomation getUiAutomation(){return sampleUi;}
 public android.content.Context getContext(){return sampleContext;}public android.content.Context getTargetContext(){return sampleContext;}
}''',
    'android/os/SystemClock.java': '''package android.os;
public class SystemClock {
 public static long up=1000,elapsed=5000;public static Runnable upHook,sleepHook;
 public static java.util.function.LongSupplier elapsedHook;
 public static long uptimeMillis(){if(upHook!=null)upHook.run();return up;}
 public static long elapsedRealtime(){return elapsedHook==null?elapsed:elapsedHook.getAsLong();}
 public static void sleep(long n){up+=n;elapsed+=n;if(sleepHook!=null)sleepHook.run();}
}''',
    'cc/kronecker/opennexus/testdriver/SyncRuntimeProbe.java': '''package cc.kronecker.opennexus.testdriver;
import android.view.accessibility.AccessibilityNodeInfo;import android.graphics.Rect;
public class SyncRuntimeProbe extends android.app.Instrumentation {
 protected final String APP="cc.kronecker.opennexus";
 protected String stage="synthetic";
 protected SyncRuntimeProbe(boolean release){if(!release)throw new AssertionError("Not release");}
 @Override public void onCreate(android.os.Bundle args){start();}
 protected String namespace(){return "SYNC";}protected boolean productUiObservationClosed(){return false;}
 protected boolean hostedCycleTouch(String s){return false;}protected boolean hostedCyclePhaseAllowed(String p){return false;}
 protected void awaitHostedCycleTouch(Rect a,Rect b,int w){}protected boolean additionalPhase(String phase)throws Exception{return false;}
 protected void readCurrent(){events.add("read-current");}protected void signedIn(){events.add("signed-in");}
 protected void click(String name){events.add(name);if(name.equals("Sync one cycle now")){
  AccessibilityNodeInfo b=sampleUi.root.children.get(0);
  if(!hostedCycleTouch(name)||!hostedCyclePhaseAllowed(OriginalSyncQuotaActions.PHASE))throw new AssertionError("Unscoped input");
  awaitHostedCycleTouch(new Rect(b.bounds),new Rect(b.bounds),b.window);b.enabled=false;
 }}
 protected AccessibilityNodeInfo waitFor(java.util.function.Predicate<AccessibilityNodeInfo> p,boolean scroll){return sampleUi.root.children.get(0);}
 protected java.util.List<AccessibilityNodeInfo> nodes(){
  java.util.List<AccessibilityNodeInfo> result=new java.util.ArrayList<>();AccessibilityNodeInfo root=sampleUi.getRootInActiveWindow();
  if(root!=null&&APP.equals(text(root.getPackageName())))result.add(root);
  for(int i=0;i<result.size()&&result.size()<2500;i++){AccessibilityNodeInfo n=result.get(i);for(int j=0;j<n.getChildCount();j++){AccessibilityNodeInfo c=n.getChild(j);if(c!=null)result.add(c);}}
  return result;
 }
 protected static String text(CharSequence s){return s==null?"":s.toString();}
 protected static boolean label(AccessibilityNodeInfo n,String s){return s.equals(text(n.getText()))||s.equals(text(n.getContentDescription()));}
 protected static void require(boolean value,String message){if(!value)throw new IllegalStateException(message);}
}''',
    'org/json/JSONArray.java': '''package org.json;
public class JSONArray {
 private final java.util.List<Object> values=new java.util.ArrayList<>();
 public JSONArray put(int n){values.add(n);return this;}
 public String toString(){return values.toString();}
}''',
    'org/json/JSONObject.java': r'''package org.json;
public class JSONObject {
 private final java.util.Map<String,Object> values=new java.util.TreeMap<>();
 public JSONObject put(String key,Object value){values.put(key,value);return this;}
 public JSONObject put(String key,int value){return put(key,Integer.valueOf(value));}
 public JSONObject put(String key,long value){return put(key,Long.valueOf(value));}
 public JSONObject put(String key,boolean value){return put(key,Boolean.valueOf(value));}
 private static String quoted(String text){return "\""+text.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n")+"\"";}
 public String toString(){StringBuilder out=new StringBuilder("{");for(java.util.Map.Entry<String,Object> e:values.entrySet()){
  if(out.length()>1)out.append(',');out.append(quoted(e.getKey())).append(':');Object v=e.getValue();out.append(v instanceof String?quoted((String)v):v.toString());
 }return out.append('}').toString();}
}''',
}


class QuotaProbeAPI(unittest.TestCase):
    def test_API36_and_controlled_original_writer_signal_node_flow(self):
        task = Path(os.environ['ONX_QUOTA_PROBE_ROOT'])
        self.assertTrue(task.is_absolute() and task.is_dir())
        actual, harness, cases = (task/name for name in ('actual','harness','cases'))
        for path in (actual,harness,cases):path.mkdir()
        jdk = Path('E:/java21/bin')
        sdk = Path('C:/Users/KiriAky/AppData/Local/Android/Sdk/platforms/android-36/android.jar')
        self.assertTrue(sdk.is_file() and (jdk/'javac.exe').is_file())
        sources = ROOT/'android-runtime'
        frozen = {p.name:p.read_bytes() for p in sources.glob('*.java')}
        commands = []

        def run(argv):
            result = subprocess.run(list(map(str,argv)), capture_output=True, timeout=90)
            commands.append(dict(argv=list(map(str,argv)),exit_code=result.returncode,
                stdout_bytes=len(result.stdout),stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                stderr_bytes=len(result.stderr),stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
                diagnostic_tail=result.stderr.decode('utf-8','replace')[-5000:]))
            (task/'command-receipts.json').write_text(json.dumps(commands,ensure_ascii=True),encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace')[-10000:])
            return result

        # These Java package sources intentionally live in a flat driver folder,
        # so javac's package-directory sourcepath lookup cannot resolve them.
        # Follow named same-folder dependencies without staging source copies.
        available={p.stem:p for p in sources.glob('*.java')}
        needed={'ReleaseSyncQuotaRuntimeProbe'};pending=list(needed)
        while pending:
            name=pending.pop()
            for token in re.findall(r'\b[A-Z][A-Za-z0-9_]*\b',frozen[name+'.java'].decode('utf-8')):
                if token in available and token not in needed:needed.add(token);pending.append(token)
        self.assertLessEqual(len(needed),80)
        compile_result=run([jdk/'javac.exe','-J-Xmx128m','-J-Duser.language=en','-J-Duser.country=US','-J-Dfile.encoding=UTF-8',
            '-encoding','UTF-8','-verbose','-classpath',sdk,'-d',actual,*[available[n] for n in sorted(needed)]])
        compiler=compile_result.stderr.decode('utf-8','replace').replace(chr(92),'/').lower()
        parsed=[p for p in sources.glob('*.java') if str(p.resolve()).replace(chr(92),'/').lower() in compiler]
        self.assertTrue(any(p.name=='ReleaseSyncQuotaRuntimeProbe.java' for p in parsed))
        stubs=dict(STUBS);stubs.update(STUBS_PREINPUT);stubs.update(EXTRA)
        paths=[]
        for relative,text in stubs.items():
            path=task/'stubs'/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');paths.append(path)
        run([jdk/'javac.exe','-J-Xmx128m','-J-Duser.language=en','-J-Duser.country=US','-J-Dfile.encoding=UTF-8','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),
             '-d',harness,*paths,ROOT/'android-runtime-tests/ReleaseSyncQuotaProbeCheck.java'])
        result=run([jdk/'java.exe','-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                    'cc.kronecker.opennexus.testdriver.ReleaseSyncQuotaProbeCheck',cases])
        vector=json.loads(result.stdout)
        self.assertTrue(vector['passed']);self.assertFalse(vector['actual_Android_UI_or_Native'])
        records=vector.pop('synthetic_records');parent,ready,failed=(records[k] for k in ('parent','ready','failed'))
        self.assertEqual(set(parent),{'schema','phase','parent_intent_id','input_intent_id','native_invocation_id_observed'})
        self.assertEqual(len(ready),13);self.assertEqual(len(failed),15)
        self.assertFalse(parent['native_invocation_id_observed'])
        self.assertEqual((parent['parent_intent_id'],parent['input_intent_id']),(ready['parent_intent_id'],ready['driver_intent_id']))
        self.assertEqual((parent['parent_intent_id'],parent['input_intent_id']),(failed['parent_intent_id'],failed['input_intent_id']))
        # Context waiting precedes ready; its uptime is never used as ready's
        # elapsedRealtime origin. The controlled elapsed clock advances 100ms.
        self.assertEqual(ready['elapsed_ms'],5100);self.assertTrue(5100<=failed['elapsed_ms']<=41100)
        self.assertEqual(failed['elapsed_ms'],failed['scene_scan_elapsed_ms']);self.assertEqual(failed['scene_counts'],[1,1,1,1,0,0,0,0])
        self.assertFalse(failed['native_original_state_verified']);self.assertFalse(failed['raw_UI_recorded'])
        self.assertTrue(all(len(json.dumps(v).encode())<=4096 for v in records.values()))
        self.assertEqual(frozen,{p.name:p.read_bytes() for p in sources.glob('*.java')})
        summary=dict(passed=True,unique_checks=1,Java_commands=len(commands),API36_inputs=[dict(path=p.relative_to(ROOT.parent).as_posix(),bytes=len(frozen[p.name]),sha256=hashlib.sha256(frozen[p.name]).hexdigest()) for p in parsed],
            sdk_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),commands=commands,JVM=vector,controlled_records=records,
            controlled_Base_Android_JSON_owner_clock=True,actual_original_writer_signal_files=True,
            actual_Android_device_Native_or_host_context_verified=False,cleanup_owned_by_outer_PowerShell=True)
        (task/'summary.json').write_text(json.dumps(summary,ensure_ascii=True),encoding='utf-8')


if __name__=='__main__':unittest.main()
