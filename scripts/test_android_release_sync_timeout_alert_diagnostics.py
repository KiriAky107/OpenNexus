"""One affected API36 compile, pure JVM channel checks and fixed-product source contract."""
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
import android_sync_timeout_alerts as alerts

ROOT=Path(__file__).resolve().parent
PRODUCT='d118b2ba63c922142f9fadee8772b6ec15791f48'
PREVIOUS='f036303aa16c3cb08a180b06948b6358a8e39e53'
TEMPORARIES=[]


def method(source,signature):
    start=source.index('{',source.index(signature));level=1;end=start+1
    while level:
        if source[end]=='{':level+=1
        if source[end]=='}':level-=1
        end+=1
    return source[start:end]


class TimeoutAlertDiagnostics(unittest.TestCase):
    def test_actual_API36_compile_source_contract_and_pure_JVM_channels_without_extra_UI_reads(self):
        files={}
        for name in ['frontend/mobile/src/syncCycleSession.ts','frontend/src/features/settings/SyncActivity.vue',
                     'frontend/src/features/settings/syncPresentation.ts']:
            raw=subprocess.check_output(['git','show',PRODUCT+':'+name],cwd=ROOT.parent)
            files[name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),text=raw.decode())
        session=files['frontend/mobile/src/syncCycleSession.ts']['text']
        presentation=files['frontend/src/features/settings/syncPresentation.ts']['text']
        activity=files['frontend/src/features/settings/SyncActivity.vue']['text']
        codeblock=re.search(r'const safeCodes = new Set\(\[(.*?)\]\)',session,re.S)[1]
        codes=list(dict.fromkeys(re.findall(r"'([A-Z0-9_]+)'",codeblock)))
        self.assertIn("? value : 'SYNC_CONNECTION_FAILED'",session);self.assertEqual(codes,list(alerts.CODES))
        prefix=re.search(r"t\('[^']*', '([^']+)'\) \}\} · \{\{ syncError\(error\) \}\}",activity)[1]
        messages={}
        for cases,english in re.findall(r"((?:case '[A-Z0-9_]+'\s*:\s*)+)message = t\('[^']*', '([^']+)'\)",presentation):
            for code in re.findall(r"case '([A-Z0-9_]+)'",cases):messages[code]=english
        fallback=re.search(r"default: message = t\('[^']*', '([^']+)'\)",presentation)[1]
        self.assertIn('return `${message} (${code})`',presentation)
        labels=[prefix+' · '+messages.get(code,fallback)+' ('+code+')' for code in codes]
        digest=hashlib.sha256(json.dumps(list(zip(codes,labels)),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(digest,alerts.CATALOG_SHA256)
        source_path='scripts/android-runtime/ReleaseSyncTimeoutRuntimeProbe.java'
        old=subprocess.check_output(['git','show',PREVIOUS+':'+source_path],cwd=ROOT.parent).decode()
        new=(ROOT.parent/source_path).read_text()
        for signature in ['private void guardOwner(', 'private void failedGuard(', 'protected boolean additionalPhase(',
                          'protected void awaitHostedCycleTouch(', 'static boolean networkAlert(String text)']:
            self.assertEqual(method(old,signature),method(new,signature))
        previous=method(old,'static boolean networkAlertNode(AccessibilityNodeInfo node)')
        current=method(new,'static boolean networkAlertNode(AccessibilityNodeInfo node,ReleaseSyncTimeoutAlerts.Tally alerts)')
        self.assertEqual(re.sub(r'\s','',previous),re.sub(r'\s','',current.replace('if(alerts!=null)alerts.record(value,description);','')))
        original_scan=method(old,'private boolean failedState(')
        current_scan=method(new,'private boolean failedState(')
        restored=current_scan.replace('ReleaseSyncTimeoutAlerts.Tally alerts=new ReleaseSyncTimeoutAlerts.Tally();','').replace('networkAlertNode(node,alerts)','networkAlertNode(node)')
        restored=restored.replace('long sampled=SystemClock.elapsedRealtime();','').replace(',sampled);',',SystemClock.elapsedRealtime());').replace('lastAlerts=alerts.snapshot();lastAlertAt=sampled;','')
        self.assertEqual(re.sub(r'\s','',original_scan),re.sub(r'\s','',restored))
        failure=method(new,'private void failureObservation(')
        self.assertNotIn('getText(',failure);self.assertNotIn('getContentDescription(',failure);self.assertNotIn('nodes()',failure)
        self.assertIn('lastAlertAt==actions.lastSceneAt()',failure)
        suffix='.exe' if os.name=='nt' else '';jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={f.relative_to(ROOT.parent).as_posix():dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources}
        commands=[];compile_passed=False;JVM_passed=False
        def run(argv):
            value=subprocess.run(argv,capture_output=True,timeout=60,check=False)
            commands.append(dict(argv=argv,returncode=value.returncode,stdout_base64=base64.b64encode(value.stdout).decode(),stderr_base64=base64.b64encode(value.stderr).decode()))
            self.assertEqual(value.returncode,0,value.stderr.decode('utf-8','replace'));return value
        try:
            with tempfile.TemporaryDirectory(prefix='onx-timeout-alert-java-') as folder:
                TEMPORARIES.append(folder);root=Path(folder).resolve();actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                compile_passed=True
                stubs=dict(STUBS);stubs['android/app/Instrumentation.java']=stubs['android/app/Instrumentation.java'].replace('public void sendStatus',
                    'public android.content.Context getTargetContext(){throw new AssertionError("No device target in diagnostic fixture");}\n public void sendStatus')
                stubs['android/view/accessibility/AccessibilityNodeInfo.java']='''package android.view.accessibility;
public final class AccessibilityNodeInfo {
 private final CharSequence text,description;private final boolean editable;
 public int textReads,descriptionReads,editableReads;
 public AccessibilityNodeInfo(String text,String description,boolean editable){this.text=text;this.description=description;this.editable=editable;}
 public CharSequence getText(){textReads++;return text;} public CharSequence getContentDescription(){descriptionReads++;return description;}
 public boolean isEditable(){editableReads++;return editable;}
}'''
                paths=[]
                for name,value in stubs.items():
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8');paths.append(str(path))
                checks=['ReleaseSyncTimeoutAlertCheck','ReleaseSyncTimeoutAlertsCheck']
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                     *paths,*[str(ROOT/'android-runtime-tests'/ (name+'.java')) for name in checks]])
                cp=os.pathsep.join(map(str,(harness,actual,sdk)))
                original=run([str(java),'-Xmx64m','-cp',cp,'cc.kronecker.opennexus.testdriver.ReleaseSyncTimeoutAlertCheck',labels[alerts.NETWORK_INDEX]])
                self.assertIn(b'node_channel_checks=20',original.stdout)
                result=run([str(java),'-Xmx64m','-cp',cp,'cc.kronecker.opennexus.testdriver.ReleaseSyncTimeoutAlertsCheck',digest,*labels])
                self.assertIn(b'RELEASE_SYNC_TIMEOUT_ALERT_DIAGNOSTICS_OK',result.stdout);JVM_passed=True
                for name,meta in inventory.items():self.assertEqual(hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest(),meta['sha256'])
        finally:
            print('RELEASE_SYNC_TIMEOUT_ALERT_DIAGNOSTIC_PROOF '+json.dumps(dict(product_source=PRODUCT,
                fixed_source_files={k:{n:v for n,v in item.items() if n!='text'} for k,item in files.items()},
                catalog_sha256=digest,catalog_kinds=len(codes),actual_API36_driver_inputs=inventory,SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),
                commands=commands,actual_API36_compile_passed=compile_passed,synthetic_channel_JVM_passed=JVM_passed,
                original_raw_network_predicate_and_owner_state_timing_source_equal=True,extra_UI_reads=0,
                compiler_max_heap_MiB=128,JVM_max_heap_MiB=64,temporary_roots_remaining=[s for s in TEMPORARIES if Path(s).exists()],
                actual_Android_UI_timeout_or_Native_cause_verified=False)))


if __name__=='__main__':unittest.main()
