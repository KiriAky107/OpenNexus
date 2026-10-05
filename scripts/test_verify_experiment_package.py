"""Installer trust checks with offline synthetic payloads; never launch them."""
import importlib.util
import copy
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('experiment_package', Path(__file__).with_name('verify-experiment-package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.staging = package.ROOT / '.build'
        self.staging.mkdir(exist_ok=True)
    def fixture(self, payload):
        lock = json.loads((package.ROOT / 'scripts/experiment-runtime-lock.json').read_text(encoding='utf-8'))
        runtime = payload / 'runtimes' / lock['runtime_id']
        runtime.mkdir(parents=True)
        files = {}
        for name in ('python.exe', 'python313._pth', 'LICENSE.txt'):
            (runtime / name).write_bytes(b'locked')
            files[name] = {'bytes': 6, 'sha256': package.builder.digest(runtime / name)}
        manifest = json.dumps({'schema_version': 1, 'lock': lock, 'files': files}).encode()
        (runtime / 'runtime.json').write_bytes(manifest)
        (payload / 'OpenNexus.exe').write_bytes(b'Host-with-embedded-inventory:' + manifest)
        return runtime, manifest

    def test_forged_package_receipt_cannot_replace_host_inventory(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='package-test-') as directory:
            payload = Path(directory)
            runtime, manifest = self.fixture(payload)
            self.assertEqual(package.verify_payload(payload, manifest)['files_verified'], 3)
            (runtime / 'python.exe').write_bytes(b'edited')
            forged = json.loads(manifest)
            forged['files']['python.exe']['sha256'] = package.builder.digest(runtime / 'python.exe')
            (runtime / 'runtime.json').write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, 'receipt differs'):
                package.verify_payload(payload, manifest)

    def test_missing_embedded_authority_and_extra_module_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='package-test-') as directory:
            payload = Path(directory)
            runtime, manifest = self.fixture(payload)
            (payload / 'OpenNexus.exe').write_bytes(b'unrelated Host')
            with self.assertRaisesRegex(ValueError, 'Host does not embed'):
                package.verify_payload(payload, manifest)
            (payload / 'OpenNexus.exe').write_bytes(manifest)
            (runtime / 'injected.pyd').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError, 'inventory differs'):
                package.verify_payload(payload, manifest)

    def test_hardlinked_package_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='package-test-') as directory:
            payload = Path(directory)
            runtime, manifest = self.fixture(payload)
            outside = payload / 'receipt-copy.json'
            outside.write_bytes(manifest)
            (runtime / 'runtime.json').unlink()
            (runtime / 'runtime.json').hardlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'receipt is unsafe'):
                package.verify_payload(payload, manifest)

    def test_storage_receipt_rejects_wrong_runtime_missing_denial_and_running_job(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='storage-receipt-') as directory:
            runtime, manifest = self.fixture(Path(directory))
            lock = json.loads(manifest)['lock']
            report = {'runtime': lock['version'], 'executable': str(runtime / lock['entrypoint']),
                      'isolated': 1, 'registry_profile_verified': True,
                      'input_acl_error': 5, 'private_acl_error': 5,
                      'input_write': {'denied': True}, 'private_read': {'denied': True}}
            operations = ('registry_write', 'registry_child_write', 'registry_acl', 'registry_parent_write')
            report.update({key: {'denied': True, 'winerror': 5} for key in operations})
            result = {'runtime_id': lock['runtime_id'], 'remaining_processes': 0, 'report': report}
            package.verify_storage_receipt(result, runtime, lock)
            for field, invalid in [('runtime_id', 'other'), ('remaining_processes', 1), ('remaining_processes', False)]:
                altered = copy.deepcopy(result)
                altered[field] = invalid
                with self.assertRaises(ValueError):
                    package.verify_storage_receipt(altered, runtime, lock)
            for key in operations:
                altered = copy.deepcopy(result)
                altered['report'][key]['denied'] = False
                with self.assertRaisesRegex(ValueError, key):
                    package.verify_storage_receipt(altered, runtime, lock)
            wrong = runtime / 'other.exe'
            wrong.write_bytes(b'other synthetic interpreter')
            result['report']['executable'] = str(wrong)
            with self.assertRaises(ValueError):
                package.verify_storage_receipt(result, runtime, lock)

    def test_source_receipt_requires_selected_read_only_copies_and_host_positive_control(self):
        operations = ('entry_write', 'entry_delete', 'source_folder_rename', 'neighbor_create', 'original_unselected_read')
        basic = {key: {'denied': True} for key in operations}
        sources = dict(outside_profile=True, bytes_preserved=True, unselected_absent=True,
                       host_original_editable=True, selected_files=2)
        package.verify_sources_receipt(basic, sources)
        for key in sources:
            changed = dict(sources)
            changed[key] = False
            with self.assertRaises(ValueError):
                package.verify_sources_receipt(basic, changed)
        for key in operations:
            changed = copy.deepcopy(basic)
            changed[key]['denied'] = False
            with self.assertRaisesRegex(ValueError, key):
                package.verify_sources_receipt(changed, sources)

    def test_write_receipt_requires_cumulative_limit_and_complete_tree_cleanup(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='write-receipt-') as directory:
            runtime, manifest = self.fixture(Path(directory))
            lock = json.loads(manifest)['lock']
            report = {'runtime': lock['version'], 'executable': str(runtime / lock['entrypoint']), 'isolated': 1}
            item = {'error': 'EXPERIMENT_WRITE_IO_LIMIT_EXCEEDED', 'remaining_processes': 0,
                    'write_io_bytes': 10 * 1024 * 1024, 'write_io_limit_bytes': 9 * 1024 * 1024,
                    'filesystem_bytes': 65536, 'disk_limit_bytes': 8 * 1024 * 1024,
                    'stdout_bytes': 0, 'stderr_bytes': 0}
            resources = {mode: dict(item) for mode in ('write-truncate', 'write-children', 'write-delete')}
            resources['logs'] = dict(item, error='completed', write_io_bytes=4 * 1024 * 1024,
                                    stdout_bytes=2 * 1024 * 1024, stderr_bytes=2 * 1024 * 1024)
            result = {'runtime_id': lock['runtime_id'], 'report': report, 'resources': resources}
            package.verify_write_receipt(result, runtime, lock)
            for field, value in [('remaining_processes', 1), ('remaining_processes', False),
                                 ('write_io_bytes', 1), ('filesystem_bytes', 8 * 1024 * 1024),
                                 ('error', 'completed')]:
                altered = copy.deepcopy(result)
                altered['resources']['write-children'][field] = value
                with self.assertRaises(ValueError):
                    package.verify_write_receipt(altered, runtime, lock)
            result['resources']['write-delete']['error'] = 'EXTENSION_RESOURCE_MONITOR_FAILED'
            package.verify_write_receipt(result, runtime, lock)
            result['report']['runtime'] = 'other'
            with self.assertRaises(ValueError):
                package.verify_write_receipt(result, runtime, lock)


if __name__ == '__main__':
    unittest.main()
