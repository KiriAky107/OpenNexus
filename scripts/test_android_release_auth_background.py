"""Held-response lifecycle guards without starting an Android or network process."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from android_auth_fixture import DeliveryProxy, arm_control, initialize_control
from android_release_auth_background import PHASES, ReleaseAuthBackground, validate_background
import test_android_release_auth as auth_examples

HOLD = '11111111-2222-3333-4444-555555555555'
RELEASE = '66666666-7777-8888-9999-000000000000'


class ReleaseBackground(unittest.TestCase):
    def sequence(self):
        provider, native, cipher = auth_examples.ReleaseAuthBoundaries().sequence()
        before = provider[0]; held = deepcopy(before)
        before['users'][0]['username'] = held['users'][0]['username'] = 'owned-a'
        before['users'][1]['username'] = held['users'][1]['username'] = 'owned-b'
        before['requests']['events'] = [dict(action='login', service_status=200, delivery_status=200, fault_operation=None)]
        held['users'][1].update(devices=[dict(id='device-b')], session_counts=[dict(device_id='device-b', sessions=1)])
        held['requests'] = dict(counts={'login:200': 2}, events=before['requests']['events'] +
            [dict(action='login', service_status=200, delivery_status=None, fault_operation=HOLD)])
        pending = deepcopy(native[0]); pending['selection']['operation_id'] = 'original-held'
        pending['intents'].append(dict(native[0]['intents'][0], operation_id='original-held', account='owned-b', phase='login_pending', device_id=None))
        cold = deepcopy(pending); cold['intents'][1]['phase'] = 'unknown'
        hold = dict(operation_id=HOLD, mode='hold_login', expected_revision=0, result_revision=1)
        release = dict(operation_id=RELEASE, mode='release', expected_revision=1, result_revision=2)
        marker = dict(operation_id=HOLD, mode='hold_login', revision=1, action='login', service_status=200, service_response_complete=True, raw_body_recorded=False)
        delivered = dict(operation_id=HOLD, original_response_send_attempted=True, ASGI_send_returned=True, client_receipt_known=False)
        transition = dict(pid=1234, original_control_id=HOLD, same_original_app_process=True, actual_foreground_before_Home=True,
            actual_background_after_Home=True, same_UI_instrumentation_alive_until_after_original_release=True, private_auth_observed_only_after_stop=True)
        return [before, held, deepcopy(held), deepcopy(held)], [native[0], pending, cold], [deepcopy(cipher[0]) for _ in range(3)], hold, release, marker, delivered, transition

    def test_original_held_request_cold_ID_and_prior_cipher_not_replaced(self):
        original = self.sequence(); checks = validate_background(*original)
        self.assertEqual(checks['original_held_login_id'], 'original-held')
        for mode in ('reposted', 'new-device', 'new-selection', 'late-active', 'new-cipher', 'rewritten-A', 'driver-ended', 'client-assumed', 'wrong-release', 'not-background'):
            provider, native, cipher, hold, release, marker, delivered, transition = deepcopy(original)
            if mode == 'reposted': provider[3]['requests']['counts']['login:200'] = 3
            if mode == 'new-device': provider[2]['users'][1]['devices'].append(dict(id='extra'))
            if mode == 'new-selection': native[2]['selection']['operation_id'] = 'new-id'
            if mode == 'late-active': native[1]['intents'][1]['phase'] = 'active'
            if mode == 'new-cipher': cipher[1]['owned.bin']['sha256'] = 'f' * 64
            if mode == 'rewritten-A': native[1]['intents'][0]['phase'] = 'unknown'
            if mode == 'driver-ended': transition['same_UI_instrumentation_alive_until_after_original_release'] = False
            if mode == 'client-assumed': delivered['client_receipt_known'] = True
            if mode == 'wrong-release': release['expected_revision'] = 0
            if mode == 'not-background': transition['actual_background_after_Home'] = False
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                validate_background(provider, native, cipher, hold, release, marker, delivered, transition)

    def test_existing_ASGI_hold_releases_same_response_once_with_body_free_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); initialize_control(root); hold = arm_control(root, HOLD, 'hold_login', 0)
            calls = []; messages = []
            async def app(scope, receive, send):
                calls.append('one original service call')
                await send(dict(type='http.response.start', status=200, headers=[]))
                await send(dict(type='http.response.body', body=b'never-export-session-response'))
            async def send(value): messages.append(value)
            async def receive(): return dict(type='http.request', body=b'')
            async def run():
                original = asyncio.create_task(DeliveryProxy(app, root)(dict(type='http', method='POST', path='/sync/v1/auth/sessions'), receive, send))
                for _ in range(100):
                    if (root / 'fault-events' / (HOLD + '.json')).exists(): break
                    await asyncio.sleep(.01)
                self.assertEqual(messages, []); self.assertFalse(original.done())
                release = arm_control(root, RELEASE, 'release', 1)
                await original
                return release
            release = asyncio.run(run()); self.assertEqual(len(calls), 1)
            marker = json.loads((root / 'fault-events' / (HOLD + '.json')).read_bytes())
            delivered = json.loads((root / 'fault-events' / (HOLD + '.released.json')).read_bytes())
            self.assertNotIn('never-export', json.dumps([marker, delivered])); self.assertEqual(messages[0]['status'], 200)
            values = self.sequence(); validate_background(*values[:3], hold, release, marker, delivered, values[-1])

    def subject(self, root, *, early=False):
        subject = object.__new__(ReleaseAuthBackground); subject.output = root / 'evidence'; subject.output.mkdir()
        subject.owner = root / 'owner'; (subject.owner / 'fault-events').mkdir(parents=True)
        (subject.owner / 'fault-events' / (HOLD + '.json')).write_text('{}', 'utf-8')
        subject.args = SimpleNamespace(serial='emulator-5562'); subject.adb = Path('never-invoked-adb')
        subject.activity = 'cc.kronecker.opennexus/.MainActivity'; subject.identity = dict(own=True); subject.phases = []
        events = []; process = Mock(); process.returncode = 0
        process.poll.return_value = 1 if early else None
        process.communicate.return_value = (b'OPENNEXUS_RELEASE_AUTH_BACKGROUND_PHASE_OK login-b-held\n', b'')
        subject.stop = lambda: events.append('stop')
        subject.original_command = lambda label, args: events.append(label)
        subject.observe = Mock(return_value=self.sequence()[0][1]); subject.snapshot = Mock()
        subject.observer = Mock(); subject.observer.observe_identity.return_value = subject.identity
        def shell(*args, **kwargs):
            value = b'ready\n' if args[0] == 'run-as' else b'1234\n'
            return CompletedProcess(args, 0, value, b'')
        subject.shell = shell; subject.app_is_resumed = Mock(side_effect=[True, False, False])
        def release(mode):
            events.append(mode); (subject.owner / 'fault-events' / (HOLD + '.released.json')).write_text('{}', 'utf-8'); return RELEASE
        subject.control = Mock(side_effect=release)
        subject.device = lambda *args, **kwargs: events.append('finish-signal')
        return subject, process, events

    def test_actual_orchestration_keeps_driver_alive_through_Home_then_stops_before_private_read(self):
        with tempfile.TemporaryDirectory() as folder:
            subject, process, events = self.subject(Path(folder))
            with patch('android_release_auth_background.subprocess.Popen', return_value=process) as spawn, patch('android_release_auth_background.time.sleep'):
                _, release, transition = subject.held_background(HOLD)
            self.assertEqual(release, RELEASE); self.assertEqual(transition['pid'], 1234)
            self.assertEqual(events, ['stop', 'held-launch', 'held-home', 'release', 'finish-signal', 'stop'])
            spawn.assert_called_once(); subject.control.assert_called_once_with('release')
            subject.snapshot.assert_not_called(); subject.observer.ciphertext_fingerprints.assert_not_called()
            process.terminate.assert_not_called(); process.communicate.assert_called_once()
            intent = json.loads((subject.output / 'held-instrument-intent.json').read_bytes())
            self.assertFalse(intent['automatic_retry']); self.assertIn('ReleaseAuthBackgroundRuntimeProbe', ' '.join(intent['arguments']))

    def test_ended_driver_is_not_replayed_or_released_after_vacuous_background(self):
        with tempfile.TemporaryDirectory() as folder:
            subject, process, events = self.subject(Path(folder), early=True)
            with patch('android_release_auth_background.subprocess.Popen', return_value=process) as spawn:
                with self.assertRaisesRegex(RuntimeError, 'DRIVER_ENDED_EARLY'): subject.held_background(HOLD)
            spawn.assert_called_once(); subject.control.assert_not_called(); subject.snapshot.assert_not_called()
            self.assertNotIn('held-home', events); self.assertNotIn('finish-signal', events)
            result = json.loads((subject.output / 'held-instrument-result.json').read_bytes())
            self.assertTrue(result['unknown_result']); self.assertFalse(result['automatic_retry'])

    def test_unknown_async_result_does_not_start_another_login(self):
        with tempfile.TemporaryDirectory() as folder:
            subject = object.__new__(ReleaseAuthBackground); subject.output = Path(folder)
            provider, native, cipher, *_ = self.sequence()
            subject.prepare = Mock(); subject.phase = Mock(); subject.control = Mock(return_value=HOLD)
            subject.observe = Mock(return_value=provider[0]); subject.snapshot = Mock(return_value=native[0])
            subject.observer = Mock(); subject.observer.ciphertext_fingerprints.return_value = cipher[0]
            subject.held_background = Mock(side_effect=RuntimeError('original async unknown'))
            with self.assertRaisesRegex(RuntimeError, 'original async unknown'): subject.run()
            subject.phase.assert_called_once_with('login-a', 'bootstrap-login')
            subject.control.assert_called_once_with('hold_login'); subject.held_background.assert_called_once_with(HOLD)


if __name__ == '__main__': unittest.main()
