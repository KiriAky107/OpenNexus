from pathlib import Path
from unittest.mock import patch
import hashlib,json,os,shlex,tempfile,unittest,yaml


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
        self.assertEqual(called['with']['product_commit'],"${{ inputs.release_product_commit }}")

    def test_product_source_is_a_full_fixed_sha_and_not_a_version_or_manifest_override(self):
        steps=self.workflow()['jobs']['release-inputs']['steps']
        gate=next(s['run'] for s in steps if s.get('name')=='Admit only this fixed original source')
        code=gate.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        for compiled in ('main','a'*39,'a'*40+' ', ''):
            with self.subTest(compiled=compiled),patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40,COMPILED_SHA=compiled),\
                    patch('subprocess.check_output',side_effect=['a'*40,b'']):
                with self.assertRaises(AssertionError):exec(compile(code,'real-product-gate','exec'),{})
        with patch.dict(os.environ,EXPECTED_COMMIT='a'*40,GITHUB_SHA='a'*40,COMPILED_SHA='b'*40),\
                patch('subprocess.check_output',side_effect=['a'*40,b'']):exec(compile(code,'real-product-gate','exec'),{})
        checkouts=[s for s in steps if s.get('uses')=='actions/checkout@v7']
        self.assertEqual(checkouts[1]['with']['ref'],"${{ inputs.product_commit || github.sha }}")
        self.assertTrue(all(s['with']['persist-credentials']=='false' for s in checkouts))

    def test_tools_are_raw_caller_blobs_outside_the_genuine_product_checkout(self):
        steps=self.workflow()['jobs']['release-inputs']['steps']
        body=next(s['run'] for s in steps if s.get('name')=='Preserve exact verifier tools outside the product checkout')
        code=body.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,RUNNER_TEMP=folder,GITHUB_SHA='a'*40,COMPILED_SHA='b'*40):
            seen=[]
            def git(args):
                self.assertEqual(args[:2],['git','show']);self.assertTrue(args[2].startswith('a'*40+':scripts/'));seen.append(args[2])
                return ('owned-tool-blob:'+args[2]).encode()
            with patch('subprocess.check_output',side_effect=git):exec(compile(code,'real-tool-snapshot','exec'),{})
            root=Path(folder)/'opennexus-release-tools';value=json.loads((root/'tools-source.json').read_bytes())
            self.assertEqual(value['tools_commit'],'a'*40);self.assertEqual(value['compiled_commit'],'b'*40);self.assertEqual(len(seen),9)
            for name,digest in value['files_sha256'].items():self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
        freeze=next(s['run'] for s in steps if s.get('name')=='Freeze source before real release compilation')
        self.assertIn('--commit "$COMPILED_SHA"',freeze);self.assertIn('--repository-dir "$GITHUB_WORKSPACE"',freeze)
        self.assertNotIn('--debug',next(s['run'] for s in steps if s.get('name')=='Build stable identifier release APKs for both ABIs'))

if __name__=='__main__':unittest.main()
