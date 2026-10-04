"""Installer trust checks with offline synthetic payloads; never launch them."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
