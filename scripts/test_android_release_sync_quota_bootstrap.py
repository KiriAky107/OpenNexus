"""Synthetic raw3 capture/readonly derivation; no real associated stop gate."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

import android_release_sync_quota_collect as collect
import android_release_sync_quota_snapshot as snapshots
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_collect import task
from test_android_release_sync_quota_precycle import Unknown
from test_android_release_sync_quota_snapshot import retained, mutate

COLLECTION = '00000000-0000-0000-0000-000000009200'
DIGEST = 'b'*64


class BootstrapFixtureAdmission(collect.OriginalQuotaBootstrapAdmission):
    """Controlled dependency, never original-driver/owned-stop verification."""
    def __init__(self, scope, identity):
        self.scope, self.identity = dict(scope), dict(identity)
        self.calls = 0; self.result = DIGEST; self.error = None

    def verify(self, *, capture_scope, identity, collection_id):
        self.calls += 1
        assert capture_scope == self.scope and identity == self.identity and collection_id == COLLECTION
        if self.error is not None:
            raise self.error
        capture_scope['uid'] = 1; identity['boot_id'] = 'changed-copy'
        return self.result


@contextmanager
def bootstrap_task(*, wal=False):
    # Fixture functions only; no old test method or retained runtime originals.
    with task('associated', wal=wal) as (root, device, legacy_gate, legacy, full, selected, old):
        operation = {k:v for k,v in full.items() if k not in ('parent_id','input_id','window')}
        capture = {k:v for k,v in operation.items() if k not in ('binding_id','upload_operation_id')}
        capture['preview_fingerprint'] = selected['preview_fingerprint']
        gate = BootstrapFixtureAdmission(capture, device.identity)
        expected = deepcopy(old); del expected['scope']
        expected.update(schema=3, stage=quota.ASSOCIATED_BOOTSTRAP,
                        capture_scope=dict(capture), derived_operation_scope=dict(operation))
        def invoke(**changes):
            args = dict(capture_scope=capture, identity=device.identity, collection_id=COLLECTION, admission=gate)
            args.update(changes)
            return collect.collect_associated_bootstrap(device, 'emulator-5562', root/'tools', root/'artifact', **args)
        yield root, device, gate, invoke, capture, operation, selected, expected, legacy, legacy_gate


class BootstrapAssociated(unittest.TestCase):
    def project(self, root, output, capture, selected, name='working'):
        return snapshots.project_associated_bootstrap(output, root/name, capture_scope=capture, selected=selected)

    def test_actual_writer_then_new_readonly_copies_derive_both_native_ids(self):
        with bootstrap_task() as (root, device, gate, invoke, capture, operation, selected, expected, _, _):
            output = invoke(); raw = (output/'snapshot.json').read_bytes(); manifest = json.loads(raw)
            original = retained(output)
            self.assertEqual(gate.calls, 1); self.assertEqual(device.stops, 3)
            self.assertEqual(manifest['schema'], 3)
            self.assertEqual(manifest['quota_association_capture_scope'], capture)
            self.assertIs(manifest['binding_and_upload_ids_present_at_capture'], False)
            self.assertTrue({'binding_id','upload_operation_id','parent_id','input_id','window'}.isdisjoint(capture))
            connect = sqlite3.connect; paths = []
            def tracked(path, *args, **kwargs):
                self.assertTrue(path.startswith((root/'working').as_uri()+'/') and path.endswith('?mode=ro'))
                self.assertNotIn(output.as_uri(), path); paths.append(path)
                return connect(path, *args, **kwargs)
            with patch.object(snapshots.sqlite3, 'connect', side_effect=tracked), \
                 patch.object(quota, '_before', side_effect=AssertionError('fake full quota scope')), \
                 patch.object(quota, 'before_associated_capture', side_effect=AssertionError('fake raw2 profile')):
                result = self.project(root, output, capture, selected)
            self.assertEqual(len(paths), 2); self.assertEqual(result.projection, expected)
            self.assertEqual(result.projection['derived_operation_scope'], operation)
            self.assertIsNone(result.projection['save']['file_id'])
            self.assertEqual([x['state'] for x in result.projection['outbox']], ['archived','pending'])
            self.assertEqual(result.note, quota.BODY); self.assertEqual(result.spool, quota.BODY)
            self.assertEqual(result.evidence['original_manifest_raw_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(retained(output), original); self.assertFalse(list((root/'artifact').iterdir()))
            self.assertNotIn(selected['endpoint'], json.dumps(result.projection))
            self.assertEqual(len(device.heads), len(set(device.heads)))

    def test_WAL_inputs_raw_manifest_and_typed_derivation_are_separate(self):
        with bootstrap_task(wal=True) as (root, _, _, invoke, capture, _, selected, _, _, _):
            output = invoke(); original = retained(output)
            self.assertTrue(all((output/(name+'-wal')).exists() for name in snapshots.DATABASES))
            result = self.project(root, output, capture, selected); evidence = result.evidence
            envelope = evidence['derivation_envelope']
            self.assertNotEqual(envelope['original_manifest_raw_sha256'], envelope['projection_typed_canonical_sha256'])
            self.assertEqual(envelope['collection_id'], COLLECTION)
            self.assertIs(envelope['binding_upload_ids_derived_after_capture'], True)
            for key in ('raw_capture_scope_has_binding_upload_ids','independent_capture_before_quota_launch_verified',
                        'Native_or_client_ACK','runtime'):
                self.assertIs(envelope[key], False)
            for key in ('completed_driver_and_stop_gate_verified','runtime_verified','credentials_selected',
                        'original_cycle_ids_present_at_capture','later_cycle_binding_verified'):
                self.assertIs(evidence[key], False)
            self.assertEqual(retained(output), original)

    def test_capture_scope_rejects_future_derived_private_partial_and_bool_before_gate(self):
        with bootstrap_task() as (_, device, gate, invoke, capture, operation, _, _, _, _):
            bads = [operation, dict(capture, binding_id=None), dict(capture, upload_operation_id=None),
                    dict(capture, parent_id=None), dict(capture, input_id=None), dict(capture, window=None),
                    dict(capture, private=Unknown()), dict(capture, uid=True), dict(capture, api=True),
                    dict(capture, preview_fingerprint=Unknown()), dict(capture, owner_run=Unknown()),
                    {k:v for k,v in capture.items() if k != 'save_operation_id'}]
            for bad in bads:
                with self.assertRaises((RuntimeError, ValueError)): invoke(capture_scope=bad)
            self.assertEqual(gate.calls, 0); self.assertEqual(device.calls, [])

    def test_required_bootstrap_gate_refuses_legacy_boolean_and_unknown_digest(self):
        with self.assertRaises(TypeError): collect.OriginalQuotaBootstrapAdmission()
        with bootstrap_task() as (_, device, gate, invoke, _, _, _, _, _, legacy_gate):
            for bad in (None, True, dict(verified=True), legacy_gate):
                with self.assertRaises(RuntimeError): invoke(admission=bad)
            for bad in (None, True, Unknown(), 'b'*63):
                gate.result = bad
                with self.assertRaises((RuntimeError, ValueError)): invoke()
            self.assertEqual(device.calls, [])

    def test_gate_exception_and_collection_collisions_do_not_read_device(self):
        with bootstrap_task() as (_, device, gate, invoke, capture, _, _, _, _, _):
            for bad in (capture['boot_id'], capture['association_operation_id'], Unknown()):
                with self.assertRaises((RuntimeError, ValueError)): invoke(collection_id=bad)
            self.assertEqual(gate.calls, 0)
            error = OSError('synthetic gate unavailable'); gate.error = error
            with self.assertRaises(OSError) as caught: invoke()
            self.assertIs(caught.exception, error); self.assertEqual(device.calls, [])

    def test_manifest_private_duplicate_partial_false_presence_and_profile_reject_before_SQL(self):
        for kind in ('private','duplicate','partial','schema','presence','derived','collection','digest','stage'):
            with self.subTest(kind=kind), bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
                output = invoke(); path = output/'snapshot.json'; value = json.loads(path.read_bytes())
                if kind == 'private': value['private_UI'] = 'synthetic-private'
                if kind == 'schema': value['schema'] = True
                if kind == 'presence': value['binding_and_upload_ids_present_at_capture'] = 0
                if kind == 'derived': value['quota_association_capture_scope']['binding_id'] = None
                if kind == 'collection': value['collection_id'] = capture['save_operation_id']
                if kind == 'digest': value['admission_metadata_canonical_sha256'] = None
                if kind == 'stage': value['quota_stage'] = quota.ASSOCIATED_BEFORE_CYCLE
                raw = json.dumps(value)
                if kind == 'duplicate': raw = raw.replace('"schema": 3', '"schema": 3, "schema": 3')
                if kind == 'partial': raw = raw[:-1]
                path.write_text(raw, encoding='utf-8'); original = retained(output)
                with patch.object(snapshots.sqlite3, 'connect', side_effect=AssertionError('SQL before manifest admission')):
                    with self.assertRaises(RuntimeError): self.project(root, output, capture, selected)
                self.assertFalse((root/'working').exists()); self.assertEqual(retained(output), original)

    def test_raw3_is_not_accepted_as_raw1_or_raw2_and_never_rewritten(self):
        with bootstrap_task() as (root, _, _, invoke, capture, operation, selected, _, _, _):
            output = invoke(); original = retained(output)
            full = dict(operation, parent_id='00000000-0000-0000-0000-000000009201',
                        input_id='00000000-0000-0000-0000-000000009202', window=41)
            with self.assertRaises(RuntimeError):
                snapshots.project_quota(output, root/'working', scope=full, selected=selected, stage='associated')
            with self.assertRaises(RuntimeError):
                snapshots.project_associated_before_cycle(output, root/'working', association_scope=operation, selected=selected)
            self.assertFalse((root/'working').exists()); self.assertEqual(retained(output), original)
        with bootstrap_task() as (root, _, _, _, capture, _, selected, _, legacy, _):
            output = legacy(); original = retained(output)
            with self.assertRaises(RuntimeError): self.project(root, output, capture, selected)
            self.assertFalse((root/'working').exists()); self.assertEqual(retained(output), original)

    def test_derived_ids_must_match_receipt_and_be_distinct_original_uuids(self):
        cases = ["UPDATE sync_bindings SET id='00000000-0000-0000-0000-000000009299'",
                 "UPDATE outbox SET operation_id='not-an-original-id' WHERE state='pending'",
                 "UPDATE outbox SET state='acked' WHERE state='pending'",
                 "UPDATE outbox SET state='pending' WHERE state='archived'",
                 "UPDATE outbox SET operation_id='"+COLLECTION+"' WHERE state='pending'"]
        for sql in cases:
            with self.subTest(sql=sql), bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
                output = invoke(); mutate(output, 'outbox', sql); original = retained(output)
                with self.assertRaises(RuntimeError): self.project(root, output, capture, selected)
                self.assertEqual(retained(output), original)

    def test_SaveNULL_archive_and_no_attempt_advance_remain_required(self):
        cases = [("UPDATE save_intents SET file_id='replacement'", True),
                 ("UPDATE outbox SET state='acked' WHERE state='archived'", False),
                 ("INSERT INTO sync_attempts (binding,operation_id,attempts,outcome,error) VALUES ('binding','operation',1,'failed','QUOTA_EXCEEDED')", False)]
        for sql, draft in cases:
            with self.subTest(draft=draft), bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
                output = invoke(); mutate(output, 'outbox', sql, draft=draft); original = retained(output)
                with self.assertRaises(RuntimeError): self.project(root, output, capture, selected)
                self.assertEqual(retained(output), original)

    def test_selected_preview_mismatch_fails_before_any_SQL_copy(self):
        with bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
            output = invoke(); bad = dict(selected, preview_fingerprint='f'*64)
            with patch.object(snapshots.sqlite3, 'connect', side_effect=AssertionError('SQL before preview admission')):
                with self.assertRaises(RuntimeError): self.project(root, output, capture, bad)
            self.assertFalse((root/'working').exists())

    def test_private_duplicate_original_request_and_wrong_original_association_refuse(self):
        for kind in ('private','duplicate','association'):
            with self.subTest(kind=kind), bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
                output = invoke()
                from contextlib import closing
                with closing(sqlite3.connect(output/snapshots.DATABASES[1])) as db:
                    raw = db.execute('SELECT request FROM mobile_sync_binding_intents').fetchone()[0]
                    if kind == 'private':
                        value = json.loads(raw); value['private_UI'] = 'synthetic-private'; raw = json.dumps(value)
                    elif kind == 'duplicate': raw = raw[:-1]+',"operation_id":"'+capture['association_operation_id']+'"}'
                    else:
                        value = json.loads(raw); value['operation_id'] = COLLECTION; raw = json.dumps(value)
                    db.execute('UPDATE mobile_sync_binding_intents SET request=?', (raw,)); db.commit()
                from test_android_release_sync_quota_snapshot import refresh
                refresh(output); original = retained(output)
                with self.assertRaises(RuntimeError): self.project(root, output, capture, selected)
                self.assertEqual(retained(output), original)

    def test_note_spool_and_live_process_reject_without_manifest(self):
        for kind in ('note','spool','running'):
            with self.subTest(kind=kind), bootstrap_task() as (root, device, _, invoke, _, _, _, _, _, _):
                if kind == 'note': device.files[snapshots.NOTE] = quota.BODY+b' '
                if kind == 'spool': device.files[snapshots.SPOOL_SOURCE] = b'x'*56
                if kind == 'running': device.running_at = 2
                with self.assertRaises(RuntimeError): invoke()
                self.assertFalse((root/'tools'/('quota-original-'+quota.ASSOCIATED_BOOTSTRAP)/'snapshot/snapshot.json').exists())

    def test_validated_rows_and_scope_outputs_have_no_caller_aliases(self):
        with bootstrap_task() as (_, _, _, _, capture, operation, _, expected, _, _):
            result = quota.before_associated_bootstrap(expected, capture, operation)
            expected['outbox'][0]['state'] = 'changed'; capture['uid'] = 1; operation['uid'] = 1
            self.assertEqual(result['outbox'][0]['state'], 'archived')
            self.assertEqual(result['capture_scope']['uid'], 10216)
            self.assertEqual(result['derived_operation_scope']['uid'], 10216)
            for key in ('schema','jobs','save','association'):
                bad = deepcopy(result); bad[key] = Unknown()
                with self.assertRaises(ValueError):
                    quota.before_associated_bootstrap(bad, result['capture_scope'], result['derived_operation_scope'])

    def test_derived_operation_base_rejects_known_id_and_collection_aliases(self):
        with bootstrap_task() as (_, _, _, _, capture, operation, _, _, _, _):
            for binding, upload in ((capture['boot_id'], operation['upload_operation_id']),
                                    (operation['binding_id'], capture['save_operation_id']),
                                    (operation['binding_id'], operation['binding_id']),
                                    (COLLECTION, operation['upload_operation_id']),
                                    (Unknown(), operation['upload_operation_id'])):
                with self.assertRaises(ValueError): quota.derived_association_scope(capture, binding, upload, COLLECTION)

    def test_partial_existing_capture_is_not_overwritten_and_has_no_second_device_read(self):
        with bootstrap_task() as (_, device, _, invoke, _, _, _, _, _, _):
            output = invoke(); original = retained(output); count = len(device.calls)
            with self.assertRaisesRegex(RuntimeError, '^RELEASE_OBSERVATION_ORIGINAL_OUTPUT_EXISTS$'): invoke()
            self.assertEqual(len(device.calls), count); self.assertEqual(retained(output), original)

    def test_linked_original_and_overlap_working_copy_are_refused(self):
        for kind in ('link','overlap'):
            with self.subTest(kind=kind), bootstrap_task() as (root, _, _, invoke, capture, _, selected, _, _, _):
                output = invoke()
                if kind == 'link': os.link(output/snapshots.NOTE, root/'original-hardlink.md')
                working = output/'working' if kind == 'overlap' else root/'working'
                with self.assertRaises(RuntimeError):
                    snapshots.project_associated_bootstrap(output, working, capture_scope=capture, selected=selected)
                self.assertFalse(working.exists())


if __name__ == '__main__': unittest.main()
