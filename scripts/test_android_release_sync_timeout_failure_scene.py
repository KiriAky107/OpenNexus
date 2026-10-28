"""Actual API36 compile and JVM bounded failure facts, no Android UI capture."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_android_release_auth_scene import STUBS

ROOT=Path(__file__).resolve().parent


class TimeoutFailureEntry(unittest.TestCase):
    def test_actual_API36_and_JVM_closed_failure_facts_preserve_action_state(self):
        suffix='.exe' if os.name=='nt' else '';jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and java.is_file() and sdk.is_file())
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={path.relative_to(ROOT.parent).as_posix():dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in sources}
        commands=[]
        def run(args):
            result=subprocess.run(args,capture_output=True,timeout=60)
            commands.append(dict(argv=args,returncode=result.returncode,stdout_base64=base64.b64encode(result.stdout).decode(),stderr_base64=base64.b64encode(result.stderr).decode()))
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace')+result.stdout.decode('utf-8','replace'));return result
        try:
            with tempfile.TemporaryDirectory(prefix='opennexus-timeout-failure-') as directory:
                root=Path(directory);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                stubs=dict(STUBS);stubs['android/app/Instrumentation.java']=stubs['android/app/Instrumentation.java'].replace('public void sendStatus',
                    'public android.content.Context getTargetContext(){throw new AssertionError("No device target in failure fixture");}\n public void sendStatus')
                paths=[]
                for name,value in stubs.items():
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                    *paths,str(ROOT/'android-runtime-tests/ReleaseSyncTimeoutFailureCheck.java')])
                result=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                    'cc.kronecker.opennexus.testdriver.ReleaseSyncTimeoutFailureCheck'])
                self.assertIn(b'RELEASE_SYNC_TIMEOUT_FAILURE_FACTS_OK checks=',result.stdout)
                for name,item in inventory.items():self.assertEqual(hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest(),item['sha256'])
        finally:
            print('RELEASE_SYNC_TIMEOUT_FAILURE_PROOF '+json.dumps(dict(actual_API36_driver_inputs=inventory,commands=commands,
                SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),compiler_max_heap_MiB=128,JVM_max_heap_MiB=64,
                actual_Android_UI_timeout_Native_or_closed_original_failure_cause_verified=False)))


if __name__=='__main__':unittest.main()
