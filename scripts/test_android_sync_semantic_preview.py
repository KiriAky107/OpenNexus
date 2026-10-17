"""Explicit native accessibility scope stays separate from failed physical input."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import android_sync_runtime as runtime


class NativePreview(unittest.TestCase):
    def test_only_explicit_original_association_gets_one_accessibility_input_argument(self):
        for phase, enabled, expected in [('associate', True, True), ('associate', False, False), ('cycle', True, False)]:
            with self.subTest(phase=phase, enabled=enabled), tempfile.TemporaryDirectory() as work:
                probe = runtime.SyncProbe.__new__(runtime.SyncProbe)
                probe.output = Path(work); probe.phases=[]; probe.args=SimpleNamespace(semantic_preview=enabled)
                probe.start_app=Mock(); probe.association_observer=Mock()
                probe.shell=Mock(return_value=SimpleNamespace(returncode=0,stdout=('OPENNEXUS_SYNC_PHASE_OK '+phase).encode()))
                probe.sync_phase(phase)
                probe.shell.assert_called_once(); command=probe.shell.call_args.args
                self.assertEqual(command.count('association_preview'), int(expected))
                self.assertEqual(command.count('native-accessibility'), int(expected))
                self.assertIn('/.SyncRuntimeProbe', command[-1])
                intent=json.loads((Path(work)/'sync-dispatch-1.json').read_text('utf-8'))
                self.assertEqual(intent['association_preview_input'], ('native-accessibility' if expected else 'touch') if phase=='associate' else None)
                self.assertFalse(intent['automatic_retry']); self.assertFalse(intent['server_or_native_operation_id'])

    def test_accessibility_flag_cannot_launch_a_different_scope_or_local_host(self):
        args=['owned-probe','--bundle','unused','--sdk','unused','--output','unused','--source-checkout','unused',
              '--commit','a'*40,'--tools-commit','b'*40,'--build-run-id','39044670347','--semantic-preview']
        with patch.object(sys,'argv',args), patch.object(runtime,'require_hosted') as hosted, patch.object(runtime,'owned_root') as root:
            with self.assertRaisesRegex(RuntimeError,'Native accessibility preview requires the explicit conflict scope'):
                runtime.main()
            hosted.assert_not_called(); root.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
