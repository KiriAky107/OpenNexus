import os
from pathlib import Path
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
        self.assertNotIn('.p12',str(self.flow()));self.assertNotIn('sign_android_release.py',str(self.flow()))


if __name__=='__main__':unittest.main()
