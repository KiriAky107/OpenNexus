"""Paired-job ordering, isolation and original public-artifact contracts."""
import os
from pathlib import Path
import unittest
import yaml

from android_product_pair_stages import STAGES
from android_product_pair_clients import CLIENTS
from test_android_desktop_workflow import admitted


class ProductPairWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(os.environ.get('ANDROID_PAIR_WORKFLOW',Path(__file__).resolve().parents[1]/'.github/workflows/android.yml'))
        cls.workflow=yaml.load(path.read_text(encoding='utf-8'),Loader=yaml.BaseLoader)

    def test_pair_uploads_the_new_bounded_read_and_cursor_observations(self):
        step=next(s for s in self.workflow['jobs']['desktop-pair']['steps']
                  if s.get('name')=='Preserve bounded Desktop pair receipts without private key, data or raw logs')
        paths=[p.strip() for p in step['with']['path'].splitlines() if p.strip()]
        base='.build/android-desktop-pair/${{ github.run_id }}/'
        for path in ('stage-1/account-read-*.json','stage-2/account-read-*.json',
                     'stage-1/cycle-*-observation.json','stage-2/cycle-*-observation.json',
                     'stage-2/before-cold-native.json','stage-2/after-cold-native.json','stage-2/after-cold-files.json'):
            self.assertIn(base+path,paths)
        self.assertFalse(any('/data/' in p or '/vault/' in p or '**' in p or p.endswith('/*.json') for p in paths))

    def test_both_jobs_use_the_same_explicit_source_run_admission_without_a_client_rebuild(self):
        jobs=self.workflow['jobs']
        linux=next(s['run'] for s in jobs['android-desktop-pair']['steps'] if s.get('name')=='Validate the immutable reuse selection')
        windows=next(s['run'] for s in jobs['desktop-pair']['steps'] if s.get('name')=='Validate original pair inputs and names')
        self.assertIn('fixed_android_client(os.environ[\'REUSE_COMMIT\'],os.environ[\'REUSE_RUN\'])',linux)
        self.assertIn('android_product_pair_clients.py "$env:ANDROID_COMPILED_SOURCE" "$env:ANDROID_BUILD_RUN_ID"',windows)
        for client in CLIENTS:
            for name in ('desktop-pair','android-desktop-pair'):
                self.assertTrue(admitted(jobs[name]['if'],'desktop-pair',reuse=client.build_run))
            for name in ('debug-apk','emulator-runtime','desktop-startup'):
                self.assertFalse(admitted(jobs[name]['if'],'desktop-pair',reuse=client.build_run))

    def test_pair_uses_two_exclusive_jobs_without_rebuilding_or_running_old_scopes(self):
        jobs=self.workflow['jobs']
        self.assertIn('desktop-pair',self.workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        for job in ('debug-apk','emulator-runtime','desktop-startup'):
            self.assertFalse(admitted(jobs[job]['if'],'desktop-pair',reuse='39054475515'))
        for job,platform in [('desktop-pair','windows-2022'),('android-desktop-pair','ubuntu-24.04')]:
            self.assertTrue(admitted(jobs[job]['if'],'desktop-pair',reuse='39054475515'))
            self.assertFalse(admitted(jobs[job]['if'],'sync-conflicts',reuse='39054475515'))
            self.assertEqual(jobs[job]['runs-on'],platform);self.assertNotIn('needs',jobs[job])
            commands='\n'.join(s.get('run','') for s in jobs[job]['steps'])
            for forbidden in ('cargo','gradle','tauri build','pnpm install','npm install'):self.assertNotIn(forbidden,commands)

    def test_each_original_named_exchange_upload_has_exactly_one_known_exchange_file(self):
        producers={'recipient':('desktop-pair','Publish owned desktop receiving key'),
                   'sealed':('android-desktop-pair','Publish sealed owned fixture'),**STAGES}
        for stage,(job,name) in producers.items():
            steps=[s for s in self.workflow['jobs'][job]['steps'] if s.get('name')==name]
            self.assertEqual(len(steps),1);step=steps[0]
            self.assertEqual(step['uses'],'actions/upload-artifact@v7')
            self.assertEqual(step['with']['name'],'onx-pair-'+stage+'-${{ github.run_id }}-${{ env.PAIR_TOOLS_SHORT }}')
            path=step['with']['path'];self.assertTrue(path.endswith('/exchange.json'));self.assertNotIn('*',path)
            self.assertNotIn('\n',path);self.assertEqual(step['with']['if-no-files-found'],'error')

    def test_cross_job_stages_have_no_dependency_deadlock_and_keep_the_same_clients_alive_between_steps(self):
        cases={'desktop-pair':['Prepare the original receiving key and fixed published installer','Publish owned desktop receiving key',
            'Run actual Desktop full merge, Markdown and Canvas edit and original upload','Publish original desktop outbound',
            'Cold reopen the same Desktop device and read original Android return','Publish original desktop cold receipt'],
            'android-desktop-pair':['Run the actual original Android first stage','Publish sealed owned fixture',
            'Run the same original Android incoming and return stage','Publish original Android return',
            'Verify the actual original Desktop cold receipt and PG/S3 bytes','Stop only the original paired emulator']}
        for job,names in cases.items():
            actual=[s.get('name') for s in self.workflow['jobs'][job]['steps']]
            positions=[actual.index(name) for name in names];self.assertEqual(positions,sorted(positions))
        steps=self.workflow['jobs']['android-desktop-pair']['steps']
        for name in ('Stop only the original paired emulator','Stop only the original paired HTTPS origin and providers'):
            self.assertEqual(next(s for s in steps if s.get('name')==name)['if'],'always()')

    def test_service_environment_stays_frozen_and_crypto_is_a_separate_hash_pinned_tool_overlay(self):
        steps=self.workflow['jobs']['android-desktop-pair']['steps']
        install=next(s['run'] for s in steps if s.get('name')=='Install an isolated tool overlay from existing frozen backend wheels')
        for flag in ('--target .build/android-pair-crypto','--no-deps','--require-hashes','--only-binary :all:'):
            self.assertIn(flag,install)
        for phase in ('first','returned','final'):
            step=next(s for s in steps if 'android_product_pair_linux.py '+phase+' ' in s.get('run',''))
            self.assertEqual(step['env']['PYTHONPATH'],'${{ github.workspace }}/.build/android-pair-crypto')
        provider=next(s for s in steps if s.get('name')=='Prepare the independent fixed Sync interoperability origin')
        self.assertIn('uv sync --frozen --project .build/android-auth-source',provider['run'])
        self.assertNotIn('PYTHONPATH',provider.get('env',{}))

    def test_public_evidence_does_not_upload_private_keys_databases_or_profile_vault_contents(self):
        for job in ('desktop-pair','android-desktop-pair'):
            steps=[s for s in self.workflow['jobs'][job]['steps'] if s.get('uses','').startswith('actions/upload-artifact@')
                   and not s['with']['name'].startswith('onx-pair-')]
            self.assertEqual(len(steps),1);paths=steps[0]['with']['path']
            for forbidden in ('private','receiving-key','.bin','.sqlite3','driver-build','native-host.log','/data/','/vault/'):
                self.assertNotIn(forbidden,paths)
            self.assertEqual(steps[0]['if'],'always()')


if __name__=='__main__':unittest.main()
