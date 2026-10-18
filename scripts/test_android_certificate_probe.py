"""Pure untrusted-certificate boundaries; no TLS listener, process or device."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import ast,os,tempfile,unittest,uuid,yaml
import android_certificate_probe as subject
from android_workflow_gate import admitted

def transport():
    before=dict(counts=dict(connections=1,verified_handshakes=0,rejected_handshakes=1,http_bytes_observed=0),tls_reasons=['TLSV1_ALERT_UNKNOWN_CA'],raw_request_recorded=False)
    after=deepcopy(before);after['counts'].update(connections=2,rejected_handshakes=2);after['tls_reasons'].append('SSLV3_ALERT_BAD_CERTIFICATE')
    return before,after

def native():
    op='90bd27c8-0fe5-4c7f-a726-44486a352d91';account='owned-unit'
    return dict(intents=[dict(operation_id=op,kind='login',phase='login_pending',device_id=None,previous_operation=None,generation=1,endpoint=subject.ENDPOINT,account=account)],
        rejections=[],selection=dict(operation_id=op,generation=1,revision=1,endpoint=subject.ENDPOINT,account=account))

class CertificateBoundaries(unittest.TestCase):
    def test_runner_rejection_and_original_android_rejection_remain_distinct_without_reset_or_http_bytes(self):
        before,after=transport();subject.validate_rejected_handshake(before,after)
        self.assertEqual(before['counts']['connections'],1);self.assertEqual(after['counts']['connections'],2)

    def test_connection_error_trusted_handshake_extra_attempt_or_application_byte_cannot_be_a_certificate_pass(self):
        for change in ('reason','trusted','extra','http','raw'):
            before,after=transport()
            if change=='reason':after['tls_reasons'][-1]='PEER_IO_FAILURE'
            elif change=='trusted':after['counts']['verified_handshakes']=1
            elif change=='extra':after['counts']['connections']=3
            elif change=='http':after['counts']['http_bytes_observed']=1
            else:after['raw_request_recorded']=True
            with self.subTest(change=change),self.assertRaises(RuntimeError):subject.validate_rejected_handshake(before,after)

    def test_pending_original_login_requires_its_same_native_selection_and_never_an_active_session(self):
        value=native();op=subject.validate_certificate_intent(value,'owned-unit');self.assertEqual(op,value['intents'][0]['operation_id'])
        for change in ('active','unknown','device','account','selection','server_error','generation'):
            wrong=deepcopy(value)
            if change=='active':wrong['intents'][0]['phase']='active'
            elif change=='unknown':wrong['intents'][0]['phase']='unknown'
            elif change=='device':wrong['intents'][0]['device_id']='a'*32
            elif change=='account':wrong['intents'][0]['account']='other'
            elif change=='selection':wrong['selection']['operation_id']=str(uuid.uuid4())
            elif change=='server_error':wrong['rejections']=[dict(status=401,code='LOGIN_FAILED')]
            else:wrong['intents'][0]['generation']=2
            with self.subTest(change=change),self.assertRaises(RuntimeError):subject.validate_certificate_intent(wrong,'owned-unit')

    def test_cold_recovery_marks_only_the_original_pending_login_unknown_without_resending(self):
        before=native();cold=deepcopy(before);cold['intents'][0]['phase']='unknown'
        self.assertEqual(subject.validate_cold_certificate_intent(before,cold),before['intents'][0]['operation_id'])
        self.assertEqual(before['intents'][0]['phase'],'login_pending')

    def test_cold_active_replaced_selection_rejection_or_new_intent_cannot_be_a_pass(self):
        before=native();cold=deepcopy(before);cold['intents'][0]['phase']='unknown'
        for change in ('unchanged','active','operation','selection','generation','new','rejection'):
            wrong=deepcopy(cold)
            if change=='unchanged':wrong=deepcopy(before)
            elif change=='active':wrong['intents'][0]['phase']='active'
            elif change=='operation':wrong['intents'][0]['operation_id']=str(uuid.uuid4())
            elif change=='selection':wrong['selection']['revision']=2
            elif change=='generation':wrong['intents'][0]['generation']=2
            elif change=='new':wrong['intents'].append(deepcopy(wrong['intents'][0]))
            else:wrong['rejections']=[dict(status=401,code='LOGIN_FAILED')]
            with self.subTest(change=change),self.assertRaises(RuntimeError):subject.validate_cold_certificate_intent(before,wrong)

    def test_foreign_owner_or_existing_origin_refuses_before_listener_or_key_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch('android_auth_fixture.owned_root',return_value=root),patch.object(subject.socket,'socket') as network,patch.object(subject.subprocess,'run') as process:
                with self.assertRaises(RuntimeError):subject.UntrustedOrigin(root/'foreign')
                subject.UntrustedOrigin(root)
                with self.assertRaises(RuntimeError):subject.UntrustedOrigin(root)
                network.assert_not_called();process.assert_not_called()

    def test_scoped_workflow_keeps_trusted_auth_and_no_rebuild_or_other_clients(self):
        root=Path(__file__).resolve().parents[1];workflow=yaml.load((root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for reuse in ('','39112675356'):
            for name in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(workflow['jobs'][name]['if'],'auth-certificate',reuse=reuse,build='skipped'))
            self.assertEqual(admitted(workflow['jobs']['emulator-runtime']['if'],'auth-certificate',reuse=reuse,build='skipped'),bool(reuse))
        steps=workflow['jobs']['emulator-runtime']['steps']
        selected=next(s for s in steps if 'scripts/android_auth_runtime.py' in s.get('run',''))
        self.assertTrue(admitted(selected['if'],'auth-certificate'));self.assertIn("'auth-certificate' && 'certificate'",selected['env']['AUTH_PROBE_SCOPE'])
        sync=next(s for s in steps if 'scripts/android_sync_runtime.py' in s.get('run',''))
        self.assertFalse(admitted(sync['if'],'auth-certificate'))
        generic=next(s for s in steps if 'scripts/android_runtime_probe.py --commit' in s.get('run',''))
        self.assertFalse(admitted(generic['if'],'auth-certificate'))
        preflight=next(s for s in steps if s.get('name')=='Validate the immutable reuse selection')
        code=preflight['run'].split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        env=dict(REUSE_RUN='39112675356',REUSE_COMMIT='20af3d2cad9da2350f0e775f0e5605630f834443',RUNTIME_SCOPE='auth-certificate')
        with patch.dict(os.environ,env,clear=True):exec(compile(code,'certificate-workflow','exec'),{})
        with patch.dict(os.environ,dict(env,WEBVIEW_PREVIEW='true'),clear=True),self.assertRaises(AssertionError):exec(compile(code,'certificate-workflow','exec'),{})

    def test_new_driver_case_does_not_weaken_the_original_trusted_endpoint_or_disable_tls_validation(self):
        root=Path(__file__).resolve().parents[1]
        java=(root/'scripts/android-runtime/AuthRuntimeProbe.java').read_text('utf-8')
        trusted=java.split('private void signIn(int index)',1)[1].split('protected void signedIn()',1)[0]
        self.assertIn('trycloudflare',trusted);self.assertNotIn('10.0.2.2',trusted)
        special=java.split('private void signInUntrustedCertificate()',1)[1].split('protected void readCurrent()',1)[0]
        self.assertIn('owned-untrusted-certificate',special);self.assertEqual(special.count('click("Sign in")'),1)
        source=Path(subject.__file__).read_text('utf-8');ast.parse(source)
        for forbidden in ('CERT_NONE','check_hostname=False','load_verify_locations','verify=False','UPDATE sessions'):
            self.assertNotIn(forbidden,source)

    def test_certificate_cold_observes_the_completed_real_mount_read_without_an_extra_button(self):
        root=Path(__file__).resolve().parents[1]
        java=(root/'scripts/android-runtime/AuthRuntimeProbe.java').read_text('utf-8')
        phase=java.split('case "certificate-cold-unusable":',1)[1].split('case "invalid-certificate":',1)[0]
        self.assertIn('Original result is unconfirmed',phase);self.assertIn('No confirmed usable session',phase)
        self.assertNotIn('click(',phase);self.assertNotIn('readCurrent()',phase)
        self.assertIn('unusable()',phase)
        pane=(root/'frontend/mobile/src/MobileSyncConnection.vue').read_text('utf-8')
        mounted=pane.split('onMounted(async () => {',1)[1].split('onBeforeUnmount',1)[0]
        self.assertIn('await session.reload()',mounted)
        source=Path(subject.__file__).read_text('utf-8')
        self.assertIn("probe.auth_phase('certificate-cold-unusable',cold=True)",source)
        self.assertIn("cold_local=local_snapshot(probe,'certificate-cold')",source)

    def test_original_readonly_ui_failure_still_captures_cold_data_and_propagates(self):
        from unittest.mock import Mock
        function=next(v for v in ast.parse(Path(subject.__file__).read_text('utf-8')).body
            if isinstance(v,ast.FunctionDef) and v.name=='run_certificate')
        block=next(v for v in ast.walk(function) if isinstance(v,ast.Try) and any(
            isinstance(item,ast.Expr) and isinstance(item.value,ast.Call)
            and isinstance(item.value.func,ast.Attribute) and item.value.func.attr=='auth_phase'
            and item.value.args and isinstance(item.value.args[0],ast.Constant)
            and item.value.args[0].value=='certificate-cold-unusable' for item in v.body))
        probe=Mock();probe.auth_phase.side_effect=RuntimeError('synthetic readonly UI observation')
        probe.private.return_value.stdout=b'controlled original spool'
        local=Mock();atomic=Mock();server=Mock()
        with tempfile.TemporaryDirectory() as directory:
            probe.output=Path(directory);probe.root='./'
            with self.assertRaisesRegex(RuntimeError,'synthetic readonly UI observation'):
                exec(compile(ast.Module(body=[block],type_ignores=[]),'actual-cold-finally','exec'),
                    dict(probe=probe,local_snapshot=local,atomic_json=atomic,server=server,digest='a'*64))
            local.assert_called_once_with(probe,'certificate-cold')
            probe.original.assert_called_once_with('runtime-note.md')
            probe.observe.assert_called_once_with('certificate-cold')
            self.assertEqual((probe.output/'certificate-cold-original-spool.md').read_bytes(),b'controlled original spool')
            self.assertEqual(atomic.call_count,2)

if __name__=='__main__':unittest.main()
