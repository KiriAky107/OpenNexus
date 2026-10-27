"""Real API36 compile and closed timeout entry/clock/file JVM checks; no device."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_android_release_auth_scene import STUBS

ROOT=Path(__file__).resolve().parent


class TimeoutEntry(unittest.TestCase):
    def test_actual_API36_timeout_entry_and_original_clock_signal_contract(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and java.is_file() and sdk.is_file(),'actual JDK/API36 required')
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={p.relative_to(ROOT.parent).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources}
        commands=[]
        def run(argv):
            result=subprocess.run(argv,capture_output=True,timeout=60)
            commands.append(dict(argv=argv,returncode=result.returncode,
                stdout=result.stdout.decode('utf-8','replace'),stderr=result.stderr.decode('utf-8','replace'),
                stdout_base64=base64.b64encode(result.stdout).decode('ascii'),stderr_base64=base64.b64encode(result.stderr).decode('ascii')))
            self.assertEqual(result.returncode,0,commands[-1]['stderr']+commands[-1]['stdout'])
            return result
        try:
            with tempfile.TemporaryDirectory(prefix='opennexus-timeout-entry-') as folder:
                root=Path(folder);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                stubs=dict(STUBS)
                stubs['android/app/Instrumentation.java']=stubs['android/app/Instrumentation.java'].replace('public void sendStatus',
                    'public android.content.Context getTargetContext(){throw new AssertionError("No device target in admission fixture");}\n public void sendStatus')
                paths=[]
                for name,value in stubs.items():
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
                checks=('ReleaseSyncTimeoutEntryCheck','OriginalSyncTimeoutActionsCheck')
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                    *paths,*(str(ROOT/('android-runtime-tests/'+name+'.java')) for name in checks)])
                for name,marker in (('ReleaseSyncTimeoutEntryCheck',b'RELEASE_SYNC_TIMEOUT_ENTRY_OK accepted=2 refused=65 policies=116 actual_Android_timeout=0'),
                    ('OriginalSyncTimeoutActionsCheck',b'ORIGINAL_SYNC_TIMEOUT_ACTIONS_OK checks=')):
                    result=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),'cc.kronecker.opennexus.testdriver.'+name])
                    self.assertIn(marker,result.stdout)
                for path in sources:self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
        finally:
            print('RELEASE_SYNC_TIMEOUT_API36_PROOF '+json.dumps(dict(actual_API36_driver_inputs=len(sources),actual_compile_inputs=inventory,
                commands=commands,SDK_jar=str(sdk),SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),compiler_max_heap_MiB=128,
                JVM_max_heap_MiB=64,actual_Android_timeout_or_GUI_or_Native_verified=False)))

    def test_separate_self_target_timeout_entry_with_original_permissions(self):
        root=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot();ns='{http://schemas.android.com/apk/res/android}'
        entries=[v for v in root.findall('instrumentation') if v.get(ns+'name')=='cc.kronecker.opennexus.testdriver.ReleaseSyncTimeoutRuntimeProbe']
        self.assertEqual(len(entries),1);self.assertEqual(entries[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
        self.assertEqual(entries[0].get(ns+'functionalTest'),'false')
        self.assertEqual([v.get(ns+'name') for v in root.findall('uses-permission')],['cc.kronecker.opennexus.testime.permission.CONTROL'])


if __name__=='__main__':unittest.main()
