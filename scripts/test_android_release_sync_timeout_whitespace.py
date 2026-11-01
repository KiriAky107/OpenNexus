"""Same-read optional whitespace diagnostics; no actual Android or Native claims."""
import ast
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import android_sync_timeout_alerts as alerts
import android_sync_timeout_failure as failure
from test_android_sync_timeout_alerts import diagnostics
from test_android_sync_timeout_failure import observation
from test_android_sync_timeout_host import parent,ready,Harness
from test_android_release_auth_scene import STUBS
from test_android_release_sync_timeout_alert_diagnostics import method

ROOT=Path(__file__).resolve().parent
BASE='b3ed95d3d4cd0b8751b59838a2ef291de6d9fc1b'
PRODUCT='d118b2ba63c922142f9fadee8772b6ec15791f48'


class WhitespaceDiagnostics(unittest.TestCase):
    def test_nonexact_channels_remain_diagnostic_not_NETWORK_or_failed_scene(self):
        value=observation();diag=diagnostics();value['alert_diagnostics']=diag
        for key in ('whitespace_nonexact_text_counts','whitespace_nonexact_description_counts'):diag[key]=[2]*50
        self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        self.assertEqual(diag['strict_counts'][2],0);self.assertEqual(value['label_counts'][3],0)
        self.assertFalse(value['native_timeout_verified']);self.assertFalse(diag['complete_alert_inferred'])
        value['alert_diagnostics']=None;self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        with self.assertRaises(RuntimeError):
            failure.validate_observation(dict(value,last_scene_scan_finished=False,label_counts=None,observed_elapsed_ms=None,alert_diagnostics=diag),parent(),ready())

    def test_rule_shape_counts_clock_scope_and_old_schema_refuse_partial_or_private(self):
        changes=[('schema',2),('whitespace_rule_id','arbitrary'),('whitespace_rule_id',True),
                 ('whitespace_nonexact_text_counts',[1]*49),('whitespace_nonexact_text_counts',[False]*50),
                 ('whitespace_nonexact_description_counts',[-1]*50),('whitespace_nonexact_description_counts',[3]*50),
                 ('whitespace_complete_candidates_only',False),('whitespace_is_not_admission',False),('scan_elapsed_ms',32001)]
        for key,new in changes:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                failure.validate_observation(dict(observation(),alert_diagnostics=dict(diagnostics(),**{key:new})),parent(),ready())
        for name in ('whitespace_rule_id','whitespace_nonexact_text_counts','whitespace_nonexact_description_counts'):
            diag=diagnostics();del diag[name]
            with self.assertRaises(RuntimeError):failure.validate_observation(dict(observation(),alert_diagnostics=diag),parent(),ready())
        for bad in [dict(diagnostics(),private='private UI'),{}]:
            with self.assertRaises(RuntimeError):failure.validate_observation(dict(observation(),alert_diagnostics=bad),parent(),ready())
        for change in [dict(schema=4),dict(window=ready()['window']+1),dict(parent_intent_id=parent()['input_intent_id'])]:
            with self.assertRaises(RuntimeError):failure.validate_observation(dict(observation(),alert_diagnostics=diagnostics(),**change),parent(),ready())

    def test_maximum_payload_stays_below_same_4096B_and_invalid_originals_read_once(self):
        r=ready();r.update(uid=99999,window=65535,elapsed_ms=2**63-1-36000)
        value=observation();value.update(uid=r['uid'],window=r['window'],original_ready_elapsed_ms=r['elapsed_ms'],observed_elapsed_ms=2**63-1,label_counts=[2]*8)
        diag=diagnostics();diag['scan_elapsed_ms']=value['observed_elapsed_ms'];value['alert_diagnostics']=diag
        for key in ('text_counts','description_counts','strict_counts','fragment_body_text_counts','fragment_body_description_counts',
                    'whitespace_nonexact_text_counts','whitespace_nonexact_description_counts'):diag[key]=[2]*50
        diag.update(fragment_header_counts=[2,2],fragment_detail_counts=[2,2],known_channel_conflicts=2)
        value['wait_guard_failure']=dict(schema=1,point='SCENE_FALSE_AT_DEADLINE',ready_elapsed_ms=r['elapsed_ms'],
            previous_elapsed_ms=2**63-2,sampled_elapsed_ms=2**63-1,deadline_elapsed_ms=2**63-1,state_observed=False,
            original_guard_rejected=True,diagnostic_only=True,native_timeout_verified=False)
        raw=json.dumps(value).encode();self.assertLess(len(raw),4096)
        self.assertEqual(failure.validate_observation(value,parent(),r),value)
        wrong=deepcopy(value);wrong['alert_diagnostics']['private']='private original channel'
        inputs=[raw,json.dumps(wrong).encode(),b'{"schema":5',raw.replace(b'"whitespace_rule_id":',b'"whitespace_rule_id":"duplicate", "whitespace_rule_id":'),b'x'*4097]
        for index,data in enumerate(inputs):
            with self.subTest(index=index),tempfile.TemporaryDirectory(prefix='onx-whitespace-read-') as folder:
                shell=Mock(return_value=SimpleNamespace(returncode=0,stdout=data,stderr=b''));subject=SimpleNamespace(shell=shell,output=Path(folder))
                failure.retain_failure(subject,'original',parent(),r);shell.assert_called_once()
                self.assertEqual(shell.call_args.kwargs,dict(check=False,timeout=1))
                path=Path(folder)/'original-driver-failure-observation.json';before=path.read_bytes();saved=json.loads(before)
                self.assertEqual(saved['available'],index==0);self.assertNotIn(b'private original channel',before)
                with self.assertRaises(FileExistsError):failure.retain_failure(subject,'original',parent(),r)
                shell.assert_called_once();self.assertEqual(path.read_bytes(),before)
        print('WHITESPACE_MAXIMUM_ORIGINAL_RECORD_BYTES '+str(len(raw)))

    def test_host_freezes_false_then_one_same_scoped_original_read_no_action_retry(self):
        with tempfile.TemporaryDirectory(prefix='onx-whitespace-host-') as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=h.subject.shell;reads=[]
            value=observation();value['alert_diagnostics']=diagnostics();value['alert_diagnostics']['whitespace_nonexact_text_counts'][2]=1
            def shell(*args,**kwargs):
                if args[:2]==('run-as',failure.DRIVER) and args[-1].endswith('-failure-observation.json'):
                    self.assertTrue((h.subject.output/'original-host-failure.json').exists());reads.append(args)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(value).encode(),stderr=b'')
                return original(*args,**kwargs)
            h.subject.shell=shell
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(len(reads),1);self.assertEqual(h.events,['launch','tap','held','input-finish'])
            self.assertFalse(h.held.release_attempted);self.assertFalse(h.subject.phases)
            saved=json.loads((h.subject.output/'original-driver-failure-observation.json').read_bytes())
            self.assertTrue(saved['available']);self.assertEqual(saved['observation']['label_counts'][3],0)
            self.assertFalse(saved['native_timeout_verified'])

    def test_actual_API36_same_read_source_and_JVM_integration_with_exact_NETWORK_unchanged(self):
        old=subprocess.check_output(['git','show',BASE+':scripts/android-runtime/ReleaseSyncTimeoutRuntimeProbe.java'],cwd=ROOT.parent).decode()
        current=(ROOT/'android-runtime/ReleaseSyncTimeoutRuntimeProbe.java').read_text(encoding='utf-8')
        for signature in ('private boolean failedState(','private void failedGuard(','private void guardOwner(',
                          'protected boolean additionalPhase(','protected void awaitHostedCycleTouch(',
                          'static boolean networkAlert(String text)','static boolean networkAlertNode(AccessibilityNodeInfo node,ReleaseSyncTimeoutAlerts.Tally alerts)'):
            self.assertEqual(method(old,signature),method(current,signature))
        handler=method(current,'private void failureObservation(')
        for token in ('getText(','getContentDescription(','nodes()','SystemClock','getUiAutomation'):self.assertNotIn(token,handler)
        restored=handler.replace('JSONArray nonexactTexts=new JSONArray(),nonexactDescriptions=new JSONArray();','')
        restored=restored.replace('for(int n:lastAlerts[8])nonexactTexts.put(n);','').replace('for(int n:lastAlerts[9])nonexactDescriptions.put(n);','')
        restored=restored.replace('.put("schema",3)','.put("schema",2)').replace('.put("schema",5)','.put("schema",4)')
        for part in ('.put("whitespace_rule_id",ReleaseSyncTimeoutAlertWhitespace.RULE_ID)',
                     '.put("whitespace_nonexact_text_counts",nonexactTexts)','.put("whitespace_nonexact_description_counts",nonexactDescriptions)',
                     '.put("whitespace_complete_candidates_only",true)','.put("whitespace_is_not_admission",true)'):restored=restored.replace(part,'')
        self.assertEqual(restored.split(),method(old,'private void failureObservation(').split())
        old_failure=ast.parse(subprocess.check_output(['git','show',BASE+':scripts/android_sync_timeout_failure.py'],cwd=ROOT.parent).decode())
        new_failure=ast.parse((ROOT/'android_sync_timeout_failure.py').read_text(encoding='utf-8'))
        for before,after in zip(old_failure.body,new_failure.body):
            if isinstance(before,ast.FunctionDef) and before.name!='validate_observation':self.assertEqual(ast.dump(before),ast.dump(after))
        sources=sorted((ROOT/'android-runtime').glob('*.java'));inventory={f.relative_to(ROOT.parent).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sources}
        self.assertEqual(len(inventory),51)
        # The new kernel and the fixed complete-label catalogue stay bound to their original source proof.
        kernel=(ROOT/'android-runtime/ReleaseSyncTimeoutAlertWhitespace.java').read_bytes()
        self.assertEqual(hashlib.sha256(kernel).hexdigest(),'4de684c4920195111e6849cd43a39aac09677bb6a1154be467415534fcd12834')
        for path in ('frontend/mobile/src/syncCycleSession.ts','frontend/src/features/settings/SyncActivity.vue','frontend/src/features/settings/syncPresentation.ts'):
            self.assertEqual(subprocess.check_output(['git','show',PRODUCT+':'+path],cwd=ROOT.parent),subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT.parent))
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin';suffix='.exe' if os.name=='nt' else ''
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix);commands=[];folder=None;passed=False
        def run(argv):
            result=subprocess.run(argv,capture_output=True,timeout=60)
            commands.append(dict(argv=argv,returncode=result.returncode,stdout_base64=base64.b64encode(result.stdout).decode(),stderr_base64=base64.b64encode(result.stderr).decode()))
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'));return result
        try:
            with tempfile.TemporaryDirectory(prefix='onx-whitespace-integration-') as name:
                folder=Path(name);actual=folder/'actual';actual.mkdir();harness=folder/'harness';harness.mkdir()
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
                stubs=dict(STUBS);stubs['android/app/Instrumentation.java']=stubs['android/app/Instrumentation.java'].replace('public void sendStatus','public android.content.Context getTargetContext(){throw new AssertionError("No Android target");}\n public void sendStatus')
                stubs['android/view/accessibility/AccessibilityNodeInfo.java']='''package android.view.accessibility;
public final class AccessibilityNodeInfo {
 private final CharSequence text,description;private final boolean editable;
 public int textReads,descriptionReads,editableReads;
 public AccessibilityNodeInfo(String a,String b,boolean e){text=a;description=b;editable=e;}
 public CharSequence getText(){textReads++;return text;} public CharSequence getContentDescription(){descriptionReads++;return description;}
 public boolean isEditable(){editableReads++;return editable;}
}'''
                paths=[]
                for path,text in stubs.items():
                    target=folder/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8');paths.append(str(target))
                checks=['ReleaseSyncTimeoutAlertCheck','ReleaseSyncTimeoutAlertWhitespaceIntegrationCheck']
                run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),*paths,
                     *[str(ROOT/'android-runtime-tests'/(check+'.java')) for check in checks]])
                cp=os.pathsep.join(map(str,(harness,actual,sdk)))
                original=run([str(java),'-Xmx64m','-cp',cp,'cc.kronecker.opennexus.testdriver.'+checks[0],
                              'Last recorded sync error · Cannot reach the service. Check the network and service address. (SYNC_NETWORK_ERROR)'])
                self.assertIn(b'node_channel_checks=20',original.stdout)
                checked=run([str(java),'-Xmx64m','-cp',cp,'cc.kronecker.opennexus.testdriver.'+checks[1]])
                self.assertIn(b'SAME_READ_PUBLIC_ALERT_WHITESPACE_OK',checked.stdout)
                self.assertEqual(inventory,{n:hashlib.sha256((ROOT.parent/n).read_bytes()).hexdigest() for n in inventory});passed=True
        finally:
            print('WHITESPACE_INTEGRATION_PROOF '+json.dumps(dict(passed=passed,API36_inputs=inventory,API36_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),
                commands=commands,task_root=str(folder),task_root_remaining=folder is not None and folder.exists(),extra_UI_device_config_or_clock_reads=0,
                same_original_complete_scan_cache_and_timestamp=True,actual_Android_UI_or_Native_verified=False)))


if __name__=='__main__':unittest.main()
