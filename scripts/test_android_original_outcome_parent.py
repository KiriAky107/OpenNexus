"""Original data comparison boundaries; no native/provider writes or UI calls."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from android_desktop_handoff import HandoffError
from android_sync_conflict import COPY, DIRTY, IDS, LOCAL, REMOTE
from android_original_outcome_parent import CONCURRENT, check_frozen, completed_decision, sha, unchanged_pending
from test_android_original_outcome_status import NONCE


class NativeComparison(unittest.TestCase):
    def frozen(self):
        return {'nonce': NONCE, 'sequence': 2, 'choice': 'copy', 'destination': COPY, 'fingerprint': 'd'*64,
                'expected_hash_from_parent_native_observation': sha(CONCURRENT), 'expected_hash_displayed_by_dialog': False,
                'native_operation_ids_not_assigned_by_driver': True}

    def native(self):
        return {'binding': {'cursor': 2}, 'heads': [{'file_id': 'own-file', 'revision': 2, 'path': 'runtime-note.md', 'hash': sha(REMOTE)}],
                'conflicts': [{'state': 'resolved'}], 'decisions': [{'sequence': 2, 'choice': 'copy', 'destination': COPY,
                'expected_hash': sha(CONCURRENT), 'fingerprint': 'd'*64, 'state': 'completed',
                **{key: '00000000-0000-4000-8000-'+str(100+i).zfill(12) for i,key in enumerate(IDS)}}]}

    def test_new_review_is_bound_to_parent_native_hash_without_claiming_hash_display_or_issuing_IDs(self):
        self.assertEqual(check_frozen(self.frozen(), NONCE, sha(CONCURRENT)), 'd'*64)
        for changes in ({'expected_hash_displayed_by_dialog': True}, {'expected_hash_from_parent_native_observation': sha(LOCAL)},
                        {'native_operation_ids_not_assigned_by_driver': False}, {'operation_id': 'new-made-up-id'},
                        {'destination': '../outside.md'}):
            with self.assertRaises(HandoffError): check_frozen(dict(self.frozen(), **changes), NONCE, sha(CONCURRENT))

    def test_completed_native_receipt_requires_actual_current_source_hash_fingerprint_and_five_original_IDs(self):
        receipt = {'file_id': 'own-file', 'path': 'runtime-note.md', 'hash': sha(REMOTE)}
        before = self.native(); original = deepcopy(before)
        self.assertEqual(completed_decision(before, self.frozen(), receipt), before['decisions'][0])
        self.assertEqual(before, original)
        for key, value in [('expected_hash', sha(LOCAL)), ('fingerprint', 'e'*64), ('state', 'pending'),
                           ('copy_id', before['decisions'][0]['operation_id']), ('operation_id', 'invalid')]:
            changed = self.native(); changed['decisions'][0][key] = value
            with self.assertRaisesRegex(HandoffError, 'NATIVE_DECISION_CHANGED'):
                completed_decision(changed, self.frozen(), receipt)

    def test_wrong_head_conflict_boundary_or_extra_native_decision_is_not_original_completion(self):
        receipt = {'file_id': 'own-file', 'path': 'runtime-note.md', 'hash': sha(REMOTE)}
        for change in (lambda n: n['binding'].update(cursor=3), lambda n: n['heads'][0].update(hash='e'*64),
                       lambda n: n['conflicts'][0].update(state='open'), lambda n: n['decisions'].append(dict(n['decisions'][0]))):
            changed = self.native(); change(changed)
            with self.assertRaises(HandoffError): completed_decision(changed, self.frozen(), receipt)

    def test_known_rejection_requires_zero_native_decisions_and_unchanged_actual_provider_source_draft(self):
        native = {'binding': {'cursor': 2}, 'decisions': []}
        provider = {'vault': {'sequence': 2}, 'rows': {'revisions': [1, 2]}, 'delivery': {'revision': 0}}
        rows = [('runtime-note.md', DIRTY.decode(), 4, 3)]
        probe = SimpleNamespace(host_snapshot=Mock(return_value=deepcopy(native)), observe=Mock(return_value=deepcopy(provider)),
                                original=Mock(return_value=CONCURRENT), draft_rows=Mock(return_value=deepcopy(rows)))
        unchanged_pending(probe, 'own', native, provider, CONCURRENT, rows)
        cases = [('host_snapshot', dict(native, decisions=[{'operation_id':'not-allowed'}])),
                 ('observe', dict(provider, vault={'sequence':3})), ('original', REMOTE),
                 ('draft_rows', [('runtime-note.md', DIRTY.decode(), 5, 3)])]
        for name, changed in cases:
            before = getattr(probe, name).return_value; getattr(probe, name).return_value = changed
            with self.assertRaises(HandoffError): unchanged_pending(probe, 'own', native, provider, CONCURRENT, rows)
            getattr(probe, name).return_value = before


if __name__ == '__main__': unittest.main()
