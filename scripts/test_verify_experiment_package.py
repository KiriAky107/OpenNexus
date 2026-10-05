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

    def test_formal_worker_requires_real_results_complete_logs_and_empty_process_tree(self):
        with tempfile.TemporaryDirectory(dir=self.staging, prefix='worker-receipt-') as directory:
            runtime, manifest = self.fixture(Path(directory))
            lock = json.loads(manifest)['lock']
            def log(text='', seen=None, truncated=False):
                retained = len(text.encode('utf-8'))
                return dict(text=text, retained_bytes=retained,
                            bytes_seen=retained if seen is None else seen,
                            complete=True, read_error=False, invalid_utf8=False, truncated=truncated)
            runs = {}
            for mode, outcome, error, code in (
                    ('basic', 'completed', None, 0), ('logs', 'completed', None, 0),
                    ('nonzero', 'failed', 'EXPERIMENT_NONZERO_EXIT', 7),
                    ('cancel', 'cancelled', 'EXPERIMENT_CANCELLED', 1),
                    ('switch', 'cancelled', 'EXPERIMENT_CANCELLED', None),
                    ('shutdown', 'cancelled', 'EXPERIMENT_CANCELLED', None),
                    ('wall', 'limited', 'EXTENSION_TOOL_DEADLINE_EXCEEDED', None),
                    ('cpu', 'limited', 'EXTENSION_RESOURCE_CPU_EXCEEDED', 1)):
                runs[mode] = dict(remaining_processes=0,
                    runtime=dict(runtime_id=lock['runtime_id'], version=lock['version'], files=3),
                    result=dict(outcome=outcome, error=error, exit_code=code, elapsed_ms=1100,
                        user_cpu_ticks=10_000_000, peak_memory_bytes=1000000, final_disk_bytes=0,
                        logs=dict(stdout=log(), stderr=log())))
            for mode in ('cancel', 'switch', 'shutdown'):
                runs[mode]['result']['logs']['stdout'] = log('child-ready\r\n')
            for stream in ('stdout', 'stderr'):
                runs['logs']['result']['logs'][stream] = log('A' * 8192, 2 * 1024 * 1024, True)
            report = dict(executable=str(runtime / lock['entrypoint']), runtime=lock['version'], isolated=1,
                          argv=[], parent_environment_inherited=False, input_write_denied=True,
                          csv_total=18, first_name='中文', scratch_acl_access=dict(
                              root=5, parent=5, created_file=5, root_acl_change=5, file_acl_change=5,
                              outside_create=5, explicit_descriptor_acl_access=5,
                              cwd_is_scratch=True, scratch_read_write_delete=True,
                              protected_descriptor_acl_access=0))
            runs['basic']['result']['logs']['stdout'] = log('WORKER_REPORT:' + json.dumps(report) + '\r\n')
            receipt = dict(schema_version=1, runtime_id=lock['runtime_id'], runs=runs)
            package.verify_worker_receipt(receipt, runtime, lock)
            diagnostic = copy.deepcopy(receipt)
            diagnostic['runs']['logs']['result']['logs']['stderr']['bytes_seen'] += 129
            package.verify_worker_receipt(diagnostic, runtime, lock)
            cases = [
                (('schema_version',), True), (('runtime_id',), 'other'),
                (('runs', 'basic', 'remaining_processes'), False),
                (('runs', 'switch', 'remaining_processes'), 1),
                (('runs', 'shutdown', 'remaining_processes'), 1),
                (('runs', 'nonzero', 'result', 'outcome'), 'completed'),
                (('runs', 'nonzero', 'result', 'exit_code'), 0),
                (('runs', 'cpu', 'result', 'user_cpu_ticks'), 0),
                (('runs', 'wall', 'result', 'elapsed_ms'), 500),
                (('runs', 'basic', 'result', 'peak_memory_bytes'), None),
                (('runs', 'basic', 'result', 'final_disk_bytes'), False),
                (('runs', 'basic', 'runtime', 'version'), 'other'),
                (('runs', 'cancel', 'result', 'logs', 'stdout'), log()),
                (('runs', 'logs', 'result', 'logs', 'stderr', 'truncated'), False),
                (('runs', 'logs', 'result', 'logs', 'stdout', 'complete'), False),
                (('runs', 'logs', 'result', 'logs', 'stdout', 'read_error'), True),
            ]
            for path, value in cases:
                with self.subTest(path=path, value=value):
                    changed = copy.deepcopy(receipt)
                    parent = changed
                    for key in path[:-1]:
                        parent = parent[key]
                    parent[path[-1]] = value
                    with self.assertRaises(ValueError):
                        package.verify_worker_receipt(changed, runtime, lock)
            for field in report['scratch_acl_access']:
                with self.subTest(filesystem=field):
                    changed = copy.deepcopy(receipt)
                    altered = copy.deepcopy(report)
                    altered['scratch_acl_access'][field] = False if field.endswith('_access') else 0
                    changed['runs']['basic']['result']['logs']['stdout'] = log('WORKER_REPORT:' + json.dumps(altered))
                    with self.assertRaises(ValueError):
                        package.verify_worker_receipt(changed, runtime, lock)
            missing = copy.deepcopy(receipt)
            missing['runs'].pop('switch')
            with self.assertRaises(ValueError):
                package.verify_worker_receipt(missing, runtime, lock)
            wrong = runtime / 'other.exe'
            wrong.write_bytes(b'not the bundled interpreter')
            for field, value in (('executable', str(wrong)), ('argv', ['-c', 'unapproved']),
                                 ('parent_environment_inherited', True), ('input_write_denied', False),
                                 ('isolated', True), ('first_name', 'wrong'), ('csv_total', 19)):
                with self.subTest(report=field):
                    changed = copy.deepcopy(receipt)
                    altered = dict(report, **{field: value})
                    changed['runs']['basic']['result']['logs']['stdout'] = log('WORKER_REPORT:' + json.dumps(altered))
                    with self.assertRaises(ValueError):
                        package.verify_worker_receipt(changed, runtime, lock)

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
