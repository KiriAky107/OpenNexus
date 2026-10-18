import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from android_desktop_handoff import HandoffError
import android_desktop_runtime as runtime
from android_workflow_gate import admitted


class DesktopWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows/android.yml').read_text('utf-8'), Loader=yaml.BaseLoader)

    def test_desktop_scope_uses_existing_packaged_client_without_native_or_apk_build(self):
        job = self.workflow['jobs']['desktop-startup']
        self.assertEqual(job['runs-on'], 'windows-2022')
        self.assertEqual(job['if'], "github.event_name == 'workflow_dispatch' && inputs.runtime_scope == 'desktop-startup'")
        self.assertNotIn('needs', job)
        commands = '\n'.join(s.get('run', '') for s in job['steps'])
        self.assertIn('--frozen', commands)
        self.assertIn('android_desktop_runtime.py', commands)
        for build_tool in ('cargo', 'gradle', 'pnpm', 'npm ', 'tauri build'):
            self.assertNotIn(build_tool, commands)

    def test_android_build_and_emulator_jobs_exclude_desktop_scope(self):
        jobs = self.workflow['jobs']
        self.assertIn("inputs.runtime_scope != 'desktop-startup'", jobs['debug-apk']['if'])
        self.assertIn("&& inputs.runtime_scope != 'desktop-startup' &&", jobs['emulator-runtime']['if'])
        self.assertIn('desktop-startup', self.workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])

    def test_existing_auth_and_sync_evidence_uploads_remain_in_emulator_job(self):
        names = [s['with']['name'] for s in self.workflow['jobs']['emulator-runtime']['steps']
                 if s.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertIn('OpenNexus-android-${{ inputs.runtime_scope }}-${{ env.COMPILED_SHA }}', names)
        self.assertIn('OpenNexus-android-auth-${{ env.COMPILED_SHA }}', names)
        self.assertFalse(any('desktop-startup' in n for n in names))

    def test_desktop_artifacts_are_explicit_json_receipts_without_app_data_or_raw_logs(self):
        uploads = [s for s in self.workflow['jobs']['desktop-startup']['steps'] if s.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 1)
        paths = uploads[0]['with']['path'].splitlines()
        self.assertTrue(all(p.endswith('.json') and p.startswith('.build/android-desktop-pair/*/') for p in paths))
        for excluded in ('private', 'credential', 'native-host.log', '/data/', '/vault/', 'installer.exe'):
            self.assertFalse(any(excluded in p for p in paths))

    def test_runtime_entry_refuses_local_execution_before_download_or_workspace_creation(self):
        with tempfile.TemporaryDirectory() as work:
            env = {'GITHUB_RUN_ID': '123', 'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKSPACE': work,
                   'ANDROID_COMPILED_SOURCE': runtime.ANDROID_SOURCE, 'ANDROID_BUILD_RUN_ID': '39006953890'}
            with patch.dict(os.environ, env, clear=True), patch.object(runtime.urllib.request, 'urlopen') as download:
                with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_HOST_REFUSED$'):
                    runtime.main()
                download.assert_not_called()
            self.assertEqual(list(Path(work).iterdir()), [])

    def test_runtime_entry_refuses_wrong_android_context_before_any_io(self):
        env = {'GITHUB_RUN_ID': '123', 'ANDROID_COMPILED_SOURCE': '0' * 40, 'ANDROID_BUILD_RUN_ID': '39006953890'}
        with patch.dict(os.environ, env, clear=True), patch.object(runtime, 'claim_root') as claim:
            with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_ANDROID_CONTEXT_INVALID$'):
                runtime.main()
            claim.assert_not_called()

    def test_build_only_produces_the_new_apk_without_launching_either_client(self):
        self.assertIn('build-only', self.workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        jobs = self.workflow['jobs']
        self.assertTrue(admitted(jobs['debug-apk']['if'], 'build-only'))
        self.assertFalse(admitted(jobs['emulator-runtime']['if'], 'build-only'))
        self.assertFalse(admitted(jobs['desktop-startup']['if'], 'build-only'))

    def test_existing_reuse_and_desktop_scopes_keep_their_actual_job_boundaries(self):
        jobs = self.workflow['jobs']
        for scope, reuse, expected in [('sync-conflicts', '39006953890', (False, True, False)),
                                       ('all', '', (True, True, False)),
                                       ('desktop-startup', '39006953890', (False, False, True))]:
            with self.subTest(scope=scope):
                actual = tuple(admitted(jobs[name]['if'], scope, reuse=reuse)
                               for name in ['debug-apk', 'emulator-runtime', 'desktop-startup'])
                self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
