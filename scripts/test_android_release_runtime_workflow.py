import os
from pathlib import Path
import shlex
import tempfile
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET
import yaml

from android_workflow_gate import admitted
from release_plan import ReleaseError


ROOT=Path(__file__).resolve().parents[1]


class ReleaseRuntimeWorkflow(unittest.TestCase):
    def flow(self):
        return yaml.load((ROOT/'.github/workflows/android-release-runtime.yml').read_text('utf-8'),Loader=yaml.BaseLoader)

    def test_dispatch_has_no_debug_build_or_old_runtime_side_jobs(self):
        caller=yaml.load((ROOT/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        job=caller['jobs']['release-runtime']
        self.assertEqual(job['uses'],'./.github/workflows/android-release-runtime.yml')
        self.assertEqual(job['with']['runtime_input'],'${{ inputs.release_runtime_input }}')
        self.assertTrue(admitted(job['if'],'release-runtime'))
        for scope in ('all','release-inputs','build-only','external-links'):self.assertFalse(admitted(job['if'],scope))
        for reuse in ('','123'):
            for name in ('debug-apk','emulator-runtime','desktop-startup','desktop-pair','android-desktop-pair','release-inputs'):
                self.assertFalse(admitted(caller['jobs'][name]['if'],'release-runtime',reuse=reuse,build='skipped'))
        self.assertEqual(set(self.flow()['on']),{'workflow_call'})

    def test_media_scene_uses_only_successor_script_and_unknown_scene_is_rejected_before_read(self):
        caller=yaml.load((ROOT/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        option=caller['on']['workflow_dispatch']['inputs']['release_runtime_scene']
        self.assertEqual(option['options'],['upgrade','media','auth','auth-recovery','auth-background','auth-certificate','sync']);self.assertEqual(option['default'],'upgrade')
        self.assertLessEqual(len(caller['on']['workflow_dispatch']['inputs']),25)
        flow=self.flow();self.assertEqual(flow['on']['workflow_call']['inputs']['scene']['default'],'upgrade')
        steps=flow['jobs']['release-upgrade']['steps']
        upgrade=next(s for s in steps if s.get('name')=='Recheck actual signed binaries and update without clear data')
        media=next(s for s in steps if s.get('name')=='Verify only the signed successor through system media and SAF')
        self.assertEqual(upgrade['if'],"inputs.scene == 'upgrade'");self.assertEqual(media['if'],"inputs.scene == 'media'")
        self.assertIn('android_release_media.py',media['run']);self.assertNotIn('android_release_upgrade.py',media['run'])
        gate=next(s['run'] for s in flow['jobs']['fetch-inputs']['steps'] if s.get('name','').startswith('Admit fixed'))
        code=gate.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        with patch.dict(os.environ,RUNTIME_SCENE='media-extra'),patch('subprocess.check_output') as command:
            with self.assertRaises(AssertionError):exec(compile(code,'closed-release-scene','exec'),{})
            command.assert_not_called()

    def test_draft_read_token_is_not_given_to_emulator_job(self):
        flow=self.flow();fetch=flow['jobs']['fetch-inputs'];runtime=flow['jobs']['release-upgrade']
        self.assertEqual(fetch['permissions'],{'contents':'write'})
        self.assertEqual(runtime['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(runtime['needs'],'fetch-inputs')
        self.assertNotIn('GITHUB_TOKEN',str(runtime));self.assertNotIn('secrets.',str(flow))
        reader=next(s for s in fetch['steps'] if s.get('name','').startswith('GET only'))
        self.assertEqual(reader['env']['GITHUB_TOKEN'],'${{ github.token }}')
        self.assertIn('android_signed_runtime_inputs.py',reader['run'])
        for job in (fetch,runtime):
            checkout=next(s for s in job['steps'] if s.get('uses')=='actions/checkout@v7')
            self.assertEqual(checkout['with']['persist-credentials'],'false')
        self.assertNotIn('pnpm',str(flow));self.assertNotIn('gradlew',str(flow));self.assertNotIn('cargo build',str(flow))

    def test_auth_origin_is_fixed_and_private_provider_paths_are_never_uploaded(self):
        runtime=self.flow()['jobs']['release-upgrade'];steps=runtime['steps']
        checkout=next(s for s in steps if s.get('with',{}).get('repository')=='KiriAky107/Sync-for-OpenNexus')
        self.assertEqual(checkout['if'],"inputs.scene == 'auth' || inputs.scene == 'auth-recovery' || inputs.scene == 'auth-background' || inputs.scene == 'auth-certificate' || inputs.scene == 'sync'")
        self.assertEqual(checkout['with']['ref'],'29e2e6d373ea752352981bdad05d34a7a0d9c3d9')
        self.assertEqual(checkout['with']['persist-credentials'],'false')
        auth=next(s for s in steps if 'android_release_auth.py' in s.get('run',''))
        self.assertEqual(auth['if'],"inputs.scene == 'auth'");self.assertNotIn('android_release_upgrade.py',auth['run'])
        prepare=next(s for s in steps if s.get('name')=='Prepare only the fixed independent HTTPS auth origin')
        self.assertIn('uv sync --frozen',prepare['run']);self.assertNotIn('GITHUB_TOKEN',str(prepare))
        cleanup=next(s for s in steps if s.get('name')=='Stop only the original owned HTTPS auth origin')
        self.assertEqual(cleanup['if'],"always() && (inputs.scene == 'auth' || inputs.scene == 'auth-recovery' || inputs.scene == 'auth-background' || inputs.scene == 'auth-certificate' || inputs.scene == 'sync')")
        upload=next(s for s in steps if s.get('uses')=='actions/upload-artifact@v7')['with']['path']
        self.assertNotIn('owned-auth-',upload);self.assertNotIn('driver-build',upload);self.assertNotIn('android-auth-source',upload)
        self.assertIn('origin-proof/',upload)

    def test_recovery_selects_only_its_original_response_script_and_private_origin(self):
        steps=self.flow()['jobs']['release-upgrade']['steps']
        recovery=next(s for s in steps if 'android_release_auth_recovery.py' in s.get('run',''))
        self.assertEqual(recovery['if'],"inputs.scene == 'auth-recovery'")
        self.assertNotIn('android_release_auth.py ',recovery['run'])
        origin=next(s for s in steps if s.get('name')=='Prepare only the fixed independent HTTPS auth origin')
        self.assertEqual(origin['if'],"inputs.scene == 'auth' || inputs.scene == 'auth-recovery' || inputs.scene == 'auth-background' || inputs.scene == 'auth-certificate' || inputs.scene == 'sync'")
        self.assertLess(steps.index(recovery),next(i for i,s in enumerate(steps) if s.get('name')=='Stop only the original owned HTTPS auth origin'))
        self.assertTrue(all('unused' not in s.get('name','').lower() for s in steps))

    def test_background_has_separate_scene_without_replaying_normal_recovery_or_upgrade(self):
        steps=self.flow()['jobs']['release-upgrade']['steps']
        background=next(s for s in steps if 'android_release_auth_background.py' in s.get('run',''))
        self.assertEqual(background['if'],"inputs.scene == 'auth-background'")
        self.assertNotIn('android_release_auth_recovery.py',background['run'])
        self.assertNotIn('android_release_auth.py ',background['run'])
        self.assertIn("inputs.scene == 'auth-background' && 'auth-background'",str(steps[-1]['with']['name']))

    def test_certificate_selects_only_its_original_TLS_script_and_same_private_origin_boundary(self):
        steps=self.flow()['jobs']['release-upgrade']['steps']
        certificate=next(s for s in steps if 'android_release_auth_certificate.py' in s.get('run',''))
        self.assertEqual(certificate['if'],"inputs.scene == 'auth-certificate'")
        self.assertNotIn('android_certificate_probe.py',certificate['run'])
        self.assertNotIn('android_release_auth.py ',certificate['run'])
        self.assertIn("inputs.scene == 'auth-certificate' && 'auth-certificate'",str(steps[-1]['with']['name']))
        origin=next(s for s in steps if s.get('name')=='Prepare only the fixed independent HTTPS auth origin')
        self.assertIn("inputs.scene == 'auth-certificate'",origin['if'])
        self.assertLess(steps.index(origin),steps.index(certificate))
        self.assertLess(steps.index(certificate),next(i for i,s in enumerate(steps) if s.get('name')=='Stop only the original owned HTTPS auth origin'))
        self.assertNotIn('key.pem',steps[-1]['with']['path'])

    def test_sync_has_own_script_and_fixed_sync_fixture_without_debug_probe(self):
        steps=self.flow()['jobs']['release-upgrade']['steps']
        sync=next(s for s in steps if 'android_release_sync.py' in s.get('run',''))
        self.assertEqual(sync['if'],"inputs.scene == 'sync'")
        self.assertNotIn('android_sync_runtime.py',sync['run'])
        origin=next(s for s in steps if s.get('name')=='Prepare only the fixed independent HTTPS auth origin')
        self.assertEqual(origin['env']['RUNTIME_SCENE'],'${{ inputs.scene }}')
        self.assertIn('if [[ "$RUNTIME_SCENE" == sync ]]',origin['run'])
        self.assertIn('prepare --fixture sync',origin['run'])
        self.assertIn("inputs.scene == 'sync' && 'sync'",str(steps[-1]['with']['name']))
        self.assertNotIn('device-private',steps[-1]['with']['path'])

    def test_auth_preparation_creates_original_hosted_parent_before_service_admission(self):
        from android_auth_hosted import HostedOrigin
        step=next(s for s in self.flow()['jobs']['release-upgrade']['steps']
                  if s.get('name')=='Prepare only the fixed independent HTTPS auth origin')
        mkdir=next(shlex.split(line) for line in step['run'].splitlines() if line.startswith('mkdir -p '))
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);private=workspace/'.build/android-runtime/owned-auth-unit'
            origin=HostedOrigin(private)
            with patch('android_auth_hosted.require_hosted'),patch.dict(os.environ,GITHUB_WORKSPACE=folder),patch('android_auth_hosted.subprocess.check_output',side_effect=RuntimeError('fixed service admission reached')) as identity,patch.object(origin,'spawn') as process,patch.object(origin,'start_container') as container:
                # Actual HostedOrigin root admission fails before any source,
                # process, Docker or network action when its parent is missing.
                with self.assertRaises(FileNotFoundError):origin.prepare()
                identity.assert_not_called();process.assert_not_called();container.assert_not_called()
                self.assertIn('.build/android-runtime',mkdir[2:])
                for relative in mkdir[2:]:
                    target=workspace/relative
                    self.assertTrue(target.resolve().is_relative_to(workspace.resolve()))
                    target.mkdir(parents=True,exist_ok=True)
                with self.assertRaisesRegex(RuntimeError,'fixed service admission reached'):origin.prepare()
                identity.assert_called_once();process.assert_not_called();container.assert_not_called()
                self.assertTrue((private/'home').is_dir());self.assertFalse((private/'owner-state.json').exists())

    def test_actual_admission_gate_rejects_branch_mismatch_dirty_source_or_unfrozen_pointer(self):
        steps=self.flow()['jobs']['fetch-inputs']['steps'];gate=next(s['run'] for s in steps if s.get('name','').startswith('Admit fixed'))
        code=gate.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        for expected,actual in [('main','a'*40),('a'*40,'b'*40)]:
            with patch.dict(os.environ,EXPECTED_COMMIT=expected,GITHUB_SHA=actual,RUNTIME_INPUT='123:99:'+'a'*64),patch('subprocess.check_output') as command:
                with self.assertRaises(AssertionError):exec(compile(code,'real-release-runtime-gate','exec'),{})
                command.assert_not_called()
        with patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40,RUNTIME_INPUT='123:99:'+'a'*64),patch('subprocess.check_output',side_effect=['a'*40,b' M tracked']):
            with self.assertRaises(AssertionError):exec(compile(code,'real-release-runtime-gate','exec'),{})
        with patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40,RUNTIME_INPUT='main'),patch('subprocess.check_output',side_effect=['a'*40,b'']):
            with self.assertRaises(ReleaseError):exec(compile(code,'real-release-runtime-gate','exec'),{})
        with patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40,RUNTIME_INPUT='123:99:'+'a'*64),patch('subprocess.check_output',side_effect=['a'*40,b'']):
            exec(compile(code,'real-release-runtime-gate','exec'),{})

    def test_driver_visibility_and_failure_evidence_do_not_require_debug_product_or_key(self):
        document=ET.parse(ROOT/'scripts/android-runtime/AndroidManifest.xml').getroot()
        ns='{http://schemas.android.com/apk/res/android}'
        self.assertIn('cc.kronecker.opennexus',[p.attrib[ns+'name'] for p in document.findall('queries/package')])
        self.assertTrue(all(p.attrib[ns+'targetPackage']=='cc.kronecker.opennexus.testdriver' for p in document.findall('instrumentation')))
        steps=self.flow()['jobs']['release-upgrade']['steps']
        stop=next(s for s in steps if s.get('name')=="Stop only this job's emulator")
        self.assertEqual(stop['if'],'always()');self.assertIn('--stop-owned',stop['run'])
        upload=next(s for s in steps if s.get('uses')=='actions/upload-artifact@v7')
        self.assertEqual(upload['if'],'always()');self.assertNotIn('driver-build',upload['with']['path'])
        self.assertIn('.build/android-release-runtime/evidence/',upload['with']['path'])
        # Native host DB/WAL/SHM originals live under vault/.ainote. The action
        # must retain those hidden paths as well as their snapshot manifest.
        self.assertEqual(upload['with']['include-hidden-files'],'true')
        self.assertEqual(upload['with']['path'].splitlines(),[
            '.build/android-release-runtime/evidence/',
            '.build/android-release-runtime/emulator.log',
            '.build/android-release-runtime/emulator-version.txt',
            '.build/android-release-runtime/cleanup.json',
            '.build/android-release-runtime/origin-proof/'])
        self.assertNotIn('.p12',str(self.flow()));self.assertNotIn('sign_android_release.py',str(self.flow()))


if __name__=='__main__':unittest.main()
