"""Owned synthetic Git fixtures for deployment provenance and false-pass refusal."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build_service_release import build, package
from release_plan import ReleaseError


class ServiceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='onx-service-release-')
        self.root = Path(self.temp.name)
        self.repo = self.root/'repository'
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.email', 'fixture@example.invalid'), ('config', 'user.name', 'Service fixture')]:
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        files = {'.gitignore': '.build/\nfrontend/docs/\n',
                 'pyproject.toml': '[project]\nname="notesagent-community"\nversion="0.4.0"\n',
                 'uv.lock': 'controlled dependency lock\n', 'community/__main__.py': '# synthetic runtime source\n',
                 'deployment/opennexus-community.service': '# synthetic operator configuration\n',
                 'tests/private-fixture.py': '# not a deployment dependency\n'}
        for name, content in files.items():
            path = self.repo/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode())
        self.commit()
        self.output = self.repo/'.build/release-fixture'

    def commit(self):
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'controlled source'], cwd=self.repo, check=True, capture_output=True)
        self.sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_uses_fixed_git_bytes_and_excludes_test_and_private_files(self):
        private = self.repo/'frontend/docs/local.md'
        private.parent.mkdir(parents=True)
        private.write_text('controlled local original', 'utf-8')
        result = package(self.repo, self.output, 'community', self.sha)
        with zipfile.ZipFile(self.output/result['deployment']) as archive:
            self.assertEqual(archive.comment.decode(), self.sha)
            self.assertEqual(set(archive.namelist()), {'pyproject.toml', 'uv.lock', 'community/__main__.py', 'deployment/opennexus-community.service'})
        self.assertEqual(private.read_text('utf-8'), 'controlled local original')
        self.assertFalse((self.output/'verification.json').exists())

    def test_existing_output_and_outside_workspace_paths_are_preserved(self):
        self.output.mkdir(parents=True)
        original = self.output/'keep.bin'
        original.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            package(self.repo, self.output, 'community', self.sha)
        with self.assertRaisesRegex(ReleaseError, 'OWNED_BUILD_OUTPUT_REQUIRED'):
            package(self.repo, self.root/'outside', 'community', self.sha)
        self.assertEqual(original.read_bytes(), b'original')
        self.assertFalse((self.root/'outside').exists())

    def test_dirty_source_is_refused_before_any_build_directory_is_created(self):
        (self.repo/'community/__main__.py').write_text('# changed source', 'utf-8')
        with self.assertRaisesRegex(ReleaseError, 'FIXED_CLEAN_BUILD_SOURCE_REQUIRED'):
            package(self.repo, self.output, 'community', self.sha)
        self.assertFalse(self.output.exists())

    def test_tracked_private_documents_are_refused_before_packaging(self):
        private = self.repo/'documents/private.md'
        private.parent.mkdir()
        private.write_text('synthetic forbidden tracked path', 'utf-8')
        self.commit()
        with self.assertRaisesRegex(ReleaseError, 'PRIVATE_BUILD_SOURCE_TRACKED'):
            package(self.repo, self.output, 'community', self.sha)
        self.assertFalse(self.output.exists())

    def test_incomplete_deployment_cannot_be_called_a_build(self):
        (self.repo/'uv.lock').unlink()
        self.commit()
        with self.assertRaisesRegex(ReleaseError, 'DEPLOYMENT_FILES_MISSING'):
            package(self.repo, self.output, 'community', self.sha)

    def test_probe_failure_leaves_a_failed_verification_receipt(self):
        with patch('build_service_release.community_probe', side_effect=ReleaseError('CONTROLLED_PROBE_FAILURE')):
            with self.assertRaisesRegex(ReleaseError, 'CONTROLLED_PROBE_FAILURE'):
                build(self.repo, self.output, 'community', self.sha)
        self.assertFalse(json.loads((self.output/'verification.json').read_text())['passed'])

    def test_probe_success_cannot_approve_changed_deployment_bytes(self):
        def changed(_extracted, _work):
            archive = next(self.output.glob('*_deploy.zip'))
            with archive.open('ab') as output:
                output.write(b'controlled corruption')
            return {'passed': True, 'cleanup_complete': True, 'module_origin_verified': True}
        with patch('build_service_release.community_probe', side_effect=changed):
            with self.assertRaisesRegex(ReleaseError, 'SERVICE_BUILD_INPUTS_CHANGED'):
                build(self.repo, self.output, 'community', self.sha)
        self.assertFalse(json.loads((self.output/'verification.json').read_text())['passed'])

    def test_probe_success_cannot_approve_source_changes_during_validation(self):
        def changed(_extracted, _work):
            (self.repo/'community/__main__.py').write_text('# edited during smoke', 'utf-8')
            return {'passed': True, 'cleanup_complete': True, 'module_origin_verified': True}
        with patch('build_service_release.community_probe', side_effect=changed):
            with self.assertRaisesRegex(ReleaseError, 'FIXED_CLEAN_BUILD_SOURCE_REQUIRED'):
                build(self.repo, self.output, 'community', self.sha)
        self.assertFalse(json.loads((self.output/'verification.json').read_text())['passed'])

    def test_incomplete_probe_cleanup_cannot_approve_a_deployment(self):
        with patch('build_service_release.community_probe', return_value={'passed': True, 'cleanup_complete': False, 'module_origin_verified': True}):
            with self.assertRaisesRegex(ReleaseError, 'PACKAGED_SERVICE_PROBE_FAILED'):
                build(self.repo, self.output, 'community', self.sha)
        self.assertFalse(json.loads((self.output/'verification.json').read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
