"""Continuous-driver stream boundaries with fake handles; no ADB/app execution."""
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from android_desktop_handoff import HandoffError
from android_runtime_probe import APP, DRIVER
import android_original_outcome_instrumentation as stream
from android_original_outcome_status import STAGES
from test_android_original_outcome_status import NONCE, ready, stage, END
import test_android_original_outcome_process as process_fixture


class OriginalStream(unittest.TestCase):
    def setUp(self):
        self.owner = process_fixture.OriginalStop('test_one_owned_stop_and_original_observation_no_replay_on_second_call')
        self.owner.setUp(); self.addCleanup(self.owner.doCleanups)
        self.probe, self.context, self.manifest = self.owner.probe, self.owner.context, self.owner.manifest
        self.probe.adb = Path('/owned/sdk/platform-tools/adb')
        self.driver_pid = 654
        self.reads = []
        def shell(*args, **kwargs):
            if args == ('pidof', APP): body = b'321\n'
            elif args == ('pidof', DRIVER): body = str(self.driver_pid).encode() + b'\n'
            elif args[:3] == ('run-as', DRIVER, 'cat'):
                self.reads.append(args[-1]); body = json.dumps({'nonce': NONCE, 'fingerprint': 'd'*64}).encode()
            else: raise AssertionError('Unexpected synthetic command')
            return SimpleNamespace(returncode=0, stdout=body, stderr=b'')
        self.probe.shell = Mock(side_effect=shell)
        for name, value in [('hosted_scope', None), ('observe_device', self.manifest['device']),
                            ('observe_packages', self.manifest['packages'])]:
            item = patch.object(stream, name, return_value=value)
            setattr(self, name, item.start()); self.addCleanup(item.stop)
        self.factory = Mock()

    def start(self, body=None, stderr=b''):
        if body is None: body = b''.join(ready()+stage(value)+END for value in STAGES)
        self.handle = SimpleNamespace(stdout=io.BytesIO(body), stderr=io.BytesIO(stderr), returncode=1,
                                      wait=Mock(return_value=1), terminate=Mock(), kill=Mock())
        self.factory.return_value = self.handle
        return stream.OriginalOutcomeInstrumentation(self.probe, self.context, NONCE, 321,
            self.manifest['device'], self.manifest['packages'], factory=self.factory)

    def proof(self):
        return {'nonce': NONCE, 'original_app_pid': 321, 'original_driver_pid': 654,
                'cold_original_entry_permitted': True, 'original_app_absence_observed': True,
                'original_driver_absence_observed': True, 'same_original_device_packages_UIDs': True,
                'owned_emulator_still_connected': True}

    def test_one_continuous_invocation_reads_only_complete_ready_stages_then_joins_after_driver_exit(self):
        value = self.start()
        with self.assertRaisesRegex(HandoffError, 'READ_BEFORE_READY'): value.read_ready('old-frozen')
        for item in STAGES:
            value.next_ready(item); self.assertEqual(value.read_ready(item)['nonce'], NONCE)
            with self.assertRaises(HandoffError): value.read_ready(item)
        observed = value.finish_after_driver_exit(self.proof())
        self.assertEqual(observed['instrumentation_invocations'], 1)
        self.assertFalse(observed['ordinary_UI_phase_success_claimed'])
        self.assertEqual(len(self.reads), 5); self.factory.assert_called_once()
        self.handle.terminate.assert_not_called(); self.handle.kill.assert_not_called()
        self.assertEqual(self.factory.call_args.args[0][-1], DRIVER+'/.SyncRuntimeProbe')

    def test_partial_frame_then_eof_cannot_authorize_file_read_or_a_second_invocation(self):
        value = self.start(ready()+stage('old-frozen'))
        with self.assertRaisesRegex(HandoffError, 'READY_UNCONFIRMED'): value.next_ready('old-frozen')
        with self.assertRaises(HandoffError): value.read_ready('old-frozen')
        self.assertEqual(self.reads, [])
        with self.assertRaises(FileExistsError): self.start()
        self.factory.assert_called_once()

    def test_foreign_host_refused_before_file_device_or_process_IO(self):
        self.hosted_scope.side_effect = HandoffError('TEST_FOREIGN_HOST')
        with self.assertRaisesRegex(HandoffError, 'TEST_FOREIGN_HOST'): self.start()
        self.factory.assert_not_called(); self.probe.shell.assert_not_called()
        self.assertFalse((self.probe.output/'continuous-original-instrumentation-intent.json').exists())

    def test_driver_PID_replacement_between_ready_stages_cannot_be_adopted(self):
        value = self.start(); value.next_ready('old-frozen'); value.read_ready('old-frozen')
        self.driver_pid = 655
        with self.assertRaisesRegex(HandoffError, 'DRIVER_PID_CHANGED'): value.next_ready('stale-confirm-intent')
        with self.assertRaises(HandoffError): value.read_ready('stale-confirm-intent')
        self.assertEqual(len(self.reads), 1)

    def test_no_host_handle_termination_or_cold_entry_without_both_original_UID_exits(self):
        value = self.start()
        for item in STAGES: value.next_ready(item); value.read_ready(item)
        for key in ('original_driver_absence_observed', 'original_app_absence_observed',
                    'same_original_device_packages_UIDs', 'owned_emulator_still_connected'):
            with self.assertRaisesRegex(HandoffError, 'DRIVER_EXIT_UNPROVEN'):
                value.finish_after_driver_exit(dict(self.proof(), **{key: False}))
        self.handle.wait.assert_not_called(); self.handle.terminate.assert_not_called()
        self.handle.kill.assert_not_called()

    def test_bounded_original_stderr_is_discarded_not_exported_and_never_a_phase_success(self):
        value = self.start(stderr=b'PRIVATE_RAW_DRIVER_ERROR\n')
        for item in STAGES: value.next_ready(item); value.read_ready(item)
        observed = value.finish_after_driver_exit(self.proof())
        self.assertEqual(observed['stderr_bytes_discarded'], 25)
        self.assertNotIn('PRIVATE_RAW_DRIVER_ERROR', json.dumps(observed))
        self.assertFalse(observed['ordinary_UI_phase_success_claimed'])


if __name__ == '__main__': unittest.main()
