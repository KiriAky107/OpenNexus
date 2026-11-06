"""Pure prelaunch/actual-intent derivation and completion-only profile checks."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import android_release_sync_association_records as records
import android_release_sync_quota_records as common
from test_android_release_sync_association_records import fixture, identifier, assign


def vectors():
    full, intent, stdout, stderr = fixture()
    completion = {k:v for k,v in full.items() if k != 'stop'}
    launch = {k:v for k,v in full['capture_scope'].items()
              if k not in ('association_operation_id','preview_fingerprint')}
    return launch, completion, full, intent, stdout, stderr


class AssociationCompletion(unittest.TestCase):
    def no_hash(self):
        return patch.object(records.hashlib, 'sha256', side_effect=AssertionError('Unexpected SHA'))

    def reject_completion(self, value, intent, stdout, stderr):
        with self.no_hash() as digest:
            with self.assertRaises(ValueError):
                records.validate_association_completion(value, intent, stdout, stderr)
            digest.assert_not_called()

    def test_launch_base_has_no_synthetic_bootstrap_call_or_future_IDs(self):
        launch, *_ = vectors()
        with self.no_hash(), patch.object(records.quota, 'bootstrap_scope', side_effect=AssertionError('Fake future scope')):
            result = records.association_launch_scope(launch)
        self.assertEqual(len(result), 14)
        self.assertEqual(result, launch)
        result['uid'] = 12345
        self.assertEqual(launch['uid'], 10123)

    def test_launch_base_requires_every_known_field_and_no_extra(self):
        launch, *_ = vectors()
        for key in launch:
            missing = dict(launch);del missing[key]
            with self.no_hash(), self.assertRaises(ValueError):records.association_launch_scope(missing)
        for key in ('association_operation_id','preview_fingerprint','binding_id','upload_operation_id','parent_id','input_id','window','private'):
            with self.no_hash(), self.assertRaises(ValueError):records.association_launch_scope(dict(launch, **{key: None}))

    def test_launch_type_owner_device_and_operation_identity_refusals(self):
        launch, *_ = vectors()
        changes = dict(schema=True, api=True, uid=False, avd='other', owner_run=None, product='0'*40,
            sync='0'*40, boot_id=identifier(2), save_operation_id=identifier(3), android_device='c'*32,
            remote_vault='private', setup_device=123)
        for key, value in changes.items():
            with self.no_hash(), self.assertRaises(ValueError):records.association_launch_scope(dict(launch, **{key:value}))

    def test_capture_scope_uses_actual_supplied_intent_only_without_hash(self):
        launch, completion, full, intent, *_ = vectors()
        actual = records.quota.bootstrap_scope
        with self.no_hash(), patch.object(records.quota, 'bootstrap_scope', wraps=actual) as admit:
            scope = records.capture_scope_from_intent(launch, intent,
                collection_id=completion['collection_id'], command_id=completion['driver']['command_id'])
        self.assertEqual(scope, full['capture_scope'])
        self.assertEqual(admit.call_count, 1)
        self.assertEqual(admit.call_args.args[0], full['capture_scope'])
        scope['association_operation_id'] = identifier(100)
        self.assertEqual(full['capture_scope']['association_operation_id'], identifier(5))
        self.assertNotIn('association_operation_id', launch)

    def test_derivation_rejects_reused_collection_command_and_GUI_IDs(self):
        launch, completion, full, intent, *_ = vectors()
        cases = [(identifier(1),identifier(7),intent),(identifier(6),identifier(6),intent),
                 (identifier(6),identifier(3),intent),(None,identifier(7),intent)]
        for reused in (identifier(1),identifier(2),identifier(3),identifier(4),identifier(6),identifier(7)):
            value=json.loads(intent);value['operation_id']=reused
            cases.append((identifier(6),identifier(7),common.encoded(value)))
        for collection, command, raw in cases:
            with self.no_hash(), self.assertRaises(ValueError):
                records.capture_scope_from_intent(launch, raw, collection_id=collection, command_id=command)

    def test_derivation_intent_is_bounded_closed_typed_and_owned(self):
        launch, completion, full, intent, *_ = vectors()
        raw_cases=[b'{}',b'[]',b'null',intent[:-1],b'\xff',intent[:-1]+b',"operation_id":"private"}',
                   b'{"operation_id":NaN}',b'{"operation_id":1e309}',b'{"private":"content"}']
        for key, wrong in [('operation_id',True),('operation_id','wrong'),('preview_fingerprint',None),
                           ('preview_fingerprint','z'*64),('remote_vault','b'*32)]:
            value=json.loads(intent);value[key]=wrong;raw_cases.append(common.encoded(value))
        for raw in raw_cases:
            with self.no_hash(), self.assertRaises(ValueError):
                records.capture_scope_from_intent(launch, raw, collection_id=completion['collection_id'], command_id=completion['driver']['command_id'])
        padded=intent+b' '*(2048-len(intent))
        with self.no_hash():
            self.assertEqual(records.capture_scope_from_intent(launch,padded,
                collection_id=identifier(6),command_id=identifier(7)),full['capture_scope'])
        with self.no_hash(), self.assertRaises(ValueError):
            records.capture_scope_from_intent(launch,padded+b' ',collection_id=identifier(6),command_id=identifier(7))

    def test_completion_only_success_performs_zero_crypto_and_has_no_future_stop(self):
        _, completion, _, intent, stdout, stderr = vectors()
        with self.no_hash() as digest, patch.object(common,'digest',side_effect=AssertionError('Canonical SHA')):
            result=records.validate_association_completion(completion,intent,stdout,stderr)
            digest.assert_not_called()
        self.assertEqual(result['profile'],records.COMPLETION_PROFILE)
        self.assertEqual(result['completion'],completion)
        self.assertEqual(result['association_intent'],json.loads(intent))
        self.assertTrue(result['completion_only'] and result['completed_driver_reported'])
        self.assertIs(result['stop_reported'],False)
        self.assertNotIn('stop',result['completion'])
        self.assertFalse(any('sha256' in key for key in result))
        for key in ('original_managed_process_independently_verified','raw_intent_reader_independently_verified',
                    'original_private_files_independently_verified','actual_separate_syscalls_verified',
                    'collector_bracketing_samples_verified','capture_before_quota_chronology_verified','Native_or_client_ACK','runtime_verified'):
            self.assertIs(result[key],False)

    def test_completion_only_and_full_profiles_do_not_accept_each_other(self):
        _, completion, full, intent, stdout, stderr = vectors()
        self.reject_completion(full,intent,stdout,stderr)
        self.reject_completion(dict(completion,stop=None),intent,stdout,stderr)
        output=records.validate_association_completion(completion,intent,stdout,stderr)
        for value in (completion,output):
            with self.no_hash(),self.assertRaises(ValueError):records.validate_association_reports(value,intent,stdout,stderr)
            with self.no_hash(),self.assertRaises(ValueError):records.parse_association_reports(common.encoded(value),intent,stdout,stderr)

    def test_completion_only_private_partial_nested_fields_refuse(self):
        paths=[(),('capture_scope',),('identity',),('original_lifetime',),('driver',),
               ('driver','instrument_result'),('driver','final_owner'),('intent_read',)]
        for path in paths:
            for partial in (False,True):
                _,value,_,intent,stdout,stderr=vectors();target=value
                for key in path:target=target[key]
                if partial:target.pop(next(iter(target)))
                else:target['private_extra']=object()
                self.reject_completion(value,intent,stdout,stderr)

    def test_completion_only_requires_known_complete_driver_and_original_bounds(self):
        mutations=[(('driver','instrument_result','returncode'),False),
            (('driver','instrument_result','unknown_result'),True),(('driver','instrument_result','early_cleanup_not_success'),True),
            (('driver','instrument_result','automatic_retry'),True),(('driver','final_owner','app_resumed'),False),
            (('driver','deadline_monotonic_ms'),181001),(('intent_read','read_count'),2),
            (('intent_read','returned_monotonic_ms'),2401),(('driver','first_exit_seen_monotonic_ms'),None)]
        for path, replacement in mutations:
            _,value,_,intent,stdout,stderr=vectors();assign(value,path,replacement);self.reject_completion(value,intent,stdout,stderr)

    def test_completion_only_bad_intent_or_stream_is_never_hashed(self):
        _,completion,_,intent,stdout,stderr=vectors()
        for raw in (b'',b'{}',intent[:-1],intent+b' '*(2049-len(intent))):
            self.reject_completion(completion,raw,stdout,stderr)
        for stream in (b'',stdout+b'private\n',b'\n'.join(reversed(records.LINES))+b'\n',stdout+stdout):
            self.reject_completion(completion,intent,stream,stderr)
        self.reject_completion(completion,intent,stdout,b'private')

    def test_all_new_calls_are_pure_no_file_SQL_clock_or_hash(self):
        launch,completion,_,intent,stdout,stderr=vectors()
        with self.no_hash(), patch('builtins.open',side_effect=AssertionError('IO')), \
             patch('pathlib.Path.open',side_effect=AssertionError('IO')), patch('sqlite3.connect',side_effect=AssertionError('SQL')), \
             patch('time.monotonic',side_effect=AssertionError('Clock')):
            records.association_launch_scope(launch)
            records.capture_scope_from_intent(launch,intent,collection_id=identifier(6),command_id=identifier(7))
            self.assertFalse(records.validate_association_completion(completion,intent,stdout,stderr)['runtime_verified'])

    def test_completion_only_nested_outputs_have_no_input_or_output_alias(self):
        _,completion,_,intent,stdout,stderr=vectors();original=deepcopy(completion)
        a=records.validate_association_completion(completion,intent,stdout,stderr)
        b=records.validate_association_completion(completion,intent,stdout,stderr)
        a['completion']['driver']['arguments'][0]='changed'
        a['completion']['identity']['boot_id']=identifier(99)
        a['association_intent']['operation_id']=identifier(99)
        self.assertEqual(completion,original)
        self.assertEqual(b['completion'],original)
        self.assertEqual(b['association_intent']['operation_id'],identifier(5))

    def test_private_future_stop_is_never_serialized_or_hashed_during_full_closure(self):
        class Private:
            def __str__(self):raise AssertionError('Private string conversion')
        _,_,full,intent,stdout,stderr=vectors();private=Private();full['stop']['private_extra']=private
        original_dumps=json.dumps;serialized=[]

        def guarded(value,*args,**kwargs):
            def inspect(item):
                if item is private:raise AssertionError('Private serialization')
                if type(item) is dict:
                    for v in item.values():inspect(v)
                elif type(item) is list:
                    for v in item:inspect(v)
            inspect(value);serialized.append(value)
            return original_dumps(value,*args,**kwargs)

        with self.no_hash() as digest,patch.object(records.json,'dumps',side_effect=guarded):
            with self.assertRaises(ValueError):records.validate_association_reports(full,intent,stdout,stderr)
            digest.assert_not_called()
        self.assertTrue(serialized)

    def test_full_report_retains_original_canonical_hash_inputs_after_split(self):
        _,completion,full,intent,stdout,stderr=vectors()
        before=records.validate_association_completion(completion,intent,stdout,stderr)
        result=records.validate_association_reports(full,intent,stdout,stderr)
        self.assertEqual(result['profile'],records.PROFILE)
        self.assertEqual(result['completion_metadata_canonical_sha256'],hashlib.sha256(common.encoded(before['completion'])).hexdigest())
        self.assertEqual(result['stop_metadata_canonical_sha256'],hashlib.sha256(common.encoded(full['stop'])).hexdigest())
        self.assertEqual(result['association_intent_raw_sha256'],hashlib.sha256(intent).hexdigest())
        self.assertTrue(result['once_stop_reported'])
        self.assertFalse(result['runtime_verified'])


if __name__ == '__main__':
    unittest.main()
