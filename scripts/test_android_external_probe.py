from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hashlib,json,subprocess,tempfile,unittest
import android_external_probe as subject
from android_external_contract import armed,DRIVER,MD


def state():
    return {p:dict(files=[dict(id=p,path=p,hash=hashlib.sha256(raw).hexdigest(),deleted=0)],
        drafts=[dict(id=p,path=p,content=raw.decode(),generation=1,saved_generation=1,edit_session='first')],
        saves=[dict(operation_id=p,state='committed')],outbox=[dict(operation_id=p)],operations=[dict(operation_id=p,fingerprint='original')])
        for p,raw in subject.originals().items()}


class ExternalObservationBoundaries(unittest.TestCase):
    def test_every_original_file_draft_and_receipt_is_retained(self):
        raw=subject.originals();base=state();changed=deepcopy(base)
        changed[subject.MD_PATH]['drafts'][0]['edit_session']='cold';subject.validate_frozen(raw,base,changed,raw)
        for key in ('files','drafts','saves','outbox','operations'):
            changed=deepcopy(base);changed[subject.CANVAS_PATH][key][0]['changed']='unreviewed'
            with self.subTest(key=key),self.assertRaises(RuntimeError):subject.validate_frozen(raw,base,changed,raw)
        with self.assertRaises(RuntimeError):subject.validate_frozen(raw,base,base,dict(raw,extra=b'other'))
        changed=deepcopy(base);changed[subject.CANVAS_PATH]['files'][0]['hash']='changed'
        with self.assertRaises(RuntimeError):subject.validate_frozen(raw,changed,changed,raw)

    def test_only_unconfirmed_markdown_draft_can_change_before_link_review(self):
        base=state();changed=deepcopy(base);changed[subject.MD_PATH]['drafts'][0].update(content=subject.DRAFT,generation=2)
        subject.validate_draft(base,changed)
        for mode in ('saved','canvas','body','confirmed'):
            bad=deepcopy(changed)
            if mode=='saved':bad[subject.MD_PATH]['operations'].append(dict(operation_id='unexpected'))
            if mode=='canvas':bad[subject.CANVAS_PATH]['drafts'][0]['content']='other'
            if mode=='body':bad[subject.MD_PATH]['drafts'][0]['content']='other'
            if mode=='confirmed':bad[subject.MD_PATH]['drafts'][0]['saved_generation']=2
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):subject.validate_draft(base,bad)

    def test_unreviewed_paths_scope_or_transfer_stop_before_io(self):
        probe=Mock();probe.args=SimpleNamespace(scope='other')
        for call in (lambda:subject.snapshot(probe,'external-baseline'),lambda:subject.transfer(probe,'owned-external-form.json',b'{}'),
            lambda:subject.read_record(probe,{}),lambda:subject.no_records(probe),lambda:subject.await_draft(probe)):
            with self.assertRaises(RuntimeError):call()
        probe.private.assert_not_called();probe.database_rows.assert_not_called();probe.shell.assert_not_called();probe.device.assert_not_called()
        probe.args.scope='external-links'
        with self.assertRaises(RuntimeError):subject.transfer(probe,'../../user-file',b'{}')
        with self.assertRaises(RuntimeError):subject.snapshot(probe,'unreviewed')
        probe.device.assert_not_called();probe.private.assert_not_called()

    def test_unknown_receiver_result_is_read_only_and_never_sends_open_again(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(scope='external-links')
            probe.shell.return_value=subprocess.CompletedProcess([],1,b'',b'not found')
            original=armed('markdown','d2f28693-fb8c-4299-b5b3-15d7723bb001')
            self.assertIsNone(subject.read_record(probe,original));probe.device.assert_not_called();probe.phase.assert_not_called()
            probe.shell.assert_called_once_with('run-as',DRIVER,'cat','files/owned-external-markdown.received.json',check=False)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_recovered_original_record_cannot_be_replaced_by_a_later_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(scope='external-links')
            original=armed('markdown','d2f28693-fb8c-4299-b5b3-15d7723bb001')
            value=dict(schema=1,scope='external-links',key='markdown',operation_id=original['operation_id'],original_uri=MD,
                actual_action='android.intent.action.VIEW',actual_browsable=True,actual_data_uri=MD,receiver_package=DRIVER,
                elapsed_realtime_ms=17,browser_or_network_or_physical_verified=False)
            raw=json.dumps(value).encode();probe.shell.return_value=subprocess.CompletedProcess([],0,raw,b'')
            self.assertEqual(subject.read_record(probe,original),value);self.assertEqual(subject.read_record(probe,original),value)
            probe.shell.return_value=subprocess.CompletedProcess([],0,json.dumps(dict(value,elapsed_realtime_ms=18)).encode(),b'')
            with self.assertRaisesRegex(RuntimeError,'ORIGINAL_RECORD_CHANGED'):subject.read_record(probe,original)
            self.assertEqual((probe.output/'external-markdown-received.json').read_bytes(),raw);probe.device.assert_not_called()

    def test_prior_native_tests_are_not_counted_as_this_os_handoff(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'),patch.dict('os.environ',{'GITHUB_RUN_ID':'1'}):
            probe=Mock();probe.output=Path(folder);probe.root=None
            probe.args=SimpleNamespace(scope='external-links',serial='emulator-5562',tools_commit='tools',commit='compiled')
            probe.binding={'sha256':'fixed'};probe.keystore_test_result={'tests':5}
            with self.assertRaisesRegex(RuntimeError,'MUST_NOT_REPLAY_NATIVE_SUITES'):subject.run_external(probe)
            proof=json.loads((probe.output/'external-link-proof.json').read_bytes())
            self.assertFalse(proof['passed']);self.assertEqual(proof['executed_steps'],[])
            probe.enable_test_ime.assert_not_called();probe.phase.assert_not_called();probe.device.assert_not_called()

    def test_cancel_has_no_open_arming_or_new_save_and_native_phases_are_closed(self):
        text=(Path(__file__).parent/'android-runtime/RuntimeProbe.java').read_text('utf-8')
        from test_android_editor_readiness import method
        body=method(text,'private void originalExternalReference(String phase)')
        cancel=body.split('case "external-cancel":',1)[1].split('break;',1)[0]
        self.assertIn('referenceDialogAction("Close")',cancel);self.assertIn('externalAbsent()',cancel)
        for forbidden in ('referenceDialogAction("Open")','save()','inputText','setText','startActivity'):
            self.assertNotIn(forbidden,cancel)
        form=subject.driver_form();self.assertEqual(len(form),8);self.assertLess(len(json.dumps(form,ensure_ascii=False).encode()),8192)
        self.assertEqual(form['canvas_uri'],subject.CANVAS);self.assertTrue(form['canvas_uri'].startswith('HTTPS:'))

if __name__=='__main__':unittest.main()
