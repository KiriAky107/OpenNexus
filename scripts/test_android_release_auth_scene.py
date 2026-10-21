"""Exercise the actual stable entry with a JVM Instrumentation lifecycle harness.

The real driver is compiled against API36 first. Only lifecycle/Bundle are
replaced for local entry admission; no Android input, credentials or TLS run.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
STUBS={
 'android/os/Bundle.java': '''package android.os;
public class Bundle {
 private final java.util.Map<String,String> data=new java.util.HashMap<>();
 public void putString(String key,String value){data.put(key,value);}
 public int size(){return data.size();}
 public String getString(String key,String fallback){String v=data.get(key);return v==null?fallback:v;}
}''',
 'android/app/Instrumentation.java': '''package android.app;
public class Instrumentation {
 public int starts=0,finishes=0,exitCode=-1;public android.os.Bundle result;
 public void onCreate(android.os.Bundle args){} public void onStart(){} public void start(){starts++;}
 public void finish(int code,android.os.Bundle value){finishes++;exitCode=code;result=value;}
 public UiAutomation getUiAutomation(){throw new AssertionError("No UI in local admission check");}
 public android.content.Context getContext(){throw new AssertionError("No credential input in local admission check");}
 public void sendStatus(int code,android.os.Bundle value){throw new AssertionError("No device stage in local check");}
}'''}

class ReleaseAuthEntry(unittest.TestCase):
    def compile(self,argv):
        result=subprocess.run(argv,capture_output=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))

    def test_actual_constructor_and_entry_refuse_extra_input_before_start(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac=jdk/('javac'+suffix);java=jdk/('java'+suffix)
        if not sdk.is_file() or not javac.is_file():self.skipTest('JDK and API36 required')
        with tempfile.TemporaryDirectory(prefix='opennexus-release-auth-entry-') as folder:
            root=Path(folder);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
            names=('ReleaseScene.java','ReleaseAuthScene.java','AuthRuntimeProbe.java','ReleaseAuthRuntimeProbe.java','ReleaseAuthRecoveryRuntimeProbe.java','ReleaseAuthBackgroundRuntimeProbe.java','OriginalAuthSignal.java','TargetStability.java','TouchViewport.java')
            self.compile([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),
                *(str(ROOT/'android-runtime'/name) for name in names)])
            paths=[]
            for name,value in STUBS.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
            self.compile([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),
                '-d',str(harness),*paths,str(ROOT/'android-runtime-tests/ReleaseAuthEntryCheck.java'),str(ROOT/'android-runtime-tests/ReleaseAuthRecoveryEntryCheck.java'),str(ROOT/'android-runtime-tests/ReleaseAuthBackgroundEntryCheck.java'),str(ROOT/'android-runtime-tests/OriginalAuthSignalCheck.java')])
            result=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseAuthEntryCheck'],capture_output=True,check=True,timeout=15)
            self.assertIn(b'RELEASE_AUTH_ENTRY_OK accepted=4 refused=15 actual_Android_TLS_or_auth=0',result.stdout)
            recovery=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseAuthRecoveryEntryCheck'],capture_output=True,check=True,timeout=15)
            self.assertIn(b'RELEASE_AUTH_RECOVERY_ENTRY_OK accepted=3 refused=15 actual_Android_TLS_or_recovery=0',recovery.stdout)

            background=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseAuthBackgroundEntryCheck'],capture_output=True,check=True,timeout=15)
            self.assertIn(b'RELEASE_AUTH_BACKGROUND_ENTRY_OK accepted=3 refused=15 actual_Android_TLS_or_background=0',background.stdout)
            signal=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.OriginalAuthSignalCheck'],capture_output=True,check=True,timeout=15)
            self.assertIn(b'ORIGINAL_AUTH_SIGNAL_OK checks=9 actual_Android_or_auth=0',signal.stdout)

    def test_instrumentation_targets_only_the_separate_driver_package(self):
        root=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot();ns='{http://schemas.android.com/apk/res/android}'
        for name in ('ReleaseAuthRuntimeProbe','ReleaseAuthRecoveryRuntimeProbe','ReleaseAuthBackgroundRuntimeProbe'):
            matches=[e for e in root.findall('instrumentation') if e.get(ns+'name')=='cc.kronecker.opennexus.testdriver.'+name]
            self.assertEqual(len(matches),1);self.assertEqual(matches[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
            self.assertEqual(matches[0].get(ns+'functionalTest'),'false')

if __name__=='__main__':unittest.main()
