"""Owned file-boundary tests use fake input pipes and temp files only."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from android_desktop_handoff import HandoffError
from android_runtime_probe import APP, DRIVER
import android_original_outcome_files as files
import test_android_original_outcome_process as process_fixture
from test_android_original_outcome_status import NONCE


class FileBoundary(unittest.TestCase):
    def setUp(self):
        self.owner = process_fixture.OriginalStop('test_one_owned_stop_and_original_observation_no_replay_on_second_call')
        self.owner.setUp(); self.addCleanup(self.owner.doCleanups)
        self.probe, self.context, self.manifest = self.owner.probe, self.owner.context, self.owner.manifest
        self.probe.root = './files/owned/'
        self.current = b'old-owned-source\n'
        self.probe.original = Mock(side_effect=lambda path: self.current)
        self.publication = None; self.unknown = False
        self.permission = {'nonce': NONCE, 'phase': 'stale-confirm', 'fingerprint': 'd'*64}
        self.hosted = patch.object(files, 'hosted_scope'); self.hosted.start(); self.addCleanup(self.hosted.stop)
        def device(*args, **kwargs):
            if kwargs.get('input') is not None:
                self.assertEqual(args[:2], ('shell', '-T'))
                self.assertTrue(args[2].startswith('run-as '+APP+' ') or args[2].startswith('run-as '+DRIVER+' '))
                if args[2].startswith('run-as '+APP+' '): self.current = kwargs['input']
                else: self.publication = kwargs['input']
                if self.unknown: raise TimeoutError('PRIVATE_UNKNOWN_MESSAGE')
            return SimpleNamespace(returncode=0, stdout=b'device\n', stderr=b'')
        self.probe.device = Mock(side_effect=device)
        base = self.probe.shell.side_effect
        def shell(*args, **kwargs):
            if args[:3] == ('run-as', DRIVER, 'cat'):
                return SimpleNamespace(returncode=0, stdout=self.publication, stderr=b'')
            return base(*args, **kwargs)
        self.probe.shell.side_effect = shell

    def test_exact_permission_is_complete_and_exclusive_before_any_driver_input(self):
        value = files.publish_original_driver_file(self.probe, self.context, NONCE, 'stale-confirm', self.permission)
        self.assertEqual(value['returncode'], 0)
        self.assertEqual(json.loads(self.publication), self.permission)
        self.assertTrue((self.probe.output/'driver-publication-stale-confirm-intent.json').is_file())
        with self.assertRaises(FileExistsError):
            files.publish_original_driver_file(self.probe, self.context, NONCE, 'stale-confirm', self.permission)
        self.probe.device.assert_called_once()

    def test_unknown_permission_can_only_be_read_back_and_is_not_published_again(self):
        self.unknown = True
        with self.assertRaisesRegex(HandoffError, 'PUBLICATION_UNCONFIRMED'):
            files.publish_original_driver_file(self.probe, self.context, NONCE, 'stale-confirm', self.permission)
        observed = files.reconcile_original_driver_file(self.probe, self.context, NONCE, 'stale-confirm', 'original')
        self.assertTrue(observed['original_body_observed'])
        self.assertTrue(observed['unknown_command_not_relabelled'])
        self.probe.device.assert_called_once()
        self.assertNotIn('PRIVATE_UNKNOWN_MESSAGE', ''.join(p.read_text('utf-8') for p in self.probe.output.glob('*.json')))

    def test_one_concurrent_source_write_retains_before_and_planned_original_bytes(self):
        old, new = self.current, b'new-owned-source\n'
        files.mutate_recorded_original_source(self.probe, self.context, self.manifest, old, new)
        self.assertEqual((self.probe.output/'concurrent-source-before.md').read_bytes(), old)
        self.assertEqual((self.probe.output/'concurrent-source-planned.md').read_bytes(), new)
        self.assertEqual(self.current, new)
        with self.assertRaisesRegex(HandoffError, 'ALREADY_REQUESTED'):
            files.mutate_recorded_original_source(self.probe, self.context, self.manifest, old, new)
        self.probe.device.assert_called_once()

    def test_replaced_old_source_or_bad_permission_body_refuses_before_write(self):
        with self.assertRaisesRegex(HandoffError, 'SOURCE_CHANGED'):
            files.mutate_recorded_original_source(self.probe, self.context, self.manifest, b'other', b'new')
        with self.assertRaisesRegex(HandoffError, 'BODY_REFUSED'):
            files.publish_original_driver_file(self.probe, self.context, NONCE, 'stale-confirm', dict(self.permission, phase='other'))
        self.probe.device.assert_not_called()
        self.assertEqual(list(self.probe.output.iterdir()), [])

    def test_unknown_concurrent_write_is_preserved_and_never_retried_or_called_a_runtime_failure(self):
        self.unknown = True; old, new = self.current, b'new-own-bytes\n'
        with self.assertRaisesRegex(HandoffError, 'SOURCE_UNCONFIRMED'):
            files.mutate_recorded_original_source(self.probe, self.context, self.manifest, old, new)
        self.assertEqual(self.current, new)
        with self.assertRaisesRegex(HandoffError, 'ALREADY_REQUESTED'):
            files.mutate_recorded_original_source(self.probe, self.context, self.manifest, old, new)
        self.probe.device.assert_called_once()
        command = json.loads((self.probe.output/'concurrent-source-original-command.json').read_text('utf-8'))
        self.assertEqual(command['outcome'], 'unknown'); self.assertIsNone(command['returncode'])


if __name__ == '__main__': unittest.main()
