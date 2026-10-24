"""Actual command recorder checks for the separate recovery driver and expected GUI failure."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import android_sync_host_touch as runtime
from android_release_upgrade import ReleaseUpgrade
import test_android_sync_host_touch as original

MARKER = b'OPENNEXUS_SYNC_EXPECTED_UNKNOWN_UI failed_cycle_observed=true unconfirmed_outcome_observed=true cycle_button_disabled=true native_original_state_verified=false raw_UI_recorded=false\n'


class RecoveryProcess(original.Process):
    def __init__(self, phase, *, marker=True):
        super().__init__(); self.phase = phase; self.marker = marker
    def communicate(self, timeout):
        if self.returncode is None: self.returncode = 0
        return ((MARKER if self.phase == 'cycle-drop-original' and self.marker else b'') +
                ('OPENNEXUS_RELEASE_SYNC_PHASE_OK ' + self.phase + '\n').encode()), b''


class RecoveryHost(unittest.TestCase):
    def subject(self, folder, phase, *, unknown=False):
        value = original.ready(); value['phase'] = phase
        subject, _ = original.HostTouch().subject(folder, value)
        subject.driver_entry = 'ReleaseSyncRecoveryRuntimeProbe'
        subject.original_command = ReleaseUpgrade.original_command.__get__(subject)
        if unknown:
            def device(*args, **kwargs):
                if args[:2] == ('shell', '-T') and args[2].startswith('input touchscreen'):
                    raise runtime.subprocess.TimeoutExpired(args, 10, output=b'original-static', stderr=b'')
                return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            subject.device.side_effect = device
        return subject

    def execute(self, subject, phase, process):
        with patch.object(runtime, 'require_hosted_emulator'), patch.object(runtime.subprocess, 'Popen', return_value=process), \
                patch.object(runtime.ReleaseAuthBackground, 'app_is_resumed', return_value=True):
            runtime.run_hosted_recovery_cycle(subject, phase, 'recovery-original')

    def test_closed_driver_phase_pairs_refuse_cross_scope_before_launch(self):
        pairs = {'ReleaseSyncRuntimeProbe': ('cycle', 'cycle-incoming'),
                 'ReleaseSyncMutationRuntimeProbe': ('cycle-rename', 'cycle-delete'),
                 'ReleaseSyncRecoveryRuntimeProbe': ('cycle-drop-original', 'cycle-reconcile-original')}
        for entry in tuple(pairs) + ('other',):
            for phase in sum(pairs.values(), ()) + ('status', 'cycle-dropped-upload', '', None):
                self.assertEqual(runtime.admitted_phase(entry, phase), phase in pairs.get(entry, ()))
        for phase in pairs['ReleaseSyncRecoveryRuntimeProbe']:
            value = original.ready(); value['phase'] = phase
            with self.assertRaises(RuntimeError): runtime.validate_ready(value, phase, 1100)
            with tempfile.TemporaryDirectory() as folder:
                subject = self.subject(folder, phase); subject.driver_entry = 'ReleaseSyncRuntimeProbe'
                with self.assertRaises(RuntimeError): self.execute(subject, phase, RecoveryProcess(phase))
                subject.stop.assert_not_called(); subject.device.assert_not_called()

    def test_both_original_phases_real_recorder_keep_unique_command_owner_and_expected_outcome(self):
        for phase in ('cycle-drop-original', 'cycle-reconcile-original'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                subject = self.subject(folder, phase); self.execute(subject, phase, RecoveryProcess(phase))
                calls = [v for v in subject.device.call_args_list if v.args[:2] == ('shell', '-T') and v.args[2].startswith('input touchscreen')]
                self.assertEqual(len(calls), 1)
                phase_intent = json.loads((Path(folder) / 'recovery-original-host-touch-phase-intent.json').read_bytes())
                command = json.loads((Path(folder) / 'recovery-original-host-touch-intent.json').read_bytes())
                self.assertEqual(phase_intent['phase'], phase); self.assertEqual(command['arguments'], list(calls[0].args))
                self.assertTrue(json.loads((Path(folder) / 'recovery-original-host-touch-after-input-owner.json').read_bytes())['same_owned_boot_identity'])
                self.assertEqual(subject.phases[0]['expected_cycle_result'], 'unknown_original' if phase == 'cycle-drop-original' else 'completed')
                self.assertFalse(subject.phases[0]['native_original_state_verified'])
                before = subject.device.call_count
                with self.assertRaises(FileExistsError): self.execute(subject, phase, RecoveryProcess(phase))
                self.assertEqual(subject.device.call_count, before)

    def test_unknown_input_both_original_phases_preserves_result_no_finish_or_retry(self):
        for phase in ('cycle-drop-original', 'cycle-reconcile-original'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                subject = self.subject(folder, phase, unknown=True); process = RecoveryProcess(phase)
                with self.assertRaises(runtime.subprocess.TimeoutExpired): self.execute(subject, phase, process)
                result = json.loads((Path(folder) / 'recovery-original-host-touch-result.json').read_bytes())
                self.assertTrue(result['unknown_result']); self.assertFalse(result['automatic_retry'])
                self.assertFalse((Path(folder) / 'recovery-original-host-touch-finish-intent.json').exists())
                self.assertEqual(sum(v.args[2].startswith('input touchscreen') for v in subject.device.call_args_list), 1)
                process.terminate.assert_called_once(); self.assertFalse(subject.phases)

    def test_drop_phase_OK_without_actual_expected_unknown_GUI_marker_is_refused_without_second_input(self):
        with tempfile.TemporaryDirectory() as folder:
            subject = self.subject(folder, 'cycle-drop-original')
            with self.assertRaises(RuntimeError): self.execute(subject, 'cycle-drop-original', RecoveryProcess('cycle-drop-original', marker=False))
            self.assertFalse(subject.phases)
            self.assertEqual(sum(v.args[2].startswith('input touchscreen') for v in subject.device.call_args_list), 1)
            self.assertEqual(subject.stop.call_count, 2)


if __name__ == '__main__': unittest.main()
