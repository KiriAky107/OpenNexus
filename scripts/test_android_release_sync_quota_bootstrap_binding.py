"""Pure raw3 correlation negatives and one new synthetic capture integration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

import android_release_sync_quota_bootstrap_binding as binder
import android_release_sync_quota_late_binding as primitive
import android_release_sync_quota_records as records
import android_release_sync_quota_snapshot as snapshots
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_bootstrap import bootstrap_task, COLLECTION, DIGEST
from test_android_release_sync_quota_precycle import Unknown
from test_android_release_sync_quota_records import fixture as records_fixture
from test_android_release_sync_quota_validation import fixture as metadata_fixture
from test_android_release_sync_quota_snapshot import retained


def seal(args):
    projection = args['projection']; capture = projection['capture_scope']; operation = projection['derived_operation_scope']
    raw_sha = hashlib.sha256(args['manifest_raw']).hexdigest()
    args['evidence']['original_manifest_raw_sha256'] = raw_sha
    args['evidence']['derivation_envelope'] = dict(schema=1, collection_id=COLLECTION,
        original_manifest_raw_sha256=raw_sha, capture_scope_typed_canonical_sha256=records.digest(capture),
        derived_operation_scope_typed_canonical_sha256=records.digest(operation),
        projection_typed_canonical_sha256=records.digest(projection), binding_upload_ids_derived_after_capture=True,
        raw_capture_scope_has_binding_upload_ids=False, independent_capture_before_quota_launch_verified=False,
        Native_or_client_ACK=False, runtime=False)
    return args


def fixture():
    data = metadata_fixture(); cycle, _, _ = records_fixture()
    operation = {k:v for k,v in data['scope'].items() if k not in ('parent_id','input_id','window')}
    capture = {k:v for k,v in operation.items() if k not in ('binding_id','upload_operation_id')}
    capture['preview_fingerprint'] = data['before']['association']['preview_fingerprint']
    projection = deepcopy(data['before']); del projection['scope']
    projection.update(schema=3, capture_scope=dict(capture), derived_operation_scope=dict(operation), stage=quota.ASSOCIATED_BOOTSTRAP)
    files = {name:None if name.endswith(('-wal','-shm')) else dict(bytes=1024, sha256='c'*64) for name in primitive.FILES}
    for name in (primitive.NOTE, primitive.SPOOL): files[name] = dict(bytes=56, sha256=quota.HASH)
    manifest = dict(schema=3, package=records.APP, identity=cycle['identity'], quota_association_capture_scope=capture,
        quota_stage=quota.ASSOCIATED_BOOTSTRAP, collection_id=COLLECTION, admission_metadata_canonical_sha256=DIGEST,
        spool_source=primitive.SPOOL_SOURCE, files=files, app_stopped_before_and_after=True,
        credential_store_copied=False, SQL_executed_on_device=False, normal_phone_permissions_verified=False,
        binding_and_upload_ids_present_at_capture=False)
    evidence = dict(original_manifest_raw_sha256='0'*64, original_files=deepcopy(files),
        query_only_new_working_copies=True, raw_DB_WAL_SHM_never_opened_by_SQLite=True, credentials_selected=False,
        completed_driver_and_stop_gate_verified=False, runtime_verified=False, collection_id=COLLECTION,
        admission_metadata_canonical_sha256=DIGEST, original_cycle_ids_present_at_capture=False,
        later_cycle_binding_verified=False, binding_upload_ids_derived_after_capture=True,
        raw_capture_scope_has_binding_upload_ids=False, derivation_envelope={})
    return seal(dict(manifest_raw=json.dumps(manifest, indent=1).encode('utf-8'), projection=projection, evidence=evidence,
        note=quota.BODY, spool=quota.BODY, parent=cycle['parent'], ready=cycle['ready'], cycle_scope=data['scope']))


class BootstrapBinding(unittest.TestCase):
    def refused_before_hash(self, args):
        with patch.object(binder.hashlib, 'sha256', side_effect=AssertionError('Unapproved input hashed')):
            with self.assertRaisesRegex(ValueError, '^SIGNED_QUOTA_BOOTSTRAP_BINDING_UNAVAILABLE$'):
                binder.bind_bootstrap_capture(**args)

    def manifest_change(self, args, change):
        value = json.loads(args['manifest_raw']); change(value)
        args['manifest_raw'] = json.dumps(value).encode('utf-8')

    def test_valid_raw3_and_typed_hashes_are_distinct_correlation_only(self):
        args = fixture(); original = deepcopy(args); result = binder.bind_bootstrap_capture(**args)
        self.assertEqual(args, original); self.assertEqual(result['before'], metadata_fixture()['before'])
        bound = result['binding']; self.assertEqual(bound['source_manifest_schema'], 3)
        self.assertEqual(bound['original_bootstrap_manifest_raw_sha256'], hashlib.sha256(args['manifest_raw']).hexdigest())
        self.assertNotEqual(bound['original_bootstrap_manifest_raw_sha256'], records.digest(json.loads(args['manifest_raw'])))
        self.assertEqual(bound['bootstrap_projection_metadata_canonical_sha256'], records.digest(args['projection']))
        self.assertEqual(bound['bootstrap_derivation_metadata_canonical_sha256'], records.digest(args['evidence']['derivation_envelope']))
        self.assertEqual(bound['quota_parent_metadata_canonical_sha256'], records.digest(args['parent']))
        self.assertEqual(bound['quota_ready_metadata_canonical_sha256'], records.digest(args['ready']))
        for key in ('raw_capture_scope_has_binding_upload_ids','original_cycle_ids_present_at_capture',
                    'independent_raw_parent_ready_verified','Native_or_client_ACK','runtime_verified'):
            self.assertIs(bound[key], False)
        self.assertIs(result['captured_before_quota_launch_independently_verified'], False)
        self.assertIs(result['original_files_independently_verified'], False)
        self.assertIs(result['typed_correlation_only'], True)
        self.assertLessEqual(len(records.encoded(result)), records.MAX_BUNDLE)

    def test_new_actual_raw3_capture_feeds_RAM_without_a_second_read_or_action(self):
        with bootstrap_task() as (root, _, _, invoke, capture, operation, selected, _, _, _):
            output = invoke(); value = snapshots.project_associated_bootstrap(output, root/'working', capture_scope=capture, selected=selected)
            raw = (output/'snapshot.json').read_bytes(); original = retained(output); cycle, _, _ = records_fixture()
            scope = dict(operation, parent_id=cycle['parent']['parent_intent_id'], input_id=cycle['parent']['input_intent_id'], window=cycle['ready']['window'])
            with patch('builtins.open', side_effect=AssertionError('File IO during binder')), \
                 patch.object(Path, 'open', side_effect=AssertionError('Path IO during binder')), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError('SQL during binder')), \
                 patch.object(time, 'monotonic', side_effect=AssertionError('Clock during binder')), \
                 patch.object(primitive, 'bind_associated_capture', side_effect=AssertionError('Raw2 binder used')):
                result = binder.bind_bootstrap_capture(raw, value.projection, value.evidence, value.note, value.spool,
                    parent=cycle['parent'], ready=cycle['ready'], cycle_scope=scope)
            self.assertEqual(result['before']['scope'], scope)
            self.assertEqual(result['before']['outbox'], value.projection['outbox'])
            self.assertEqual(retained(output), original); self.assertIs(result['runtime_verified'], False)

    def test_nonutf8_partial_duplicate_nonfinite_and_raw2_profile_fail_before_hash(self):
        for kind in ('utf8','partial','duplicate','nonfinite','raw2'):
            args = fixture(); raw = args['manifest_raw']
            if kind == 'utf8': raw = b'\xff'
            if kind == 'partial': raw = raw[:-1]
            if kind == 'duplicate': raw = raw.replace(b'"schema": 3', b'"schema": 3, "schema": 3', 1)
            if kind == 'nonfinite': raw = raw.replace(b'"schema": 3', b'"schema": NaN', 1)
            if kind == 'raw2': raw = raw.replace(b'"schema": 3', b'"schema": 2', 1)
            args['manifest_raw'] = raw; self.refused_before_hash(args)

    def test_exact_bytes_without_unknown_or_buffer_coercion(self):
        for key, value in [('manifest_raw', Unknown()), ('manifest_raw', bytearray(b'{}')),
                           ('note', Unknown()), ('spool', bytearray(quota.BODY)), ('note', b'x'*56)]:
            args = fixture(); args[key] = value; self.refused_before_hash(args)

    def test_private_extras_in_all_supplied_metadata_fail_before_hash(self):
        for where in ('manifest','projection','evidence','envelope','parent','ready','cycle_scope'):
            args = fixture()
            if where == 'manifest': self.manifest_change(args, lambda x:x.update(private_UI='synthetic-private'))
            elif where == 'envelope': args['evidence']['derivation_envelope']['private_UI'] = Unknown()
            else: args[where]['private_UI'] = Unknown()
            self.refused_before_hash(args)

    def test_closed_flags_bool_int_and_capture_presence_fail_before_hash(self):
        for change in [lambda x:x.update(schema=True), lambda x:x.update(binding_and_upload_ids_present_at_capture=0),
                       lambda x:x.update(credential_store_copied=True), lambda x:x['quota_association_capture_scope'].update(binding_id=None)]:
            args = fixture(); self.manifest_change(args, change); self.refused_before_hash(args)
        for key in ('runtime','Native_or_client_ACK','raw_capture_scope_has_binding_upload_ids',
                    'independent_capture_before_quota_launch_verified','binding_upload_ids_derived_after_capture'):
            args = fixture(); args['evidence']['derivation_envelope'][key] = 0; self.refused_before_hash(args)

    def test_file_whitelist_limits_nullability_and_exact56B(self):
        changes = [lambda x:x['files'].update(private=None), lambda x:x['files'].update({primitive.NOTE:None}),
                   lambda x:x['files'][primitive.NOTE].update(bytes=True),
                   lambda x:x['files'][primitive.SPOOL].update(bytes=55),
                   lambda x:x['files'][primitive.DATABASES[0]].update(bytes=primitive.MAX_FILE+1)]
        for change in changes:
            args = fixture(); self.manifest_change(args, change); self.refused_before_hash(args)

    def test_wrong_base_owner_and_derived_collection_collision_fail_before_hash(self):
        for kind in ('uid','base','collection','capture'):
            args = fixture()
            if kind == 'uid': args['cycle_scope']['uid'] = True
            if kind == 'base': args['cycle_scope']['binding_id'] = '00000000-0000-0000-0000-000000009299'
            if kind == 'collection': args['projection']['derived_operation_scope']['upload_operation_id'] = COLLECTION
            if kind == 'capture': args['projection']['capture_scope']['save_operation_id'] = COLLECTION
            self.refused_before_hash(args)

    def test_unknown_partial_envelope_and_hash_shapes_fail_before_hash(self):
        for key in ('original_manifest_raw_sha256','capture_scope_typed_canonical_sha256',
                    'derived_operation_scope_typed_canonical_sha256','projection_typed_canonical_sha256'):
            args = fixture(); args['evidence']['derivation_envelope'][key] = Unknown(); self.refused_before_hash(args)
        args = fixture(); del args['evidence']['derivation_envelope']['runtime']; self.refused_before_hash(args)

    def test_closed_wrong_raw_digest_hashes_only_already_approved_buffer(self):
        args = fixture(); args['evidence']['original_manifest_raw_sha256'] = '0'*64
        args['evidence']['derivation_envelope']['original_manifest_raw_sha256'] = '0'*64
        real = hashlib.sha256
        with patch.object(binder.hashlib, 'sha256', wraps=real) as hashed:
            with self.assertRaises(ValueError): binder.bind_bootstrap_capture(**args)
        self.assertEqual(hashed.call_count, 1); self.assertEqual(hashed.call_args.args, (args['manifest_raw'],))

    def test_closed_wrong_typed_digest_refuses_without_repair(self):
        for key in ('capture_scope_typed_canonical_sha256','derived_operation_scope_typed_canonical_sha256','projection_typed_canonical_sha256'):
            args = fixture(); args['evidence']['derivation_envelope'][key] = '0'*64
            with self.assertRaisesRegex(ValueError, '^SIGNED_QUOTA_BOOTSTRAP_BINDING_UNAVAILABLE$'):
                binder.bind_bootstrap_capture(**args)

    def test_parent_ready_geometry_Long_and_bool_boundaries(self):
        for key, value in [('window', True), ('elapsed_ms', records.MAX-35999), ('original', [0,0,0,0]), ('x', True)]:
            args = fixture(); args['ready'][key] = value; self.refused_before_hash(args)
        args = fixture(); args['ready']['elapsed_ms'] = records.MAX-36000
        self.assertFalse(binder.bind_bootstrap_capture(**args)['runtime_verified'])

    def test_parent_input_window_and_known_draft_id_mismatch_fail_before_hash(self):
        for kind in ('parent','input','window','draft'):
            args = fixture()
            if kind == 'parent': args['parent']['parent_intent_id'] = COLLECTION
            if kind == 'input': args['ready']['driver_intent_id'] = COLLECTION
            if kind == 'window': args['cycle_scope']['window'] += 1
            if kind == 'draft':
                identifier = args['projection']['draft']['id']
                args['parent']['parent_intent_id'] = identifier; args['ready']['parent_intent_id'] = identifier
                args['cycle_scope']['parent_id'] = identifier
            self.refused_before_hash(args)

    def test_SaveNULL_outbox_and_hidden_attempt_rows_fail_before_hash(self):
        for kind in ('save','outbox','attempt'):
            args = fixture()
            if kind == 'save': args['projection']['save']['file_id'] = args['projection']['file']['id']
            if kind == 'outbox': args['projection']['outbox'][1]['state'] = 'queued'
            if kind == 'attempt': args['projection']['attempts'] = [dict(private_UI=Unknown())]
            self.refused_before_hash(args)

    def test_outputs_and_caller_lists_have_no_aliases(self):
        args = fixture(); result = binder.bind_bootstrap_capture(**args)
        args['projection']['outbox'][0]['state'] = 'changed'; args['ready']['visible'][0] = -1
        self.assertEqual(result['before']['outbox'][0]['state'], 'archived')
        result['before']['scope']['uid'] = 1
        self.assertEqual(result['binding']['full_cycle_scope']['uid'], 10216)
        result['binding']['full_cycle_scope']['uid'] = 2
        self.assertEqual(args['cycle_scope']['uid'], 10216)

    def test_raw_manifest_16384_accepts_and_16385_rejects_before_hash(self):
        args = fixture(); args['manifest_raw'] += b' '*(primitive.MAX_MANIFEST-len(args['manifest_raw'])); seal(args)
        self.assertEqual(len(args['manifest_raw']), 16384)
        self.assertEqual(binder.bind_bootstrap_capture(**args)['binding']['source_manifest_schema'], 3)
        args['manifest_raw'] += b' '; self.refused_before_hash(args)


if __name__ == '__main__': unittest.main()
