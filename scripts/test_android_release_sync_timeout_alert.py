"""Real API36/JVM alert classifier anchored to fixed product GUI source."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_android_release_auth_scene import STUBS

ROOT=Path(__file__).resolve().parent
PRODUCT='d118b2ba63c922142f9fadee8772b6ec15791f48'


class TimeoutAlert(unittest.TestCase):
    def test_actual_API36_and_JVM_accept_only_complete_fixed_product_network_alert(self):
        fixed={}
        for name in ('SyncActivity.vue','syncPresentation.ts'):
            path='frontend/src/features/settings/'+name
            raw=subprocess.check_output(['git','show',PRODUCT+':'+path],cwd=ROOT.parent)
            fixed[name]=dict(raw=raw.decode('utf-8'),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        activity,presentation=fixed['SyncActivity.vue']['raw'],fixed['syncPresentation.ts']['raw']
        prefix=re.search(r"t\('[^']*', '([^']+)'\) \}\} · \{\{ syncError\(error\) \}\}",activity)[1]
        message=re.search(r"case 'SYNC_NETWORK_ERROR': message = t\('[^']*', '([^']+)'\)",presentation)[1]
        self.assertIn('return `${message} (${code})`',presentation)
        alert=prefix+' · '+message+' (SYNC_NETWORK_ERROR)'
        self.assertEqual(alert,'Last recorded sync error · Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)')
        suffix='.exe' if os.name=='nt' else '';jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and java.is_file() and sdk.is_file())
        sources=sorted((ROOT/'android-runtime').glob('*.java'));inventory={f.relative_to(ROOT.parent).as_posix():dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources}
        commands=[]
        def run(args):
            result=subprocess.run(args,capture_output=True,timeout=60)
            commands.append(dict(argv=args,returncode=result.returncode,stdout_base64=base64.b64encode(result.stdout).decode(),stderr_base64=base64.b64encode(result.stderr).decode()))
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace')+result.stdout.decode('utf-8','replace'))
            return result
        try:
            with tempfile.TemporaryDirectory(prefix='opennexus-timeout-alert-') as directory:
                root=Path(directory);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                stubs=dict(STUBS);stubs['android/app/Instrumentation.java']=stubs['android/app/Instrumentation.java'].replace('public void sendStatus',
                    'public android.content.Context getTargetContext(){throw new AssertionError("No device target in alert fixture");}\n public void sendStatus')
                paths=[]
                for name,value in stubs.items():
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                    *paths,str(ROOT/'android-runtime-tests/ReleaseSyncTimeoutAlertCheck.java')])
                result=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                    'cc.kronecker.opennexus.testdriver.ReleaseSyncTimeoutAlertCheck',alert])
                self.assertIn(b'RELEASE_SYNC_TIMEOUT_FIXED_ALERT_OK allowed=1 refused=9 scene_boundaries=3 actual_Android_UI=0',result.stdout)
                for name,meta in inventory.items():self.assertEqual(hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest(),meta['sha256'])
        finally:
            print('RELEASE_SYNC_TIMEOUT_ALERT_PROOF '+json.dumps(dict(product_source=PRODUCT,fixed_source_files={k:{n:v for n,v in item.items() if n!='raw'} for k,item in fixed.items()},
                source_bound_full_alert=alert,actual_API36_driver_inputs=inventory,SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),
                commands=commands,actual_Android_UI_timeout_or_Native_cause_verified=False,compiler_max_heap_MiB=128,JVM_max_heap_MiB=64)))


if __name__=='__main__':unittest.main()
