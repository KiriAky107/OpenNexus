import json,unittest
from copy import deepcopy
import android_reference_contract as subject

class LocalReferenceContracts(unittest.TestCase):
    def test_targets_sources_and_synthetic_canvas_extensions_are_declared_separately(self):
        raw=subject.originals();self.assertEqual(set(raw),set(subject.PATHS));self.assertEqual(len(raw),4)
        source=json.loads(raw[subject.SOURCE_CANVAS]);target=json.loads(raw[subject.TARGET_CANVAS])
        self.assertEqual([v['file'] for v in source['nodes']],[subject.TARGET_MD,subject.TARGET_CANVAS])
        self.assertEqual(target['nodes'][0]['text'],subject.TARGET_CANVAS_TEXT)
        self.assertTrue(all('vendor' in n for v in (source,target) for n in v['nodes']))
        for path in (subject.SOURCE_CANVAS,subject.TARGET_CANVAS):
            self.assertIn(b'\r\n',raw[path]);self.assertNotIn(b'\n',raw[path].replace(b'\r\n',b''))

    def test_draft_is_distinct_from_saved_bytes_and_keeps_all_original_review_addresses(self):
        self.assertNotEqual(subject.DRAFT_BODY,subject.SOURCE_BODY)
        self.assertTrue(subject.DRAFT_BODY.startswith(subject.SOURCE_BODY))
        for value in (subject.TARGET_MD,subject.TARGET_CANVAS,subject.MISSING,subject.UNSAFE):
            self.assertIn(value,subject.SOURCE_BODY);self.assertIn(value,subject.DRAFT_BODY)

    def test_observation_refuses_missing_extra_or_reencoded_originals(self):
        raw=subject.originals();subject.same_originals(raw,deepcopy(raw))
        for mode in ('missing','extra','line-ending','content'):
            other=deepcopy(raw)
            if mode=='missing':other.pop(subject.TARGET_MD)
            elif mode=='extra':other['other.md']=b'unrelated'
            elif mode=='line-ending':other[subject.SOURCE_CANVAS]=other[subject.SOURCE_CANVAS].replace(b'\r\n',b'\n')
            else:other[subject.TARGET_MD]+=b'changed'
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):subject.same_originals(raw,other)

    def test_native_projection_excludes_only_transient_edit_session(self):
        value={p:{'files':[{'id':p,'hash':'original'}],'drafts':[{'generation':2,'content':'original draft','edit_session':'first'}],
            'saves':[],'outbox':[],'operations':[]} for p in subject.PATHS}
        second=deepcopy(value);second[subject.SOURCE_MD]['drafts'][0]['edit_session']='new'
        self.assertEqual(subject.stable_state(value),subject.stable_state(second))
        for key in ('content','generation'):
            changed=deepcopy(second);changed[subject.SOURCE_MD]['drafts'][0][key]='unexpected'
            self.assertNotEqual(subject.stable_state(value),subject.stable_state(changed))
        self.assertEqual(value[subject.SOURCE_MD]['drafts'][0]['edit_session'],'first')
        with self.assertRaises(RuntimeError):subject.stable_state({})

    def test_scope_has_four_minimal_seeds_but_no_save_after_review_or_foreign_browser_action(self):
        self.assertEqual(len([v for v in subject.STEPS if v.endswith('-seed')]),4)
        self.assertEqual(subject.STEPS[-1],'reference-cold')
        self.assertNotIn('reference-external',subject.STEPS)
        self.assertIn('reference-md-cancel',subject.STEPS);self.assertIn('reference-md-return',subject.STEPS)
        self.assertIn('reference-missing',subject.STEPS);self.assertIn('reference-unsupported',subject.STEPS)

if __name__=='__main__':unittest.main()
