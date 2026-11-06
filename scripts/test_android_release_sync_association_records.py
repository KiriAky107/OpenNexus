"""Synthetic report validation only; no actor, device, files or stopped originals."""
from copy import deepcopy
import hashlib
import json
import shlex
import unittest
from unittest.mock import patch

import android_release_sync_association_records as records
import android_release_sync_quota_records as common
import android_release_sync_quota_validation as quota


def identifier(n):
    return f'00000000-0000-0000-0000-{n:012x}'


def fixture():
    scope = dict(schema=1, owner_run='99999999999-1', avd='opennexus-ci-99999999999-1',
        boot_id=identifier(1), api=36, uid=10123, product=quota.PRODUCT, sync=quota.SYNC,
        remote_vault='a'*32, android_device='b'*32, setup_device='c'*32, preview_fingerprint='d'*64,
        login_operation_id=identifier(2), setup_operation_id=identifier(3), save_operation_id=identifier(4),
        association_operation_id=identifier(5))
    identity = dict(avd=scope['avd'], qemu='1', api='36', build_type='userdebug', debuggable='1', boot_id=scope['boot_id'])
    life = dict(package=common.APP, pid=1234, uid=scope['uid'], start_ticks=123456)

    def owner(at, command=False):
        result = dict(verified_monotonic_ms=at, original_lifetime=deepcopy(life), owned_boot=deepcopy(identity),
            original_driver_completed=True, same_original_foreground_PID_UID_boot=True, app_resumed=True,
            root_uid=0, native_cycle_acknowledged=False)
        if command:
            result['observed_after_private_command_intent'] = True
        return result

    driver = dict(phase=records.PHASE, entry=records.ENTRY, command_id=identifier(7),
        arguments=['shell','-T',shlex.join(('am','instrument','-w','-e','phase',records.PHASE,common.DRIVER+'/.'+records.ENTRY))],
        timeout_ms=180000, started_monotonic_ms=1000, deadline_monotonic_ms=181000,
        first_exit_seen_monotonic_ms=1200, streams_read_started_monotonic_ms=1250,
        streams_read_returned_monotonic_ms=1300,
        instrument_result=dict(returncode=0, unknown_result=False, automatic_retry=False,
            early_cleanup_not_success=False, no_final_product_force_stop_or_reopen=True), final_owner=owner(1600))
    read = dict(arguments=['shell','-T','run-as '+common.DRIVER+' sh -c '+shlex.quote(
        'test -f '+records.INTENT_PATH+' && test ! -L '+records.INTENT_PATH+' && cat '+records.INTENT_PATH)],
        bound_bytes=2048, timeout_ms=1000, read_count=1, returncode=0, unknown_result=False,
        automatic_retry=False, stderr_bytes=0, unvalidated_streams_exported=False,
        started_monotonic_ms=1400, returned_monotonic_ms=1500)
    samples = []
    for i in (1, 2):
        samples.append(dict(schema=1, sample_index=i, observation_id=identifier(8+i), stop_intent_id=identifier(8),
            package=common.APP, original_pid=life['pid'], product_uid=life['uid'], owned_boot=deepcopy(identity), root_uid=0,
            sample_start_monotonic_ms=1900+i*100, sample_end_monotonic_ms=1901+i*100,
            original_pid_absent=True, all_owned_package_processes_absent=True, user0_force_stopped=True,
            original_driver_completed=True, raw_process_list_exported=False, native_cycle_acknowledged=False))
    stop = dict(schema=1, phase=records.PHASE, driver_command_id=driver['command_id'], stop_intent_id=identifier(8),
        original_lifetime=deepcopy(life), owned_boot=deepcopy(identity), intent=dict(arguments=['shell','-T',
            shlex.join(('am','force-stop','--user','0',common.APP))], original_driver_completed=True,
            snapshot_stop_only=True, automatic_retry=False), intent_written_monotonic_ms=1720,
        before_command_owner=owner(1700), at_command_owner=owner(1750, True),
        result=dict(returncode=0, automatic_retry=False, stdout_bytes=0, stdout_sha256=common.EMPTY,
            stderr_bytes=0, stderr_sha256=common.EMPTY, unvalidated_streams_exported=False,
            unknown_result=False, device_command_invoked=True),
        command_clock=dict(started_monotonic_ms=1800, returned_monotonic_ms=1900, observation_deadline_monotonic_ms=6800),
        observations=samples)
    value = dict(schema=1, capture_scope=scope, identity=identity, collection_id=identifier(6),
        original_lifetime=life, driver=driver, intent_read=read, stop=stop)
    intent = dict(operation_id=scope['association_operation_id'], preview_fingerprint=scope['preview_fingerprint'], remote_vault=scope['remote_vault'])
    return value, common.encoded(intent), b'\n'.join(records.LINES)+b'\n', b''


def assign(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


class AssociationRecords(unittest.TestCase):
    def rejected(self, value, intent, stdout, stderr):
        with patch.object(records.hashlib, 'sha256', side_effect=AssertionError('Hash before closure')) as digest:
            with self.assertRaises(ValueError):
                records.validate_association_reports(value, intent, stdout, stderr)
            digest.assert_not_called()

    def test_valid_reports_are_detached_correlation_with_false_independence(self):
        inputs = fixture()
        result = records.validate_association_reports(*inputs)
        self.assertEqual(result['profile'], records.PROFILE)
        self.assertTrue(result['completed_driver_reported'] and result['once_stop_reported'])
        self.assertTrue(result['two_distinct_stopped_samples_reported'])
        for key in ('original_managed_process_independently_verified','raw_intent_reader_independently_verified',
                    'original_private_files_independently_verified','actual_separate_syscalls_verified',
                    'collector_bracketing_samples_verified','capture_before_quota_chronology_verified','Native_or_client_ACK','runtime_verified'):
            self.assertIs(result[key], False)
        result['capture_scope']['uid'] = 12345
        result['identity']['boot_id'] = identifier(999)
        result['observation_ids'][0] = identifier(999)
        original = records.validate_association_reports(*inputs)
        self.assertEqual(original['capture_scope']['uid'], 10123)
        self.assertEqual(original['identity']['boot_id'], identifier(1))
        self.assertEqual(original['observation_ids'], [identifier(9), identifier(10)])

    def test_call_has_no_IO_SQL_clock_or_action(self):
        inputs = fixture()
        with patch('builtins.open', side_effect=AssertionError('IO')), patch('pathlib.Path.open', side_effect=AssertionError('IO')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQL')), patch('time.monotonic', side_effect=AssertionError('Clock')):
            self.assertFalse(records.validate_association_reports(*inputs)['runtime_verified'])

    def test_private_or_partial_nested_reports_refuse_before_hash(self):
        paths = [(),('capture_scope',),('identity',),('original_lifetime',),('driver',),('driver','final_owner'),
                 ('driver','instrument_result'),('intent_read',),('stop',),('stop','intent'),('stop','result'),
                 ('stop','before_command_owner'),('stop','at_command_owner'),('stop','command_clock'),('stop','observations',0)]
        for path in paths:
            for partial in (False, True):
                with self.subTest(path=path, partial=partial):
                    value, *rest = fixture()
                    target = value
                    for key in path:
                        target = target[key]
                    if partial:
                        target.pop(next(iter(target)))
                    else:
                        target['private_extra'] = object()
                    self.rejected(value, *rest)

    def test_bool_int_unknown_and_Long_ranges_refuse_before_hash(self):
        changes = [(('schema',),True),(('capture_scope','api'),True),(('capture_scope','uid'),False),
            (('original_lifetime','pid'),True),(('original_lifetime','start_ticks'),2**64),
            (('driver','timeout_ms'),180000.0),(('driver','started_monotonic_ms'),records.MAX-179999),
            (('driver','first_exit_seen_monotonic_ms'),None),(('driver','instrument_result','returncode'),False),
            (('intent_read','read_count'),True),(('intent_read','returncode'),False),
            (('stop','result','device_command_invoked'),1),(('stop','command_clock','started_monotonic_ms'),records.MAX-4999),
            (('stop','observations',0,'sample_index'),True),(('stop','observations',1,'sample_end_monotonic_ms'),records.MAX+1)]
        for path, replacement in changes:
            with self.subTest(path=path):
                value, *rest = fixture();assign(value, path, replacement);self.rejected(value, *rest)

    def test_actual_known_scope_only_no_binding_or_future_cycle_IDs(self):
        for key in ('binding_id','upload_operation_id','parent_id','input_id','window'):
            value, *rest = fixture();value['capture_scope'][key] = identifier(99);self.rejected(value, *rest)
        for path, replacement in [(('capture_scope','product'),'f'*40),(('capture_scope','sync'),'f'*40),
            (('capture_scope','owner_run'),'1-1'),(('capture_scope','android_device'),'c'*32),
            (('capture_scope','association_operation_id'),identifier(4))]:
            value, *rest = fixture();assign(value, path, replacement);self.rejected(value, *rest)

    def test_collection_command_stop_and_sample_IDs_cannot_be_reused(self):
        changes = [(('collection_id',),identifier(5)),(('driver','command_id'),identifier(6)),
            (('stop','stop_intent_id'),identifier(7)),(('stop','driver_command_id'),identifier(99)),
            (('stop','observations',1,'observation_id'),identifier(9)),(('stop','observations',0,'observation_id'),identifier(1)),
            (('stop','observations',0,'observation_id'),identifier(8)),(('stop','observations',0,'stop_intent_id'),identifier(99))]
        for path, replacement in changes:
            value, *rest = fixture();assign(value, path, replacement);self.rejected(value, *rest)

    def test_exact_command_and_unknown_retry_cleanup_cannot_pass(self):
        changes = [(('driver','phase'),'associate'),(('driver','entry'),'SyncRuntimeProbe'),
            (('driver','arguments'),['shell','-T','am instrument; am instrument']),
            (('driver','instrument_result','unknown_result'),True),(('driver','instrument_result','automatic_retry'),True),
            (('driver','instrument_result','early_cleanup_not_success'),True),
            (('driver','instrument_result','no_final_product_force_stop_or_reopen'),False),
            (('intent_read','arguments'),['shell','-T','run-as '+common.APP+' cat private']),
            (('intent_read','read_count'),2),(('intent_read','timeout_ms'),120000),(('intent_read','bound_bytes'),4096),
            (('intent_read','unknown_result'),True),(('stop','intent','automatic_retry'),True),
            (('stop','intent','arguments'),['shell','-T','am force-stop; am force-stop']),
            (('stop','result','unknown_result'),True),(('stop','result','returncode'),None),
            (('stop','result','stdout_bytes'),1),(('stop','result','stdout_sha256'),'a'*64)]
        for path, replacement in changes:
            value, *rest = fixture();assign(value, path, replacement);self.rejected(value, *rest)

    def test_readonly_same_product_owner_required_without_driver_alive_claim(self):
        paths = [('driver','final_owner'),('stop','before_command_owner'),('stop','at_command_owner')]
        for path in paths:
            for tail, replacement in [(('root_uid',),1),(('app_resumed',),False),(('original_driver_completed',),False),
                (('same_original_foreground_PID_UID_boot',),False),(('original_lifetime','start_ticks'),99),
                (('owned_boot','boot_id'),identifier(99))]:
                value, *rest = fixture();assign(value, (*path,*tail), replacement);self.rejected(value, *rest)
            value, *rest = fixture();owner=value
            for key in path:owner=owner[key]
            owner['original_instrumentation_alive'] = True
            self.rejected(value, *rest)

    def test_order_and_original_deadlines_cannot_be_reset(self):
        changes = [(('driver','first_exit_seen_monotonic_ms'),999),(('driver','streams_read_started_monotonic_ms'),1199),
            (('driver','streams_read_returned_monotonic_ms'),1450),(('driver','deadline_monotonic_ms'),181001),
            (('driver','final_owner','verified_monotonic_ms'),181001),(('intent_read','returned_monotonic_ms'),2401),
            (('stop','before_command_owner','verified_monotonic_ms'),1599),
            (('stop','intent_written_monotonic_ms'),1699),(('stop','at_command_owner','verified_monotonic_ms'),1801),
            (('stop','command_clock','returned_monotonic_ms'),3801),
            (('stop','command_clock','observation_deadline_monotonic_ms'),6801),
            (('stop','observations',0,'sample_start_monotonic_ms'),1899),
            (('stop','observations',1,'sample_start_monotonic_ms'),2000),
            (('stop','observations',1,'sample_end_monotonic_ms'),6801)]
        for path, replacement in changes:
            value, *rest = fixture();assign(value, path, replacement);self.rejected(value, *rest)

    def test_180s_1s_2s_5s_and_Long_exact_metadata_endpoints(self):
        for end in (181000, records.MAX-5000):
            value, *rest = fixture();driver=value['driver'];stop=value['stop'];read=value['intent_read']
            start=end-180000
            driver.update(started_monotonic_ms=start,deadline_monotonic_ms=end,first_exit_seen_monotonic_ms=start+200,
                streams_read_started_monotonic_ms=start+250,streams_read_returned_monotonic_ms=start+300)
            read.update(started_monotonic_ms=start+400,returned_monotonic_ms=start+1400)
            driver['final_owner']['verified_monotonic_ms']=end
            stop['before_command_owner']['verified_monotonic_ms']=end
            stop['intent_written_monotonic_ms']=end;stop['at_command_owner']['verified_monotonic_ms']=end
            stop['command_clock'].update(started_monotonic_ms=end,returned_monotonic_ms=end+2000,observation_deadline_monotonic_ms=end+5000)
            stop['observations'][0].update(sample_start_monotonic_ms=end+2000,sample_end_monotonic_ms=end+2001)
            stop['observations'][1].update(sample_start_monotonic_ms=end+5000,sample_end_monotonic_ms=end+5000)
            self.assertFalse(records.validate_association_reports(value,*rest)['runtime_verified'])

    def test_two_distinct_indexed_stopped_sample_reports_required(self):
        changes = [(('original_pid_absent',),False),(('all_owned_package_processes_absent',),False),
            (('user0_force_stopped',),False),(('raw_process_list_exported',),True),(('native_cycle_acknowledged',),True),
            (('product_uid',),10124),(('original_pid',),1235),(('root_uid',),1),(('sample_index',),2)]
        for tail, replacement in changes:
            value, *rest = fixture();assign(value, ('stop','observations',0,*tail), replacement);self.rejected(value,*rest)
        for count in (0,1,3):
            value, *rest = fixture();value['stop']['observations']=[deepcopy(value['stop']['observations'][0]) for _ in range(count)];self.rejected(value,*rest)
        value, *rest = fixture();value['stop']['observations'][1]=value['stop']['observations'][0];self.rejected(value,*rest)

    def test_intent_keys_types_owner_and_original_operation_are_closed(self):
        for key, replacement in [('operation_id',identifier(99)),('preview_fingerprint','a'*64),('remote_vault','b'*32),
                                  ('operation_id',True),('operation_id',123)]:
            value, intent, stdout, stderr=fixture();decoded=json.loads(intent);decoded[key]=replacement
            self.rejected(value,common.encoded(decoded),stdout,stderr)
        value,intent,stdout,stderr=fixture()
        for raw in (b'{}',b'[]',b'null',b'{"private":"secret"}',intent[:-1]+b',"operation_id":"private"}',
                    intent[:-1],b'\xff',b'{"operation_id":NaN}',b'{"operation_id":1e309}'):
            self.rejected(value,raw,stdout,stderr)

    def test_intent_2048_and_stdout_4096_exact_bounds(self):
        value,intent,stdout,stderr=fixture()
        approved_intent=intent+b' '*(2048-len(intent));approved_stdout=stdout+b'\n'*(4096-len(stdout))
        self.assertFalse(records.validate_association_reports(value,approved_intent,approved_stdout,stderr)['runtime_verified'])
        self.rejected(value,approved_intent+b' ',stdout,stderr)
        self.rejected(value,intent,approved_stdout+b'\n',stderr)

    def test_private_partial_duplicate_or_reordered_streams_never_hash(self):
        value,intent,stdout,stderr=fixture()
        for raw in (b'',stdout+b'private account\n',stdout.replace(b'false',b'true',1),stdout[:-10],
                    stdout+stdout,b'\n'.join(reversed(records.LINES))+b'\n',b'OPENNEXUS_SYNC_PREVIEW_OBSERVATION private\n'+stdout):
            self.rejected(value,intent,raw,stderr)
        self.rejected(value,intent,stdout,b'private error')

    def test_approved_framing_and_raw_vs_typed_hashes_remain_distinct(self):
        value,intent,stdout,stderr=fixture()
        framed=b'INSTRUMENTATION_STATUS: stream='+records.LINES[0]+b'\n\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_RESULT: stream='+records.LINES[1]+b'\n\nINSTRUMENTATION_CODE: 0\n'
        a=records.validate_association_reports(value,intent,stdout,stderr)
        b=records.validate_association_reports(value,b' '+intent,framed,stderr)
        self.assertEqual(a['association_intent_raw_sha256'],hashlib.sha256(intent).hexdigest())
        self.assertNotEqual(a['association_intent_raw_sha256'],b['association_intent_raw_sha256'])
        self.assertEqual(a['association_intent_metadata_canonical_sha256'],b['association_intent_metadata_canonical_sha256'])
        self.assertEqual(a['completion_metadata_canonical_sha256'],b['completion_metadata_canonical_sha256'])
        self.assertNotEqual(a['stdout_raw_sha256'],b['stdout_raw_sha256'])

    def test_bounded_report_parser_rejects_duplicate_private_or_partial_JSON(self):
        value,intent,stdout,stderr=fixture();raw=common.encoded(value)
        padded=raw+b' '*(records.LIMIT-len(raw))
        self.assertEqual(records.parse_association_reports(padded,intent,stdout,stderr),records.validate_association_reports(value,intent,stdout,stderr))
        malformed=[padded+b' ',raw[:-1],b'\xff',b'{"schema":1,"schema":1}',b'NaN',b'Infinity',b'[]',b'null',
                   raw.replace(b'"schema":1',b'"schema":1,"schema":1',1),b'{"private":"secret"}']
        for candidate in malformed:
            with patch.object(records.hashlib,'sha256',side_effect=AssertionError('Unknown bytes hashed')) as digest:
                with self.assertRaises(ValueError):records.parse_association_reports(candidate,intent,stdout,stderr)
                digest.assert_not_called()

    def test_unknown_objects_are_not_coerced_or_stringified(self):
        class Unknown:
            def __str__(self):raise AssertionError('Unknown string conversion')
            def __eq__(self,other):raise AssertionError('Unknown equality')
            def __int__(self):raise AssertionError('Unknown number conversion')
            def __bytes__(self):raise AssertionError('Unknown bytes conversion')
        for path in [('schema',),('collection_id',),('driver','started_monotonic_ms'),('stop','result','returncode')]:
            value,*rest=fixture();assign(value,path,Unknown());self.rejected(value,*rest)
        value,intent,stdout,stderr=fixture();self.rejected(value,Unknown(),stdout,stderr)


if __name__ == '__main__':
    unittest.main()
