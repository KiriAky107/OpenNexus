"""New pure late-binding cases, plus one freshly generated capture integration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

import android_release_sync_quota_late_binding as binder
import android_release_sync_quota_records as records
import android_release_sync_quota_snapshot as snapshots
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_records import fixture as records_fixture
from test_android_release_sync_quota_validation import fixture as metadata_fixture
from test_android_release_sync_quota_precycle import precycle_task, COLLECTION, DIGEST
from test_android_release_sync_quota_snapshot import retained


class Unknown:
    def __str__(self):raise AssertionError('Unknown formatted')
    def __repr__(self):raise AssertionError('Unknown represented')
    def __eq__(self,other):raise AssertionError('Unknown compared')
    def __hash__(self):raise AssertionError('Unknown hashed')


def fixture():
    data = metadata_fixture();cycle,_,_ = records_fixture()
    base = {k:v for k,v in data['scope'].items() if k not in ('parent_id','input_id','window')}
    projection = deepcopy(data['before']);del projection['scope']
    projection.update(schema=2,association_scope=dict(base),stage=quota.ASSOCIATED_BEFORE_CYCLE)
    files = {name:None if name.endswith(('-wal','-shm')) else dict(bytes=1024,sha256='b'*64)
             for name in binder.FILES}
    for name in (binder.NOTE,binder.SPOOL):files[name] = dict(bytes=56,sha256=quota.HASH)
    manifest = dict(schema=2,package=records.APP,identity=cycle['identity'],quota_association_scope=base,
        quota_stage=quota.ASSOCIATED_BEFORE_CYCLE,collection_id=COLLECTION,
        admission_metadata_canonical_sha256=DIGEST,spool_source=binder.SPOOL_SOURCE,files=files,
        app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False,
        normal_phone_permissions_verified=False)
    raw = json.dumps(manifest,indent=1).encode('utf-8')
    evidence = dict(original_manifest_raw_sha256=hashlib.sha256(raw).hexdigest(),original_files=deepcopy(files),
        query_only_new_working_copies=True,raw_DB_WAL_SHM_never_opened_by_SQLite=True,
        credentials_selected=False,completed_driver_and_stop_gate_verified=False,runtime_verified=False,
        collection_id=COLLECTION,admission_metadata_canonical_sha256=DIGEST,
        original_cycle_ids_present_at_capture=False,later_cycle_binding_verified=False)
    return dict(manifest_raw=raw,projection=projection,evidence=evidence,note=quota.BODY,spool=quota.BODY,
        parent=cycle['parent'],ready=cycle['ready'],cycle_scope=data['scope'])


class QuotaLateBinding(unittest.TestCase):
    def refused_before_hash(self,args):
        with patch.object(binder.hashlib,'sha256',side_effect=AssertionError('Unapproved input hashed')):
            with self.assertRaisesRegex(ValueError,'^SIGNED_QUOTA_LATE_BINDING_UNAVAILABLE$'):
                binder.bind_associated_capture(**args)

    def change_manifest(self,args,change):
        value = json.loads(args['manifest_raw']);change(value)
        args['manifest_raw'] = json.dumps(value).encode('utf-8')

    def test_exact_raw_and_typed_hashes_are_distinct_correlation_only(self):
        args = fixture();before = deepcopy(args);result = binder.bind_associated_capture(**args)
        self.assertEqual(args,before)
        binding = result['binding'];self.assertEqual(binding['original_associated_manifest_raw_sha256'],hashlib.sha256(args['manifest_raw']).hexdigest())
        self.assertNotEqual(binding['original_associated_manifest_raw_sha256'],records.digest(json.loads(args['manifest_raw'])))
        self.assertEqual(binding['associated_projection_metadata_canonical_sha256'],records.digest(args['projection']))
        self.assertEqual(binding['quota_parent_metadata_canonical_sha256'],records.digest(args['parent']))
        self.assertEqual(binding['quota_ready_metadata_canonical_sha256'],records.digest(args['ready']))
        self.assertEqual(result['before'],metadata_fixture()['before'])
        self.assertTrue(binding['projected_scope_bound_after_ready']);self.assertTrue(result['typed_correlation_only'])
        for key in ('runtime_verified','independent_raw_parent_ready_verified','Native_or_client_ACK','original_cycle_ids_present_at_capture'):
            self.assertFalse(binding[key])
        self.assertFalse(result['captured_before_quota_launch_independently_verified'])
        self.assertFalse(result['original_files_independently_verified'])

    def test_actual_new_capture_and_projection_feed_binding_without_another_read(self):
        with precycle_task() as (root,_,_,invoke,base,scope,selected,_,_):
            output = invoke();projected = snapshots.project_associated_before_cycle(output,root/'working',association_scope=base,selected=selected)
            raw = (output/'snapshot.json').read_bytes();original = retained(output);cycle,_,_ = records_fixture()
            with patch('builtins.open',side_effect=AssertionError('File IO during pure binding')), \
                 patch.object(Path,'open',side_effect=AssertionError('Path IO during pure binding')), \
                 patch.object(sqlite3,'connect',side_effect=AssertionError('SQL during pure binding')), \
                 patch.object(time,'monotonic',side_effect=AssertionError('Clock during pure binding')):
                result = binder.bind_associated_capture(raw,projected.projection,projected.evidence,
                    projected.note,projected.spool,parent=cycle['parent'],ready=cycle['ready'],cycle_scope=scope)
            self.assertEqual(result['before']['scope'],scope)
            self.assertEqual(result['before']['outbox'],projected.projection['outbox'])
            self.assertEqual(result['binding']['original_associated_manifest_raw_sha256'],projected.evidence['original_manifest_raw_sha256'])
            self.assertEqual(retained(output),original);self.assertFalse(result['runtime_verified'])

    def test_non_utf8_partial_duplicate_nonfinite_and_legacy_raw_reject_before_hash(self):
        for kind in ('utf8','partial','duplicate','nonfinite','legacy'):
            args = fixture();raw = args['manifest_raw']
            if kind=='utf8':raw = b'\xff'
            if kind=='partial':raw = raw[:-1]
            if kind=='duplicate':raw = raw.replace(b'"schema": 2',b'"schema": 2, "schema": 2',1)
            if kind=='nonfinite':raw = raw.replace(b'"schema": 2',b'"schema": NaN',1)
            if kind=='legacy':raw = raw.replace(b'"schema": 2',b'"schema": 1',1)
            args['manifest_raw'] = raw;self.refused_before_hash(args)

    def test_manifest_private_extra_and_future_scope_never_get_hashed(self):
        for change in [lambda x:x.update(private_UI='synthetic-private'),
                       lambda x:x['quota_association_scope'].update(parent_id=None),
                       lambda x:x['identity'].update(extra='synthetic-private')]:
            args = fixture();self.change_manifest(args,change);self.refused_before_hash(args)

    def test_raw_and_note_buffers_are_exact_bytes_without_coercion(self):
        for key,value in [('manifest_raw',Unknown()),('manifest_raw',bytearray(b'{}')),
                          ('note',Unknown()),('note',bytearray(quota.BODY)),('spool',None),('spool',b'x'*56)]:
            args = fixture();args[key] = value;self.refused_before_hash(args)

    def test_manifest_flags_schema_boolean_and_owner_are_closed_before_hash(self):
        for change in [lambda x:x.update(schema=True),lambda x:x.update(credential_store_copied=True),
                       lambda x:x.update(app_stopped_before_and_after=None),
                       lambda x:x['identity'].update(boot_id='00000000-0000-0000-0000-000000009999')]:
            args = fixture();self.change_manifest(args,change);self.refused_before_hash(args)

    def test_file_whitelist_metadata_nullability_and_56_byte_mapping(self):
        for change in [lambda x:x['files'].update(private_auth=None),
                       lambda x:x['files'].update({'mobile-drafts.sqlite3':None}),
                       lambda x:x['files'][binder.NOTE].update(bytes=True),
                       lambda x:x['files'][binder.SPOOL].update(sha256='b'*64),
                       lambda x:x['files']['mobile-drafts.sqlite3'].update(bytes=binder.MAX_FILE+1)]:
            args = fixture();self.change_manifest(args,change);self.refused_before_hash(args)

    def test_evidence_private_unknown_extra_and_false_claims_are_not_hash_inputs(self):
        for key,value in [('private',Unknown()),('original_manifest_raw_sha256',Unknown()),
            ('original_files',Unknown()),('runtime_verified',True),('later_cycle_binding_verified',True),
            ('completed_driver_and_stop_gate_verified',True),('admission_metadata_canonical_sha256',None)]:
            args = fixture();args['evidence'][key] = value;self.refused_before_hash(args)

    def test_known_wrong_raw_digest_is_not_repaired_or_returned_as_success(self):
        args = fixture();args['evidence']['original_manifest_raw_sha256'] = 'c'*64
        with self.assertRaises(ValueError):binder.bind_associated_capture(**args)
        self.assertEqual(args['evidence']['original_manifest_raw_sha256'],'c'*64)

    def test_base_projection_requires_original_save_null_and_independent_outbox(self):
        for change in [lambda x:x['save'].update(file_id='00000000-0000-0000-0000-000000000011'),
                       lambda x:x['outbox'][0].update(state='acked'),
                       lambda x:x['outbox'][1].update(operation_id=x['outbox'][0]['operation_id']),
                       lambda x:x['attempts'].append(dict(attempts=1)),
                       lambda x:x['association'].update(original_request_bytes_sha256=Unknown())]:
            args = fixture();change(args['projection']);self.refused_before_hash(args)

    def test_full_cycle_scope_cannot_change_base_or_use_unknown_fields(self):
        for key,value in [('uid',True),('private',Unknown()),('android_device','4'*32),
                          ('parent_id',Unknown()),('window',0),('owner_run','other')]:
            args = fixture();args['cycle_scope'][key] = value;self.refused_before_hash(args)

    def test_parent_phase_native_id_and_private_metadata_must_close_before_hash(self):
        for key,value in [('private',Unknown()),('schema',True),('phase','associate'),
                          ('native_invocation_id_observed',True),('input_intent_id',Unknown())]:
            args = fixture();args['parent'][key] = value;self.refused_before_hash(args)

    def test_parent_input_collection_and_operation_ids_are_separate(self):
        for known in [COLLECTION,metadata_fixture()['scope']['save_operation_id'],metadata_fixture()['scope']['boot_id']]:
            args = fixture();args['parent']['parent_intent_id'] = known
            args['ready']['parent_intent_id'] = known;args['cycle_scope']['parent_id'] = known
            self.refused_before_hash(args)
        args = fixture();args['parent']['input_intent_id'] = args['parent']['parent_intent_id'];self.refused_before_hash(args)

    def test_ready_scope_geometry_unknown_and_clock_integer_bounds(self):
        for key,value in [('window',True),('uid',10000),('visible',[0,0,0,0]),
                          ('x',Unknown()),('elapsed_ms',records.MAX-35999),('private',Unknown())]:
            args = fixture();args['ready'][key] = value;self.refused_before_hash(args)

    def test_ready_parent_window_and_full_scope_must_describe_same_cycle(self):
        for change in [lambda a:a['ready'].update(driver_intent_id=a['cycle_scope']['save_operation_id']),
                       lambda a:a['cycle_scope'].update(window=42),
                       lambda a:a['cycle_scope'].update(input_id=a['cycle_scope']['setup_operation_id'])]:
            args = fixture();change(args);self.refused_before_hash(args)

    def test_raw_manifest_16384_endpoint_is_distinct_from_canonical_size(self):
        args = fixture();args['manifest_raw'] += b' '*(binder.MAX_MANIFEST-len(args['manifest_raw']))
        args['evidence']['original_manifest_raw_sha256'] = hashlib.sha256(args['manifest_raw']).hexdigest()
        self.assertEqual(binder.bind_associated_capture(**args)['binding']['original_associated_manifest_raw_sha256'],args['evidence']['original_manifest_raw_sha256'])
        args['manifest_raw'] += b' ';self.refused_before_hash(args)

    def test_synthetic_file_limit_and_ready_long_endpoint_are_only_metadata(self):
        args = fixture();value = json.loads(args['manifest_raw'])
        value['files']['mobile-drafts.sqlite3']['bytes'] = binder.MAX_FILE
        args['manifest_raw'] = json.dumps(value).encode();args['evidence']['original_files'] = deepcopy(value['files'])
        args['evidence']['original_manifest_raw_sha256'] = hashlib.sha256(args['manifest_raw']).hexdigest()
        args['ready']['elapsed_ms'] = records.MAX-36000
        result = binder.bind_associated_capture(**args);self.assertFalse(result['original_files_independently_verified'])
        self.assertFalse(result['captured_before_quota_launch_independently_verified'])

    def test_inputs_and_both_outputs_do_not_share_arrays_or_scope_dicts(self):
        args = fixture();result = binder.bind_associated_capture(**args)
        args['projection']['outbox'][0]['state'] = 'changed';args['cycle_scope']['uid'] = 1
        self.assertEqual(result['before']['outbox'][0]['state'],'archived')
        self.assertEqual(result['before']['scope']['uid'],10216)
        result['before']['scope']['uid'] = 2
        self.assertEqual(result['binding']['full_cycle_scope']['uid'],10216)
        self.assertEqual(json.loads(args['manifest_raw'])['quota_association_scope']['uid'],10216)


if __name__ == '__main__':unittest.main()
