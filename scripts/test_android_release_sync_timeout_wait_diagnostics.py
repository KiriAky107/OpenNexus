"""Actual affected API36 dependency compile and two pure JVM action checks."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent
BASE='431a89f446880fcdd9b39761c179c73cb7612cf3'


def method(source,signature):
    start=source.index('{',source.index(signature));depth=1;end=start+1
    while depth:
        if source[end]=='{':depth+=1
        if source[end]=='}':depth-=1
        end+=1
    return source[start:end]


class WaitDiagnostics(unittest.TestCase):
    def test_API36_and_original_wait_contract_with_no_new_UI_or_clock_reads(self):
        original=subprocess.check_output(['git','show',BASE+':scripts/android-runtime/OriginalSyncTimeoutActions.java'],cwd=ROOT.parent).decode()
        current=(ROOT/'android-runtime/OriginalSyncTimeoutActions.java').read_text('utf-8')
        signatures=['private static void awaitUntil(','static void awaitSignal(','private static void need(',
                    'void awaitRelease(','void recordScene(','static boolean failedScene(','void beforeProductUi(']
        for signature in signatures:self.assertEqual(method(original,signature),method(current,signature))
        restored=re.sub(r'needWait\((now>=last && now<=until|now<until),OriginalSyncTimeoutWaitFacts\.Point\.[A-Z_]+,last,now,until,(?:null|observed|Boolean.TRUE|Boolean.FALSE)\)',r'need(\1)',method(current,'private void awaitFailureUntil('))
        self.assertEqual(restored,method(original,'private static void awaitUntil('))
        self.assertEqual(method(current,'void observeFailure(').replace('waitFailure=null;','').replace('awaitFailureUntil(','awaitUntil(').split(),method(original,'void observeFailure(').split())
        capture=method(current,'private void needWait(')
        self.assertEqual(capture.count('capture('),1);self.assertIn('if(!value)',capture)
        for token in ('clock.','owner.','state.','pause.','SystemClock','getUiAutomation'):self.assertNotIn(token,capture)
        path='scripts/android-runtime/ReleaseSyncTimeoutRuntimeProbe.java';old_probe=subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT.parent).decode();new_probe=(ROOT.parent/path).read_text('utf-8')
        for signature in ['private void guardOwner(','private boolean failedState(','private void failedGuard(',
                          'protected boolean additionalPhase(','protected void awaitHostedCycleTouch(',
                          'static boolean networkAlert(String text)','static boolean networkAlertNode(AccessibilityNodeInfo node,ReleaseSyncTimeoutAlerts.Tally alerts)']:
            self.assertEqual(method(old_probe,signature),method(new_probe,signature))
        failure=method(new_probe,'private void failureObservation(')
        for token in ('getText(','getContentDescription(','nodes()','SystemClock','getUiAutomation'):self.assertNotIn(token,failure)
        a=failure.index('OriginalSyncTimeoutWaitFacts wait=');b=failure.index('write("owned-sync-timeout-',a)
        restored=failure[:a]+failure[b:];restored=restored.replace('.put("schema",4)','.put("schema",3)').replace('.put("wait_guard_failure",waitFacts==null?JSONObject.NULL:waitFacts)','')
        self.assertEqual(restored.split(),method(old_probe,'private void failureObservation(').split())
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin';suffix='.exe' if os.name=='nt' else ''
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        sources=sorted((ROOT/'android-runtime').glob('*.java'));inventory={f.relative_to(ROOT.parent).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sources};self.assertEqual(len(inventory),50)
        commands=[];folder=None;passed=False
        def run(args):
            r=subprocess.run(args,capture_output=True,timeout=60);commands.append(dict(argv=args,returncode=r.returncode,stdout_base64=base64.b64encode(r.stdout).decode(),stderr_base64=base64.b64encode(r.stderr).decode()));self.assertEqual(r.returncode,0,r.stderr.decode('utf-8','replace'));return r
        try:
            with tempfile.TemporaryDirectory(prefix='onx-timeout-wait-integration-') as temp:
                folder=Path(temp).resolve();actual=folder/'actual';actual.mkdir();harness=folder/'harness';harness.mkdir();scratch=folder/'java-tmp';scratch.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                checks=['OriginalSyncTimeoutActionsCheck','OriginalSyncTimeoutWaitIntegrationCheck']
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),*[str(ROOT/'android-runtime-tests'/(n+'.java')) for n in checks]])
                cp=os.pathsep.join(map(str,(harness,actual,sdk)))
                for name in checks:
                    r=run([str(java),'-Xmx64m','-Djava.io.tmpdir='+str(scratch),'-cp',cp,'cc.kronecker.opennexus.testdriver.'+name]);self.assertIn(b'actual_Android',r.stdout)
                self.assertEqual(list(scratch.iterdir()),[])
                self.assertEqual(inventory,{n:hashlib.sha256((ROOT.parent/n).read_bytes()).hexdigest() for n in inventory});passed=True
        finally:
            print('TIMEOUT_WAIT_INTEGRATION_PROOF '+json.dumps(dict(passed=passed,API36_inputs=inventory,API36_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),commands=commands,task_root=None if folder is None else str(folder),task_root_remaining=folder is not None and folder.exists(),extra_clock_UI_or_device_reads=0,original_static_wait_and_unwrapped_observe_loop_source_equal=True,actual_Android_Native_timeout_or_recovery_verified=False)))


if __name__=='__main__':unittest.main()
