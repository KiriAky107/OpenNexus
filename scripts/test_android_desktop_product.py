from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from android_desktop_handoff import HandoffError, canonical
from android_desktop_product import DESKTOP_SOURCE, directory_identity, original_marker, owned_root, safe_failure, start_product, verify_storage


class ProductIsolation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.context = {'run_id': 123, 'tools_commit': 'a' * 40, 'android_commit': 'b' * 40, 'desktop_commit': DESKTOP_SOURCE}
        self.env = {'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Windows', 'RUNNER_ENVIRONMENT': 'github-hosted',
                    'GITHUB_REPOSITORY': 'KiriAky107/OpenNexus', 'GITHUB_RUN_ID': '123', 'GITHUB_SHA': 'a' * 40,
                    'GITHUB_WORKSPACE': str(self.workspace)}

    def test_only_original_hosted_windows_run_can_name_its_root(self):
        root = owned_root(self.env, self.context, platform='nt')
        self.assertEqual(root, self.workspace / '.build/android-desktop-pair/123')
        self.assertEqual(list(self.workspace.iterdir()), [])
        for key, value in [('GITHUB_ACTIONS', ''), ('RUNNER_ENVIRONMENT', 'self-hosted'), ('RUNNER_OS', 'Linux'),
                           ('GITHUB_REPOSITORY', 'other/repo'), ('GITHUB_RUN_ID', '124'), ('GITHUB_SHA', 'c' * 40)]:
            with self.subTest(key=key), self.assertRaises(HandoffError):
                owned_root(dict(self.env, **{key: value}), self.context, platform='nt')

    def test_other_desktop_source_and_non_windows_platform_are_refused(self):
        with self.assertRaises(HandoffError):
            owned_root(self.env, dict(self.context, desktop_commit='0' * 40), platform='nt')
        with self.assertRaises(HandoffError):
            owned_root(self.env, self.context, platform='posix')

    def test_an_existing_file_cannot_be_adopted_as_an_owned_directory(self):
        (self.workspace / '.build').write_bytes(b'user file retained')
        with self.assertRaises(HandoffError):
            owned_root(self.env, self.context, platform='nt')
        self.assertEqual((self.workspace / '.build').read_bytes(), b'user file retained')

    def test_marker_requires_original_context_and_exact_directory_identity(self):
        root = self.workspace / 'owned'; root.mkdir()
        value = {'context': self.context, 'root_identity': directory_identity(root)}
        (root / 'owner.json').write_bytes(canonical(value))
        self.assertEqual(original_marker(root, self.context), value)
        for key in self.context:
            changed = deepcopy(self.context); changed[key] = 124 if key == 'run_id' else '0' * 40
            with self.subTest(key=key), self.assertRaises(HandoffError):
                original_marker(root, changed)
        value['root_identity'][1] += 1
        (root / 'owner.json').write_bytes(canonical(value))
        with self.assertRaises(HandoffError):
            original_marker(root, self.context)

    def test_extra_or_oversized_owner_data_is_not_trusted(self):
        root = self.workspace / 'owned'; root.mkdir()
        (root / 'owner.json').write_text(json.dumps({'context': self.context, 'root_identity': directory_identity(root),
                                                   'borrow_existing_profile': True}), encoding='utf-8')
        with self.assertRaises(HandoffError):
            original_marker(root, self.context)
        (root / 'owner.json').write_bytes(b'x' * 2049)
        with self.assertRaises(HandoffError):
            original_marker(root, self.context)

    def test_local_start_is_refused_before_any_profile_or_process_access(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_HOST_REFUSED$'):
                with start_product(self.context, 1):
                    self.fail('A local product must not start')
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_storage_requires_exact_active_and_configured_paths_not_a_json_substring(self):
        target = r'C:\owned\data'
        value = {'active_path': target, 'configured_path': target, 'default_path': r'C:\default',
                 'custom': True, 'restart_required': False}
        verify_storage(value, Path(target))
        for key, wrong in [('active_path', target + '-foreign'), ('configured_path', target + '-foreign'),
                           ('custom', False), ('restart_required', True)]:
            with self.subTest(key=key), self.assertRaises(HandoffError):
                verify_storage(dict(value, **{key: wrong}), Path(target))
        misleading = dict(value, active_path=r'C:\user\production', configured_path=r'C:\user\production', default_path=target)
        with self.assertRaises(HandoffError):
            verify_storage(misleading, Path(target))

    def test_credential_bearing_ui_exceptions_are_reduced_to_static_diagnostics(self):
        secret = 'OWNED_PASSWORD_MUST_NOT_APPEAR'
        value = safe_failure(TimeoutError('fill("' + secret + '") timed out'))
        self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(value, {'code': 'DESKTOP_PAIR_UI_FAILED', 'error_class': 'TimeoutError',
                                 'original_exception_text_returned': False})

    def test_canonical_owned_windows_paths_match_without_bypassing_restart_or_foreign_path_checks(self):
        target = r'D:\owned\data'
        value = {'active_path': '\\\\?\\' + target, 'configured_path': '\\\\?\\' + target,
                 'default_path': r'D:\default', 'custom': True, 'restart_required': False}
        verify_storage(value, Path(target))
        for changed in [dict(value, active_path=r'\\?\D:\owned\data-foreign'),
                        dict(value, configured_path=r'\\?\D:\user\production'),
                        dict(value, restart_required=True)]:
            with self.assertRaises(HandoffError): verify_storage(changed, Path(target))

    def test_only_absolute_drive_paths_can_supply_the_new_owned_profile_pointer(self):
        from android_desktop_product import windows_drive_path
        self.assertEqual(windows_drive_path(Path(r'D:\owned\data'), extended=True), r'\\?\D:\owned\data')
        self.assertEqual(windows_drive_path(r'\\?\D:\owned\data', extended=True), r'\\?\D:\owned\data')
        for path in [r'relative\data', r'\\server\share', r'\\.\C:', r'C:relative', r'D:\owned\..\user']:
            with self.subTest(path=path), self.assertRaises(HandoffError): windows_drive_path(path, extended=True)

    def test_only_known_static_bootstrap_codes_are_retained_without_exception_text(self):
        code = 'DESKTOP_PAIR_STORAGE_MISMATCH'
        self.assertEqual(safe_failure(HandoffError(code))['checkpoint_code'], code)
        for suffix in ('DELIVERY_INVALID', 'OUTCOME_UNKNOWN', 'NATIVE_REFUSED', 'TARGET_MISMATCH'):
            known = 'DESKTOP_PAIR_PICKER_' + suffix
            self.assertEqual(safe_failure(HandoffError(known))['checkpoint_code'], known)
        secret = 'OWNED_PASSWORD_MUST_NOT_APPEAR'
        for error in [HandoffError(secret), RuntimeError(code + secret), TimeoutError(secret)]:
            value = safe_failure(error)
            self.assertNotIn(secret, json.dumps(value))
            self.assertNotIn('checkpoint_code', value)

    def test_startup_checkpoint_diagnostics_preserve_only_known_literal_stages(self):
        secret = 'OWNED_PASSWORD_MUST_NOT_APPEAR'
        for checkpoint in ['core-version', 'workspace-readiness', 'owned-vault-open', secret]:
            value = safe_failure(RuntimeError(secret), checkpoint)
            self.assertEqual(value['failed_checkpoint'], checkpoint if checkpoint != secret else 'unknown')
            self.assertNotIn(secret, json.dumps(value))
            self.assertFalse(value['original_exception_text_returned'])


if __name__ == '__main__':
    unittest.main()
