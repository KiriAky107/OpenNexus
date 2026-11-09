"""Affected API36 source closure plus actual new entries on controlled JVM ports.

No actual Android/shared GUI methods, Native, private source factory or ACK runs.
All compiler/stub/class outputs stay in RAM; no build root or deletion is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
STUBS = {
    'android/os/Bundle.java': '''package android.os;
public class Bundle {
 private final java.util.Map<String,Object> values=new java.util.HashMap<>();
 public boolean fail;public Runnable hook;
 public void putString(String k,String v){values.put(k,v);}public void put(String k,Object v){values.put(k,v);}
 public int size(){if(fail)throw new IllegalStateException("SENSITIVE Bundle");return values.size();}
 public Object get(String k){if(hook!=null){Runnable once=hook;hook=null;once.run();}return values.get(k);}
 public String getString(String k,String fallback){Object v=get(k);return v instanceof String?(String)v:fallback;}
}''',
    'android/content/Context.java': '''package android.content;
public class Context {private final String name;public Context(String n){name=n;}public String getPackageName(){return name;}}
''',
    'android/view/accessibility/AccessibilityNodeInfo.java': '''package android.view.accessibility;
public class AccessibilityNodeInfo {}
''',
    'android/app/Instrumentation.java': '''package android.app;
import cc.kronecker.opennexus.testdriver.Control;
public class Instrumentation {
 public void onCreate(android.os.Bundle b){}public void onStart(){}
 public void start(){Control.starts++;}
 public android.content.Context getContext(){return new android.content.Context(Control.context);}
 public android.content.Context getTargetContext(){return new android.content.Context(Control.target);}
 public void finish(int code,android.os.Bundle value){Control.finishCalls++;Control.exit=code;
  Control.streams.add(value.getString("stream","SENSITIVE missing"));
  if(Control.throwFinish)throw new IllegalStateException("SENSITIVE finish");}
 public void sendStatus(int code,android.os.Bundle value){Control.statusCalls++;
  Control.streams.add(value.getString("stream","SENSITIVE missing"));
  if(Control.throwStatus)throw new IllegalStateException("SENSITIVE status");}
}''',
    'cc/kronecker/opennexus/testdriver/Control.java': '''package cc.kronecker.opennexus.testdriver;
import android.os.Bundle;
public final class Control {
 public static final String SEED_OK="OPENNEXUS_SIGNED_QUOTA_SEED_UI note_Save_UI_observed=true raw_UI_recorded=false native_original_state_verified=false\\nOPENNEXUS_SIGNED_QUOTA_SEED_PHASE_OK markdown\\n";
 public static String context,target,mode,namespace;public static Bundle original;
 public static int starts,finishCalls,statusCalls,exit,uiReads,signedReads,passwordEdits,shapeReads;
 public static boolean throwFinish,throwStatus;public static Runnable action,resultAction;
 public static java.util.List<String> streams;
 public static void reset(){context=target="cc.kronecker.opennexus.testdriver";mode="ok";namespace="";original=null;
  starts=finishCalls=statusCalls=uiReads=signedReads=passwordEdits=shapeReads=0;exit=-1;
  throwFinish=throwStatus=false;action=resultAction=null;streams=new java.util.ArrayList<>();}
 public static Bundle result(boolean login){Bundle b=new Bundle();
  if(!mode.equals("missing"))b.putString("stream",login?"OPENNEXUS_SIGNED_QUOTA_LOGIN_PHASE_OK login-a\\n":SEED_OK);
  if(!login)b.putString("phase","markdown");
  if(mode.equals("raw"))b.putString("stream","SENSITIVE RAW_TREE stack endpoint password");
  if(mode.equals("extra"))b.putString("private-extra","SENSITIVE");
  if(mode.equals("object"))b.put("stream",new Object(){public String toString(){throw new AssertionError("Coercion");}});
  if(mode.equals("throw-bundle"))b.fail=true;
  if(mode.equals("reenter-result"))b.hook=resultAction;
  return b;
 }
 public static void act(){if(action!=null){Runnable once=action;action=null;once.run();}}
}''',
    'cc/kronecker/opennexus/testdriver/RuntimeProbe.java': '''package cc.kronecker.opennexus.testdriver;
import android.os.Bundle;import android.view.accessibility.AccessibilityNodeInfo;
public class RuntimeProbe extends android.app.Instrumentation {
 protected RuntimeProbe(boolean release){if(!release)throw new AssertionError("Not fixed release");}
 protected String privatePhaseNamespace(){return null;}
 protected java.util.List<AccessibilityNodeInfo> nodes(){Control.uiReads++;return java.util.Collections.emptyList();}
 @Override public void onCreate(Bundle b){Control.original=b;start();}
 @Override public void onStart(){try{
  Control.namespace=privatePhaseNamespace();
  if(Control.mode.equals("pre-fail"))throw new IllegalStateException("SENSITIVE");
  if(Control.mode.equals("raw-status")){Bundle b=new Bundle();b.putString("stream","SENSITIVE");sendStatus(1,b);}
  nodes();Control.act();nodes();finish(0,Control.result(false));
 }catch(Throwable error){Bundle b=new Bundle();b.putString("stream","SENSITIVE RAW_TREE stack");finish(1,b);}}
}''',
    'cc/kronecker/opennexus/testdriver/AuthRuntimeProbe.java': '''package cc.kronecker.opennexus.testdriver;
import android.os.Bundle;import android.view.accessibility.AccessibilityNodeInfo;
public class AuthRuntimeProbe extends android.app.Instrumentation {
 protected AuthRuntimeProbe(boolean release){if(!release)throw new AssertionError("Not fixed release");}
 protected String namespace(){return "AUTH";}protected boolean viewportAdmission(){return false;}
 protected boolean productUiObservationClosed(){return false;}
 protected java.util.List<AccessibilityNodeInfo> nodes(){Control.uiReads++;return java.util.Collections.emptyList();}
 protected void signedIn(){Control.signedReads++;nodes();}
 @Override public void onCreate(Bundle b){Control.original=b;start();}
 @Override public void onStart(){try{
  if(!viewportAdmission())throw new AssertionError("No viewport gate");
  Control.namespace=namespace();
  if(Control.mode.equals("pre-fail"))throw new IllegalStateException("SENSITIVE");
  if(Control.mode.equals("raw-status")){Bundle b=new Bundle();b.putString("stream","SENSITIVE");sendStatus(1,b);}
  nodes();Control.act();signedIn();
  if(Control.mode.equals("double-signed"))signedIn();
  if(Control.mode.equals("double-status")){Bundle b=new Bundle();b.putString("stream",ReleaseQuotaLoginRuntimeProbe.UI);sendStatus(1,b);}
  if(!productUiObservationClosed()){Control.passwordEdits++;nodes();}
  finish(0,Control.result(true));
 }catch(Throwable error){if(!productUiObservationClosed()){Control.passwordEdits++;Control.shapeReads++;nodes();}
  Bundle b=new Bundle();b.putString("stream","SENSITIVE RAW_TREE stack");finish(1,b);}}
}''',
}


class PrivateQuotaGuiAPI(unittest.TestCase):
    def test_affected_API36_and_private_entry_lifecycle(self):
        sdk = Path('C:/Users/KiriAky/AppData/Local/Android/Sdk/platforms/android-36/android.jar')
        jdk = Path('E:/java21/bin')
        self.assertTrue(sdk.is_file() and (jdk/'java.exe').is_file())
        sources = ROOT/'android-runtime'
        available = {p.stem: p for p in sources.glob('*.java')}
        needed = {'RuntimeProbe', 'AuthRuntimeProbe', 'ReleaseRuntimeProbe',
            'ReleaseSyncImageRuntimeProbe', 'ReleaseSyncReadRuntimeProbe',
            'ReleaseQuotaSeedRuntimeProbe', 'ReleaseQuotaLoginRuntimeProbe'}
        while True:
            expanded = set(needed)
            for name in needed:
                expanded |= set(re.findall(r'\b[A-Z][A-Za-z0-9_]*\b', available[name].read_text('utf-8'))) & set(available)
            if needed == expanded:
                break
            needed = expanded
        frozen = {name: available[name].read_bytes() for name in needed}
        controlled = dict(STUBS)
        controlled['cc/kronecker/opennexus/testdriver/ReleaseQuotaPrivateGuiCheck.java'] = (
            ROOT/'android-runtime-tests/ReleaseQuotaPrivateGuiCheck.java').read_text('utf-8')
        payload = bytearray(struct.pack('>II', 0x4f4e5847, len(controlled)))
        for relative, value in controlled.items():
            for item in (relative, value):
                raw = item.encode('utf-8')
                payload.extend(struct.pack('>I', len(raw)))
                payload.extend(raw)
        command = [jdk/'java.exe', '-Xmx192m', '-XX:-UsePerfData', '-Dfile.encoding=UTF-8',
            '-Duser.language=en', '-Duser.country=US', '--source', '21',
            ROOT/'android-runtime-tests/ReleaseQuotaPrivateGuiMemoryCheck.java', sdk, str(len(needed)),
            *[available[name] for name in sorted(needed)]]
        process_env = os.environ.copy()
        for name in ('CLASSPATH', 'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS'):
            process_env.pop(name, None)
        result = subprocess.run([str(x) for x in command], cwd=ROOT.parent, input=bytes(payload),
            capture_output=True, timeout=120, env=process_env)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[:6000])
        lines = result.stdout.decode('utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        vector, compiler = map(json.loads, lines)
        self.assertEqual(compiler['API36_source_count'], len(needed))
        self.assertTrue(compiler['API36_new_entry_bytecode_reused'])
        self.assertEqual(compiler['filesystem_outputs'], 0)
        self.assertTrue(vector['passed'])
        self.assertEqual(vector['checks'], len(vector['actual_bodies']))
        self.assertEqual(len(vector['actual_bodies']), len(set(vector['actual_bodies'])))
        self.assertFalse(vector['actual_shared_UI_body_executed'])
        self.assertFalse(vector['actual_device_or_Native'])
        self.assertTrue(all(raw == available[name].read_bytes() for name, raw in frozen.items()))
        summary = dict(passed=True, Python_test_bodies=1, Java_processes=1,
            source_launcher_in_memory=True, compiler_tasks_in_memory=2, compiler=compiler, JVM=vector,
            API36_inputs=[dict(path=available[name].relative_to(ROOT.parent).as_posix(), bytes=len(frozen[name]),
                sha256=hashlib.sha256(frozen[name]).hexdigest()) for name in sorted(needed)],
            API36_actual_source_count=len(needed), SDK_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),
            controlled_Android_and_shared_base=True, actual_new_entry_bytecode=True,
            actual_shared_UI_body_executed=False, actual_device_or_Native=False,
            filesystem_output_or_fixture_created=False, no_delete_command_needed_or_used=True)
        type(self).summary = summary


if __name__ == '__main__':
    unittest.main()
