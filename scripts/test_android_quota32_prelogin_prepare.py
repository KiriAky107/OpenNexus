"""RAM-only ordering of the real hosted prepare method, not service acceptance."""
from contextlib import ExitStack
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_auth_hosted as hosted
import android_release_sync_quota_fixture as quota
import android_sync_expiration as expiration
import android_sync_fixture as sync


class PreloginPrepare(unittest.TestCase):
    def harness(self, fixture=quota.FIXTURE, *, zero_error=None, readiness_error=False,
                existing=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        events = []
        root = Path(__file__).absolute().parent / 'never-created-quota32-owner'
        endpoint = 'https://controlled-owned-origin.trycloudflare.com'
        script = root / 'controlled-fixture.py'

        def exists(path):
            return path.name == 'tunnel-origin.private.log' or (existing and path == root)

        stack.enter_context(patch.object(Path, 'exists', exists))
        stack.enter_context(patch.object(Path, 'resolve', lambda path, **kwargs: path))
        stack.enter_context(patch.object(Path, 'mkdir', lambda path, **kwargs: events.append(('mkdir', path))))
        stack.enter_context(patch.object(Path, 'read_bytes', lambda path: b'controlled fixture source'))
        stack.enter_context(patch.object(Path, 'read_text', lambda path, *args, **kwargs: endpoint))
        stack.enter_context(patch.object(hosted, 'require_hosted', lambda: events.append(('hosted',))))
        stack.enter_context(patch.object(hosted, 'initialize_control', lambda path: events.append(('auth-control', path))))
        stack.enter_context(patch.object(sync, 'initialize_control', lambda path: events.append(('sync-control', path))))
        stack.enter_context(patch.object(expiration.OwnedClock, 'initialize', lambda path: events.append(('clock-control', path))))
        stack.enter_context(patch.object(hosted, 'minimal_environment', lambda path: {}))
        stack.enter_context(patch.object(hosted.secrets, 'token_hex', lambda count: 'a' * (count * 2)))
        stack.enter_context(patch.object(hosted.secrets, 'token_urlsafe', lambda count: 'controlled-password-' + 'x' * count))
        stack.enter_context(patch.dict(os.environ, GITHUB_WORKSPACE=str(root.parent), GITHUB_RUN_ID='12345', GITHUB_RUN_ATTEMPT='1'))
        stack.enter_context(patch.object(hosted.subprocess, 'check_output', side_effect=[hosted.SERVICE_SHA + '\n', '']))
        stack.enter_context(patch.object(hosted.subprocess, 'Popen', side_effect=AssertionError('real child forbidden')))
        stack.enter_context(patch.object(hosted.subprocess, 'run', side_effect=AssertionError('real command forbidden')))
        stack.enter_context(patch.object(os, 'open', side_effect=AssertionError('real file forbidden')))
        stack.enter_context(patch.object(hosted.time, 'sleep', side_effect=AssertionError('real wait forbidden')))
        times = iter([0, 100]) if readiness_error else None
        stack.enter_context(patch.object(hosted.time, 'monotonic', lambda: next(times) if times else 100))

        def atomic(path, value, *, exclusive=False):
            self.assertTrue(exclusive)
            events.append(('private-config', path.name))

        def handshake(address):
            events.append(('handshake', address))
            if readiness_error:
                raise RuntimeError('controlled readiness unavailable')
            return dict(protocol=1, chunk_size=1048576, max_object_size=104857600,
                        encryption='transport-only')

        stack.enter_context(patch.object(hosted, 'atomic_json', atomic))
        stack.enter_context(patch.object(hosted, 'origin_handshake', handshake))
        stack.enter_context(patch.object(hosted, 'download_cloudflared', lambda path: {'controlled': True}))
        origin = hosted.HostedOrigin(root, fixture=fixture)
        stack.enter_context(patch.object(origin, 'fixture_script', return_value=script))
        stack.enter_context(patch.object(origin, 'save', lambda: events.append(('save', origin.state['result']))))
        stack.enter_context(patch.object(origin, 'command', lambda argv, stage, **kwargs: events.append(('command', stage))))
        stack.enter_context(patch.object(origin, 'start_container', lambda *args: events.append(('container', args[0]))))
        stack.enter_context(patch.object(origin, 'spawn', lambda name, *args: (events.append(('spawn', name)) or SimpleNamespace(poll=lambda: None))))
        stack.enter_context(patch.object(origin, 'stop', side_effect=AssertionError('implicit stop forbidden')))

        def zero(path):
            self.assertIs(path, root)
            self.assertEqual(origin.state['result'], 'ready')
            self.assertEqual(events[-1], ('save', 'ready'))
            self.assertIn(('private-config', 'device-private.json'), events)
            self.assertEqual(sum(event[0] == 'handshake' for event in events), 2)
            events.append(('historical-zero', path))
            if zero_error:
                raise zero_error
            # The owning prepare does not export or inspect private proof bytes.
            return object()

        verifier = stack.enter_context(patch.object(quota, 'verify_prepared', side_effect=zero))
        return origin, events, verifier

    def test_quota32_zero_is_once_after_ready_and_before_successful_return(self):
        origin, events, verifier = self.harness()
        result = origin.prepare()
        events.append(('consumer-login-would-begin-after-return',))
        verifier.assert_called_once_with(origin.root)
        self.assertEqual(events[-2], ('historical-zero', origin.root))
        self.assertEqual(result['fixture'], quota.FIXTURE)
        self.assertIs(result['Android_TLS_or_auth_verified'], False)
        self.assertEqual(set(result), {'source', 'fixture', 'fixture_script_sha256', 'endpoint',
            'providers', 'images', 'handshake', 'cloudflared', 'Android_TLS_or_auth_verified',
            'secrets_in_artifacts'})

    def test_zero_failure_propagates_without_return_retry_or_implicit_cleanup(self):
        error = RuntimeError('controlled zero proof unavailable')
        origin, events, verifier = self.harness(zero_error=error)
        with self.assertRaises(RuntimeError) as caught:
            origin.prepare()
        self.assertIs(caught.exception, error)
        verifier.assert_called_once_with(origin.root)
        self.assertEqual(events[-1], ('historical-zero', origin.root))
        origin.stop.assert_not_called()

    def test_other_fixture_prepares_do_not_create_quota32_historical_proof(self):
        for fixture in ('auth', 'sync', 'sync-expiration', 'sync-quota'):
            with self.subTest(fixture=fixture):
                origin, events, verifier = self.harness(fixture)
                result = origin.prepare()
                verifier.assert_not_called()
                self.assertEqual(events[-1], ('save', 'ready'))
                self.assertEqual(result['fixture'], fixture)
                self.doCleanups()

    def test_readiness_failure_never_calls_zero_or_implicit_stop(self):
        origin, events, verifier = self.harness(readiness_error=True)
        with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_ORIGIN_NOT_READY$'):
            origin.prepare()
        verifier.assert_not_called()
        origin.stop.assert_not_called()
        self.assertEqual(origin.state['result'], 'preparing')

    def test_existing_origin_refuses_before_any_prepare_action_or_zero(self):
        origin, events, verifier = self.harness(existing=True)
        with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_ALREADY_PREPARED$'):
            origin.prepare()
        self.assertEqual(events, [('hosted',)])
        verifier.assert_not_called()
        origin.stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
