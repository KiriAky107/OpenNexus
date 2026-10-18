from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hashlib,json,tempfile,unittest
import android_reference_probe as subject

def state():
    return {path:dict(files=[dict(id=path,path=path,hash=hashlib.sha256(raw).hexdigest(),deleted=0)],
        drafts=[dict(id=path,content=raw.decode(),generation=1,saved_generation=1,edit_session='first')],
        saves=[dict(operation_id=path,state='committed')],outbox=[dict(operation_id=path,hash=hashlib.sha256(raw).hexdigest())],
        operations=[dict(operation_id=path,fingerprint='original')]) for path,raw in subject.originals().items()}

class ReferenceObservationBoundaries(unittest.TestCase):
    def test_equal_raw_bytes_do_not_hide_a_changed_native_hash_or_original_receipt(self):
        raw=subject.originals();base=state()
        for key in ('files','saves','outbox','operations'):
            changed=deepcopy(base);changed[subject.SOURCE_MD][key][0]['unexpected']='changed'
            with self.subTest(key=key),self.assertRaises(RuntimeError):subject.validate_frozen(raw,base,changed,raw)
        changed=deepcopy(base);changed[subject.SOURCE_MD]['files'][0]['hash']='wrong'
        with self.assertRaises(RuntimeError):subject.validate_frozen(raw,changed,changed,raw)

    def test_navigation_can_change_only_the_transient_edit_session(self):
        raw=subject.originals();base=state();changed=deepcopy(base)
        changed[subject.SOURCE_MD]['drafts'][0]['edit_session']='cold'
        subject.validate_frozen(raw,base,changed,raw)
        for key in ('content','generation','saved_generation','id'):
            bad=deepcopy(changed);bad[subject.SOURCE_MD]['drafts'][0][key]='changed'
            with self.subTest(key=key),self.assertRaises(RuntimeError):subject.validate_frozen(raw,base,bad,raw)

    def test_missing_or_deleted_target_is_not_counted_as_a_successful_read(self):
        raw=subject.originals();base=state()
        for rows in ([],[dict(base[subject.TARGET_MD]['files'][0],deleted=1)]):
            changed=deepcopy(base);changed[subject.TARGET_MD]['files']=rows
            with self.assertRaises(RuntimeError):subject.validate_frozen(raw,changed,changed,raw)

    def test_only_the_owned_unsaved_source_draft_may_change_during_draft_input(self):
        base=state();changed=deepcopy(base);draft=changed[subject.SOURCE_MD]['drafts'][0]
        draft.update(content=subject.DRAFT_BODY,generation=2)
        subject.validate_draft(base,changed)
        for mutation in ('target','save','confirmed','body'):
            bad=deepcopy(changed)
            if mutation=='target':bad[subject.TARGET_MD]['drafts'][0]['content']='other'
            elif mutation=='save':bad[subject.SOURCE_MD]['saves'].append({'operation_id':'unexpected'})
            elif mutation=='confirmed':bad[subject.SOURCE_MD]['drafts'][0]['saved_generation']=2
            else:bad[subject.SOURCE_MD]['drafts'][0]['content']='other'
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):subject.validate_draft(base,bad)

    def test_foreign_scope_or_unreviewed_snapshot_label_stops_before_private_read(self):
        for scope,label in [('lifecycle','reference-baseline'),('local-references','other-file')]:
            probe=Mock();probe.args=SimpleNamespace(scope=scope)
            with self.assertRaises(RuntimeError):subject.snapshot(probe,label)
            probe.private.assert_not_called();probe.database_rows.assert_not_called()

    def test_current_native_suite_is_not_replayed_or_impersonated(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'),patch.dict('os.environ',{'GITHUB_RUN_ID':'1'}):
            probe=Mock();probe.args=SimpleNamespace(scope='local-references',serial='emulator-5562',tools_commit='tools',commit='compiled')
            probe.output=Path(folder);probe.binding={'sha256':'fixed'};probe.keystore_test_result={'tests':5}
            with self.assertRaisesRegex(RuntimeError,'REFERENCE_MUST_NOT_REPLAY_NATIVE_SUITES'):subject.run_references(probe)
            proof=json.loads((probe.output/'local-reference-proof.json').read_bytes())
            self.assertFalse(proof['passed']);self.assertEqual(proof['executed_steps'],[])
            probe.enable_test_ime.assert_not_called();probe.device.assert_not_called();probe.phase.assert_not_called()

    def test_reviewed_driver_transfer_is_bounded_and_preserves_original_addresses(self):
        form=subject.driver_form();self.assertEqual(len(form),12);self.assertLess(len(json.dumps(form,ensure_ascii=False).encode()),8192)
        self.assertEqual(form['draft_body'],subject.DRAFT_BODY);self.assertEqual(form['scope'],'local-references')
        self.assertEqual(form['unsafe'],subject.UNSAFE);self.assertNotIn('external_url',form)

    def test_dirty_status_is_not_used_in_place_of_the_exact_durable_draft(self):
        probe=Mock();probe.database_rows.side_effect=[[('old',2,1)],[(subject.DRAFT_BODY,3,1)]]
        with patch.object(subject.time,'sleep'):subject.await_source_draft(probe)
        self.assertEqual(probe.database_rows.call_count,2);probe.phase.assert_not_called();probe.device.assert_not_called()
        probe=Mock();probe.database_rows.return_value=[(subject.DRAFT_BODY,2,2)]
        with patch.object(subject.time,'monotonic',side_effect=[0,11]),self.assertRaisesRegex(RuntimeError,'NOT_DURABLE'):
            subject.await_source_draft(probe)
        probe.device.assert_not_called()

if __name__=='__main__':unittest.main()
