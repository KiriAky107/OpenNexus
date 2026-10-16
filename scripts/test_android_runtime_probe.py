import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from android_runtime_probe import APP, digest, require_hosted_emulator, validate_binding


class RuntimeBoundaries(unittest.TestCase):
    def test_developer_machine_and_phone_are_rejected_before_adb(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                require_hosted_emulator('emulator-5562')
        with patch('android_runtime_probe.sys.platform', 'linux'), patch.dict(os.environ,
            {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            with self.assertRaisesRegex(RuntimeError, 'unowned emulator or physical'):
                require_hosted_emulator('owned-phone-serial')

    def test_changed_apk_is_rejected_even_when_filename_and_commit_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'OpenNexus-android-debug.apk').write_bytes(b'changed apk')
            (root / 'source-provenance.json').write_text(json.dumps({'source_commit': 'fixed'}))
            (root / 'package-verification.json').write_text(json.dumps({'passed': True, 'source_clean': True,
                'source_commit': 'fixed', 'package': APP, 'debug_signing': True, 'sha256': digest(b'original apk')}))
            with self.assertRaisesRegex(RuntimeError, 'APK bytes changed'):
                validate_binding(root, 'fixed')

    def test_an_old_package_proof_cannot_be_used_for_a_new_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source-provenance.json').write_text(json.dumps({'source_commit': 'old'}))
            (root / 'package-verification.json').write_text(json.dumps({'passed': True, 'source_clean': True, 'source_commit': 'old'}))
            with self.assertRaisesRegex(RuntimeError, 'source mismatch'):
                validate_binding(root, 'new')


if __name__ == '__main__':
    unittest.main()
