"""Explicit original-cycle WebView input; no native result is manufactured."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_runtime as runtime
import android_sync_webview_input as original

NONCE='a326b9cd-165c-4444-b1c2-5b05b48f1994'


class OriginalCycleInput(unittest.TestCase):
    def test_only_an_explicit_product_pair_initial_cycle_uses_its_original_ready_stream(self):
        for scope,flag,expected in [('product-pair',True,True),('product-pair',False,False),('sync-initial',True,False)]:
            with self.subTest(scope=scope,flag=flag),tempfile.TemporaryDirectory() as folder:
                probe=runtime.SyncProbe.__new__(runtime.SyncProbe)
                probe.args=SimpleNamespace(scope=scope,webview_cycle=flag)
                probe.output=Path(folder);probe.phases=[];probe.start_app=Mock()
                result=subprocess.CompletedProcess([],0,b'OPENNEXUS_SYNC_PHASE_OK cycle')
                probe.shell=Mock(return_value=result)
                with patch.object(runtime,'instrument_original_cycle',return_value=result) as instrument:
                    probe.sync_phase('cycle')
                self.assertEqual(instrument.call_count,int(expected));self.assertEqual(probe.shell.call_count,int(not expected))
                intent=json.loads((probe.output/'sync-dispatch-1.json').read_text('utf-8'))
                self.assertEqual(intent['cycle_input'],'webview-touch' if expected else None)

    def test_only_explicit_remaining_cycles_use_the_new_backend_without_a_native_fallback(self):
        for phase,expected in [('cycle-conflict',True),('cycle-conflict-copy',True),('cycle-incoming',True),('cycle-media',True),('cycle',False),('status',False)]:
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as folder:
                probe=runtime.SyncProbe.__new__(runtime.SyncProbe);probe.args=SimpleNamespace(webview_cycle=True)
                probe.output=Path(folder);probe.phases=[];probe.start_app=Mock()
                result=subprocess.CompletedProcess([],0,('OPENNEXUS_SYNC_PHASE_OK '+phase).encode())
                probe.shell=Mock(return_value=result)
                with patch.object(runtime,'instrument_original_cycle',return_value=result) as instrument:
                    probe.sync_phase(phase)
                self.assertEqual(instrument.call_count,int(expected));self.assertEqual(probe.shell.call_count,int(not expected))
                intent=json.loads((probe.output/'sync-dispatch-1.json').read_text(encoding='utf-8'))
                self.assertEqual(intent['cycle_input'],'webview-touch' if expected else None)

    def test_unknown_original_cycle_completion_is_retained_and_never_relaunched(self):
        for phase,unknown in [('cycle-conflict',False),('cycle-conflict',True),('cycle',False),('cycle',True)]:
            with self.subTest(phase=phase,unknown=unknown),tempfile.TemporaryDirectory() as folder:
                probe=SimpleNamespace(output=Path(folder),adb=Path('/owned/adb'),args=SimpleNamespace(serial='emulator-5562',commit='a'*40,tools_commit='b'*40),
                    shell=Mock(return_value=SimpleNamespace(stdout=b'321')),wait_owned_connection=Mock())
                process=Mock();process.stdout=io.BytesIO(('INSTRUMENTATION_STATUS: owned_cycle_ready='+NONCE+'\nOPENNEXUS_SYNC_PHASE_OK '+phase+'\n').encode())
                process.returncode=None;process.poll.return_value=None
                def finish(timeout):process.returncode=0;return 0
                process.wait.side_effect=finish
                with patch.object(original,'require_hosted_emulator'),patch.object(original.subprocess,'Popen',return_value=process) as launch,\
                     patch.object(original,'touch_original_cycle',side_effect=RuntimeError('Private data must not escape') if unknown else None) as touch:
                    result=original.instrument_original_cycle(probe,NONCE,phase)
                    with self.assertRaises(FileExistsError):original.instrument_original_cycle(probe,NONCE,phase)
                    launch.assert_called_once();touch.assert_called_once()
                    self.assertEqual(result.returncode,int(unknown))
                    record=probe.output/('original-webview-'+phase)/'original-webview-cycle-stream.json'
                    value=json.loads(record.read_text(encoding='utf-8'))
                    self.assertEqual(value['readiness_signals'],1);self.assertEqual(value['failed'],unknown)
                    self.assertNotIn('Private data',record.read_text(encoding='utf-8'))

    def test_cycle_flag_outside_conflict_scope_refuses_before_host_or_owner_access(self):
        args=['probe','--bundle','unused','--sdk','unused','--output','unused','--source-checkout','unused',
              '--commit','a'*40,'--tools-commit','b'*40,'--build-run-id','39054475515','--webview-cycle']
        with patch.object(sys,'argv',args),patch.object(runtime,'require_hosted') as host:
            with self.assertRaises(RuntimeError):runtime.main()
            host.assert_not_called()


if __name__=='__main__':unittest.main()
