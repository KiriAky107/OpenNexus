"""Synthetic command handles; no local ADB or instrumentation is executed."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from android_desktop_handoff import HandoffError
from android_runtime_probe import APP, DRIVER
import android_original_outcome_driver_stop as driver
import android_original_outcome_process as original
import test_android_original_outcome_process as process_fixture


class DriverQuiescence(unittest.TestCase):
    def setUp(self):
        self.owner = process_fixture.OriginalStop('test_one_owned_stop_and_original_observation_no_replay_on_second_call')
        self.owner.setUp(); self.addCleanup(self.owner.doCleanups)
        self.probe, self.context, self.manifest = self.owner.probe, self.owner.context, self.owner.manifest
        observed = original.stop_original_once(self.probe, self.context, self.manifest, self.owner.authorization)
        self.native_exit = observed['original_exit_proof']
        self.bridge = {'event': 'original-exited', 'nonce': self.manifest['nonce'], 'original_pid': 321,
                       'original_callback_consumed': False, 'Debugger_resume_sent': False}
        self.pid, self.driver_stopped, self.unknown, self.foreign, self.stderr = 654, False, False, False, b''
        base = self.probe.shell.side_effect
        def shell(*args, **kwargs):
            if args == ('pidof', DRIVER):
                return SimpleNamespace(returncode=1 if self.driver_stopped else 0,
                    stdout=b'' if self.driver_stopped else (b'655\n' if self.foreign else b'654\n'), stderr=self.stderr)
            if args == ('am', 'force-stop', DRIVER):
                self.driver_stopped = True
                if self.unknown: raise TimeoutError('PRIVATE_DRIVER_ERROR')
                return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            return base(*args, **kwargs)
        self.probe.shell.side_effect = shell

    def writes(self):
        return [c for c in self.probe.shell.call_args_list if c.args == ('am', 'force-stop', DRIVER)]

    def stop(self, **changes):
        return driver.stop_original_driver_once(self.probe, self.context, self.manifest, self.pid,
            changes.get('native_exit', self.native_exit), changes.get('bridge_exit', self.bridge))

    def test_separate_captured_driver_stops_once_before_cold_entry_no_UI_success_claim(self):
        value = self.stop()
        self.assertTrue(value['cold_original_entry_permitted'])
        self.assertFalse(value['original_instrumentation_UI_success_claimed'])
        self.assertEqual(len(self.writes()), 1)
        with self.assertRaisesRegex(HandoffError, 'ALREADY_REQUESTED'): self.stop()
        self.assertEqual(len(self.writes()), 1)

    def test_native_unknown_or_consumed_reply_or_foreign_driver_never_sends_stop(self):
        for key, value in [('native_exit', dict(self.native_exit, force_stop_returncode=None)),
                           ('bridge_exit', dict(self.bridge, original_callback_consumed=True)),
                           ('bridge_exit', dict(self.bridge, nonce='00000000-0000-4000-8000-000000000099'))]:
            with self.assertRaises(HandoffError): self.stop(**{key: value})
        self.foreign = True
        with self.assertRaisesRegex(HandoffError, 'PID_CHANGED'): self.stop()
        self.assertEqual(len(self.writes()), 0)

    def test_unknown_stop_is_only_read_reconciled_and_cannot_grant_cold_entry(self):
        self.unknown = True; value = self.stop()
        self.assertTrue(value['original_driver_absence_observed'])
        self.assertFalse(value['cold_original_entry_permitted'])
        again = driver.reconcile_original_driver(self.probe, self.context, self.manifest, self.pid, 'second')
        self.assertEqual(again['command_outcome'], 'unknown')
        self.assertIsNone(again['force_stop_returncode']); self.assertEqual(len(self.writes()), 1)
        self.assertNotIn('PRIVATE_DRIVER_ERROR', ''.join(p.read_text('utf-8') for p in self.owner.output.glob('*.json')))

    def test_an_already_exited_driver_needs_no_input_and_ADB_error_is_not_absence(self):
        self.driver_stopped = True; value = self.stop()
        self.assertEqual(value['command_outcome'], 'already-absent')
        self.assertTrue(value['cold_original_entry_permitted']); self.assertEqual(len(self.writes()), 0)
        self.stderr = b'error: disconnected'
        again = driver.reconcile_original_driver(self.probe, self.context, self.manifest, self.pid, 'error')
        self.assertFalse(again['cold_original_entry_permitted'])

    def test_changed_UID_or_unconfirmed_app_exit_after_stop_blocks_cold_entry(self):
        self.stop()
        packages = deepcopy(self.manifest['packages']); packages[1]['uid'] += 1
        self.owner.observe_packages.return_value = packages
        with self.assertRaisesRegex(HandoffError, 'DEVICE_OR_PACKAGE_CHANGED'):
            driver.reconcile_original_driver(self.probe, self.context, self.manifest, self.pid, 'changed')
        self.assertEqual(len(self.writes()), 1)
        self.owner.observe_packages.return_value = self.manifest['packages']
        self.owner.stopped = False
        observed = driver.reconcile_original_driver(self.probe, self.context, self.manifest, self.pid, 'app-reappeared')
        self.assertFalse(observed['cold_original_entry_permitted'])


if __name__ == '__main__': unittest.main()
