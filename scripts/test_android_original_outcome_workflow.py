"""Explicit-scope/cleanup admission only; no device, build or provider IO."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml
import android_sync_runtime as runtime
from android_product_pair_clients import CLIENTS


class OriginalOutcomeWorkflow(unittest.TestCase):
    def args(self):
        return SimpleNamespace(original_native_outcome=True, conflicts=True, cold_originals=True,
            webview_preview=True, webview_cycle=True, semantic_preview=False,
            commit='0756d1e079ac045255322759d944b7c01408b0bd', build_run_id='39064416419')

    def workflow(self):
        path=Path(__file__).resolve().parents[1]/'.github/workflows/android.yml'
        return yaml.load(path.read_text('utf-8'),Loader=yaml.BaseLoader)

    def test_normal_scopes_do_not_acquire_new_tools_and_explicit_scope_requires_all_fixed_inputs(self):
        runtime.validate_original_outcome_args(SimpleNamespace(original_native_outcome=False))
        runtime.validate_original_outcome_args(self.args())
        for key,value in [('conflicts',False),('cold_originals',False),('webview_preview',False),
                          ('webview_cycle',False),('semantic_preview',True),('commit','a'*40),('build_run_id','39054475515')]:
            args=self.args();setattr(args,key,value)
            with self.subTest(key=key),self.assertRaises(RuntimeError):runtime.validate_original_outcome_args(args)

    def test_new_fixed_client_uses_the_same_original_scope_without_admitting_the_old_unqualified_client(self):
        for client in CLIENTS:
            args=self.args();args.commit=client.source;args.build_run_id=client.build_run
            if client.original_outcome:runtime.validate_original_outcome_args(args)
            else:
                with self.assertRaises(RuntimeError):runtime.validate_original_outcome_args(args)

    def test_workflow_refuses_foreign_or_incomplete_outcome_scope_before_package_download(self):
        job=self.workflow()['jobs']['emulator-runtime'];step=next(s for s in job['steps'] if s.get('name')=='Validate the immutable reuse selection')
        code=step['run'].split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        original={'REUSE_RUN':'39064416419','REUSE_COMMIT':self.args().commit,'RUNTIME_SCOPE':'sync-conflicts',
                  'ORIGINAL_NATIVE_OUTCOME':'true','COLD_ORIGINALS':'true','WEBVIEW_PREVIEW':'true','WEBVIEW_CYCLE':'true'}
        with patch.dict(os.environ,original,clear=True):exec(compile(code,'workflow-immutable-selection','exec'),{})
        for key,value in [('RUNTIME_SCOPE','sync-initial'),('REUSE_RUN','39054475515'),('COLD_ORIGINALS','false'),
                          ('WEBVIEW_PREVIEW','false'),('WEBVIEW_CYCLE','false'),('REUSE_COMMIT','a'*40)]:
            with self.subTest(key=key),patch.dict(os.environ,dict(original,**{key:value}),clear=True),self.assertRaises(AssertionError):
                exec(compile(code,'workflow-immutable-selection','exec'),{})

    def test_crypto_overlay_is_hash_pinned_separate_and_used_only_for_this_client_step(self):
        workflow=self.workflow();job=workflow['jobs']['emulator-runtime']
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['sync_original_native_outcome']['default'],'false')
        step=next(s for s in job['steps'] if s.get('name')=='Install isolated tooling for the explicit original native outcome')
        self.assertEqual(step['if'],"inputs.runtime_scope == 'sync-conflicts' && inputs.sync_original_native_outcome")
        for part in ('android_pair_crypto_requirements.py','--target .build/android-original-outcome-crypto','--no-deps','--require-hashes','--only-binary :all:'):
            self.assertIn(part,step['run'])
        self.assertNotIn('PYTHONPATH',job.get('env',{}))
        for other in job['steps']:
            if other.get('name')=='Check actual Android UI association and original foreground Sync exchange':
                self.assertIn('inputs.sync_original_native_outcome',other['env']['PYTHONPATH'])
                self.assertIn("--original-native-outcome",other['run'])
            else:self.assertNotIn('android-original-outcome-crypto',other.get('env',{}).get('PYTHONPATH',''))

    def test_unknown_original_stop_is_not_resent_by_generic_finally_even_after_driver_failure(self):
        class DeliberateFailure(Exception):pass
        for explicit in (False,True):
            with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
                root=Path(folder);(root/'source-provenance.json').write_text('{}',encoding='utf-8')
                probe=Mock();probe.run.side_effect=DeliberateFailure()
                probe.output=root;probe.original_outcome_stage='PRIVATE_UNTRUSTED_STAGE'
                argv=['android_sync_runtime.py','--bundle',str(root),'--sdk',str(root),'--output',str(root/'not-created'),
                      '--source-checkout',str(root),'--commit',self.args().commit,'--tools-commit','a'*40,
                      '--build-run-id','39064416419','--conflicts','--cold-originals','--webview-preview','--webview-cycle']
                if explicit:argv.append('--original-native-outcome')
                stack.enter_context(patch.object(sys,'argv',argv))
                for name,value in [('require_hosted',None),('require_hosted_emulator',None),('owned_root',root),
                                   ('read_json',{'result':'ready','fixture':'sync'}),('validate_binding',(root/'unread.apk',{})),
                                   ('build_job_origin',{}),('SyncProbe',probe)]:
                    stack.enter_context(patch.object(runtime,name,return_value=value))
                stack.enter_context(patch('release_plan.build_source',return_value={}))
                with self.assertRaises(DeliberateFailure):runtime.main()
                probe.restore_test_ime.assert_called_once()
                if explicit:probe.shell.assert_not_called()
                else:probe.shell.assert_called_once_with('am','force-stop',runtime.APP,check=False)
                expected={'source-provenance.json'}
                if explicit:
                    expected.add('original-native-outcome-failure.json')
                    value=json.loads((root/'original-native-outcome-failure.json').read_text('utf-8'))
                    self.assertEqual(value['stage'],'not-entered');self.assertEqual(value['error_class'],'OtherError')
                    self.assertNotIn('PRIVATE_UNTRUSTED_STAGE',json.dumps(value))
                self.assertEqual({path.name for path in root.iterdir()},expected)


if __name__=='__main__':unittest.main()
