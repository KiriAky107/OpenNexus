"""The new browser-input backend cannot silently become a retry or native IPC."""
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import android_sync_runtime as runtime
import android_sync_webview_input as original

NONCE = 'a326b9cd-165c-4444-b1c2-5b05b48f1994'


class WebViewPreview(unittest.TestCase):
    def test_only_explicit_association_uses_original_stream_and_requires_one_real_UI_click(self):
        for events in ([{'name':'Preview merge','kind':'click','trusted':True}], [],
                       [{'name':'Preview merge','kind':'click','trusted':False}]):
            with self.subTest(events=events), tempfile.TemporaryDirectory() as work:
                probe = runtime.SyncProbe.__new__(runtime.SyncProbe); probe.output = Path(work); probe.phases = []
                probe.args = SimpleNamespace(webview_preview=True,semantic_preview=False)
                probe.start_app = Mock(); probe.shell = Mock()
                probe.association_observer = Mock(side_effect=[{}, {'observation':{'events':events}}])
                with patch.object(runtime,'instrument_original_preview',return_value=subprocess.CompletedProcess([],0,b'OPENNEXUS_SYNC_PHASE_OK associate')) as instrument:
                    if events and events[0]['trusted']: probe.sync_phase('associate')
                    else:
                        with self.assertRaises(RuntimeError): probe.sync_phase('associate')
                    instrument.assert_called_once(); probe.shell.assert_not_called()
                    intent = json.loads((probe.output/'sync-dispatch-1.json').read_text('utf-8'))
                    self.assertEqual(instrument.call_args.args,(probe,intent['driver_dispatch_id']))
                    self.assertEqual(intent['association_preview_input'],'webview-touch')
                    self.assertFalse(intent['automatic_retry'])

    def test_browser_flag_refuses_other_scopes_and_mixed_input_before_host_or_owner_access(self):
        base = ['probe','--bundle','unused','--sdk','unused','--output','unused','--source-checkout','unused',
                '--commit','a'*40,'--tools-commit','b'*40,'--build-run-id','39044670347']
        for flags in (['--webview-preview'],['--conflicts','--webview-preview','--semantic-preview']):
            with self.subTest(flags=flags), patch.object(sys,'argv',base+flags), patch.object(runtime,'require_hosted') as host:
                with self.assertRaises(RuntimeError): runtime.main()
                host.assert_not_called()

    def test_revocation_admits_explicit_preview_only_without_acquiring_an_owner(self):
        base = ['probe','--bundle','unused','--sdk','unused','--output','unused','--source-checkout','unused',
                '--commit','a'*40,'--tools-commit','b'*40,'--build-run-id','39112675356','--revocation']
        class HostedBoundary(Exception): pass
        for flags in (['--webview-preview'], ['--webview-preview','--webview-cycle'],
                      ['--semantic-preview'], ['--webview-preview','--semantic-preview']):
            admitted = flags == ['--webview-preview']
            with self.subTest(flags=flags), patch.object(sys,'argv',base+flags), \
                 patch.object(runtime,'require_hosted',side_effect=HostedBoundary) as host, \
                 patch.object(runtime,'owned_root') as owner:
                with self.assertRaises(HostedBoundary if admitted else RuntimeError): runtime.main()
                self.assertEqual(host.call_count, int(admitted))
                owner.assert_not_called()

    def test_original_stream_accepts_one_nonce_and_never_relaunches_after_unknown_input(self):
        for mode in ('normal','wrong_nonce','duplicate','ended','unknown'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as work:
                probe = SimpleNamespace(output=Path(work),adb=Path('/owned/adb'),
                    args=SimpleNamespace(serial='emulator-5562',commit='a'*40,tools_commit='b'*40),
                    shell=Mock(return_value=SimpleNamespace(stdout=b'321')),wait_owned_connection=Mock())
                marker = ('INSTRUMENTATION_STATUS: owned_preview_ready='+('b'*36 if mode=='wrong_nonce' else NONCE)+'\n').encode()
                data = marker*(2 if mode=='duplicate' else 1)+b'OPENNEXUS_SYNC_PHASE_OK associate\n'
                process = Mock(); process.stdout=io.BytesIO(data); process.returncode=None
                process.poll.return_value=0 if mode=='ended' else None
                def wait(timeout): process.returncode=0; return 0
                process.wait.side_effect=wait
                with patch.object(original,'require_hosted_emulator'), patch.object(original.subprocess,'Popen',return_value=process) as launch, \
                     patch.object(original,'touch_original_preview',side_effect=RuntimeError('private text must not escape') if mode=='unknown' else None) as touch:
                    result=original.instrument_original_preview(probe,NONCE)
                    launch.assert_called_once()
                    self.assertEqual(launch.call_args.args[0].count('associate'),1)
                    self.assertEqual(launch.call_args.args[0].count('webview-touch'),1)
                    self.assertLessEqual(touch.call_count,1)
                    if mode in ('normal','unknown'): touch.assert_called_once_with(probe,NONCE,b'321')
                    if mode in ('wrong_nonce','ended'): touch.assert_not_called()
                    self.assertEqual(result.returncode,0 if mode=='normal' else 1)
                    self.assertNotIn('private text',(probe.output/'original-webview-preview-stream.json').read_text('utf-8'))
                    process.terminate.assert_not_called(); process.kill.assert_not_called()

    def test_changed_original_PID_refuses_before_any_forward_or_input_and_redacts_failure(self):
        with tempfile.TemporaryDirectory() as work:
            probe=SimpleNamespace(output=Path(work),args=SimpleNamespace(serial='emulator-5562'),
                shell=Mock(return_value=SimpleNamespace(stdout=b'999')),device=Mock(),command=Mock(),wait_owned_connection=Mock())
            with patch.object(original,'require_hosted_emulator'):
                with self.assertRaisesRegex(RuntimeError,'ORIGINAL_WEBVIEW_PREVIEW_INPUT_UNCONFIRMED'):
                    original.touch_original_preview(probe,NONCE,b'321')
            probe.device.assert_not_called(); probe.command.assert_not_called()
            value=json.loads((probe.output/'original-webview-preview-dispatch.json').read_text('utf-8'))
            self.assertTrue(value['failed']); self.assertFalse(value['raw_error_returned']); self.assertFalse(value['automatic_retry'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
