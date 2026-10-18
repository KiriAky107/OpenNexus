from pathlib import Path
from unittest.mock import patch
import os,shlex,unittest,yaml


class ReleaseWorkflowBoundaries(unittest.TestCase):
    def workflow(self):
        return yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android-release.yml').read_text('utf-8'),Loader=yaml.BaseLoader)

    def test_actual_checkout_gate_rejects_mismatched_branch_ref_before_tools_or_build(self):
        flow=self.workflow();self.assertEqual(set(flow['on']),{'workflow_dispatch','workflow_call'})
        steps=flow['jobs']['release-inputs']['steps'];gate=next(s['run'] for s in steps if s.get('name')=='Admit only this fixed original source')
        code=gate.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        for expected,actual in [('a'*40,'b'*40),('not-a-commit','a'*40)]:
            with self.subTest(expected=expected),patch.dict(os.environ,EXPECTED_COMMIT=expected,GITHUB_SHA=actual),patch('subprocess.check_output') as command:
                with self.assertRaises(AssertionError):exec(compile(code,'real-release-gate','exec'),{})
                command.assert_not_called()
        with patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40),patch('subprocess.check_output',side_effect=['a'*40,b' M tracked-source']):
            with self.assertRaises(AssertionError):exec(compile(code,'real-release-gate','exec'),{})

    def test_build_has_no_debug_key_publish_or_native_debug_test_route(self):
        flow=self.workflow();self.assertEqual(flow['permissions'],{'contents':'read'})
        steps=flow['jobs']['release-inputs']['steps'];build=next(s for s in steps if s.get('name')=='Build stable identifier release APKs for both ABIs')
        args=shlex.split(build['run']);self.assertNotIn('--debug',args);self.assertIn('--split-per-abi',args)
        self.assertEqual(args[args.index('--target')+1:args.index('--split-per-abi')],['aarch64','x86_64'])
        text=str(flow);self.assertNotIn('secrets.',text);self.assertNotIn('publish_release',text);self.assertNotIn('assembleUniversalDebugAndroidTest',text)
        self.assertEqual(flow['concurrency']['cancel-in-progress'],'false')

    def test_existing_dispatch_route_calls_same_commit_and_skips_all_debug_or_runtime_jobs(self):
        from android_workflow_gate import admitted
        flow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for job in ('debug-apk','emulator-runtime','desktop-startup','desktop-pair','android-desktop-pair'):
            self.assertFalse(admitted(flow['jobs'][job]['if'],'release-inputs',reuse='',build='skipped'))
        called=flow['jobs']['release-inputs'];self.assertTrue(admitted(called['if'],'release-inputs',reuse='',build='skipped'))
        for scope in ('all','build-only','external-links','candidate-languages'):
            self.assertFalse(admitted(called['if'],scope))
        self.assertEqual(called['uses'],'./.github/workflows/android-release.yml');self.assertNotIn('secrets',called)
        self.assertEqual(called['permissions'],{'contents':'read'})
        self.assertEqual(called['with']['expected_commit'],"${{ inputs.apk_run_id == '' && inputs.apk_commit == '' && github.sha || '' }}")

if __name__=='__main__':unittest.main()
