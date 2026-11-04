"""Synthetic new-protocol metadata, not live original driver/stop evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest

import android_release_sync_quota_records as records
import android_release_sync_quota_upload as upload
from android_sync_stop_host import record as original_record
from test_android_release_sync_quota_validation import fixture as quota_fixture


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


def empty(invoked=True):
    value = dict(returncode=0, automatic_retry=False, stdout_bytes=0, stdout_sha256=hashlib.sha256(b'').hexdigest(),
                 stderr_bytes=0, stderr_sha256=hashlib.sha256(b'').hexdigest(), unvalidated_streams_exported=False)
    if invoked:
        value.update(unknown_result=False, device_command_invoked=True)
    return value


def signal(name):
    path = 'no_backup/' + name
    return dict(intent=dict(arguments=['shell','-T','run-as cc.kronecker.opennexus.testdriver sh -c '+
                shlex.quote('umask 077; test ! -e '+path+' && test ! -L '+path+' && cat > '+path)],
                bytes=7, automatic_retry=False), result=empty(False),
                readback=dict(exact_static_bytes=True, bytes=7, private_signal_bytes_exported=False))


def fixture():
    scope = deepcopy(quota_fixture()['scope'])
    life = dict(package=records.APP, pid=1234, uid=scope['uid'], start_ticks=5000)
    identity = dict(avd=scope['avd'], qemu='1', api='36', build_type='userdebug', debuggable='1', boot_id=scope['boot_id'])
    parent = upload.parent_value(scope['parent_id'], scope['input_id'])
    context = upload.context_from_parent(parent, owner_run=scope['owner_run'],
                                         remote_vault=scope['remote_vault'], device_id=scope['android_device'])
    ready = dict(schema=1, phase=records.PHASE, action='cycle-start', parent_intent_id=scope['parent_id'],
                 driver_intent_id=scope['input_id'], package=records.APP, uid=scope['uid'], window=scope['window'],
                 original=[0,0,100,80], visible=[10,10,90,70], x=50, y=40, elapsed_ms=1000)
    owner = dict(original_lifetime=life, same_original_foreground_PID_UID_boot=True,
                 original_instrumentation_alive=True, ready_age_ms=10, ready_age_bound_ms=2000,
                 native_cycle_acknowledged=False)
    final_owner = dict(original_lifetime=life, owned_boot=identity, original_driver_completed=True,
                       same_original_foreground_PID_UID_boot=True, app_resumed=True, root_uid=0,
                       native_cycle_acknowledged=False)
    value = dict(schema=1, scope=scope, identity=identity, phase=records.PHASE, entry=records.ENTRY,
        parent=parent, context=context,
        context_write=dict(phase=records.PHASE, context_file_sha256=hashlib.sha256(encode(context)).hexdigest(),
                           same_writer_buffer_reported=True, original_buffer_write_returned=True,
                           automatic_retry=False, native_cycle_acknowledged=False),
        context_ready_signal=signal('owned-sync-quota-context-'+scope['parent_id']+'.finish'),
        ready=ready, original_lifetime=life, input_owner=owner,
        input_owner_at_command=dict(owner, ready_age_ms=20, observed_after_private_command_intent=True),
        input_clock=dict(sample_start_monotonic_ms=2000, sample_end_monotonic_ms=2010, sampled_elapsed_ms=1010),
        command_clock=dict(sample_start_monotonic_ms=2020, sample_end_monotonic_ms=2030, sampled_elapsed_ms=1020),
        host_window=dict(original_ready_elapsed_ms=1000, preinput_monotonic_ms=2000,
                         host_completion_deadline_monotonic_ms=44000, driver_failure_bound_ms=36000,
                         product_client_bound_ms=30000, ready_age_bound_ms=2000, clock_reset_on_marker=False,
                         no_fault_injection=True, native_timeout_verified=False),
        touch_intent=dict(arguments=['shell','-T','input touchscreen tap 50 40'], package=records.APP, automatic_retry=False),
        touch_result=empty(), post_input_owner=dict(input_command_returned=True, native_cycle_acknowledged=False,
            observation_unavailable=False, original_instrumentation_alive=True, same_original_PID=True,
            app_resumed=True, same_owned_boot_identity=True),
        input_finish_signal=signal('owned-sync-quota-touch-'+scope['input_id']+'.finish'),
        failed_ready=dict(schema=1, phase=records.PHASE, parent_intent_id=scope['parent_id'], input_intent_id=scope['input_id'],
            package=records.APP, uid=scope['uid'], window=scope['window'], elapsed_ms=1100, scene_scan_elapsed_ms=1100,
            scene_counts=[1,1,1,1,0,0,0,0], public_alert_rule_id=records.RULE, failed_cycle_UI_observed=True,
            quota_error_UI_observed=True, raw_UI_recorded=False, native_original_state_verified=False),
        failed_clock=dict(sample_start_monotonic_ms=2100, sample_end_monotonic_ms=2110, sampled_elapsed_ms=1110),
        instrument_result=dict(returncode=0, unknown_result=False, automatic_retry=False,
                               early_cleanup_not_success=False, no_final_product_force_stop_or_reopen=True),
        final_clock=dict(driver_exit_seen_monotonic_ms=2120, final_owner_verified_monotonic_ms=2130),
        final_owner=final_owner,
        phase_result=dict(schema=1, phase=records.PHASE, label=records.LABEL, pid=life['pid'], passed=True,
            original_parent_intent_id=scope['parent_id'], original_input_intent_id=scope['input_id'],
            quota_error_UI_observed=True, native_original_state_verified=False, client_receipt_known=False))
    # Serialize once to remove fixture aliases before individual mutations.
    value = json.loads(encode(value))
    static = b'\n'.join(records.LINES)+b'\n'
    value['instrument_streams'] = dict(stdout_bytes=len(static), stdout_sha256=hashlib.sha256(static).hexdigest(),
        stderr_bytes=0, stderr_sha256=hashlib.sha256(b'').hexdigest(), raw_private_streams_exported=False)
    stop_id = '00000000-0000-0000-0000-000000009000'
    stop = dict(schema=1, phase=records.PHASE, scope=deepcopy(scope), stop_intent_id=stop_id,
        completion_metadata_canonical_sha256=hashlib.sha256(encode(value)).hexdigest(),
        original_lifetime=deepcopy(life), owned_boot=deepcopy(identity),
        intent=dict(arguments=['shell','-T','am force-stop --user 0 '+records.APP],
                    original_driver_completed=True, snapshot_stop_only=True, automatic_retry=False),
        before_command_owner=deepcopy(final_owner),
        at_command_owner=dict(deepcopy(final_owner), observed_after_private_command_intent=True),
        result=empty(), command_clock=dict(started_monotonic_ms=2200, returned_monotonic_ms=2210,
                                           observation_deadline_monotonic_ms=7200), observations=[])
    for index in (1,2):
        stop['observations'].append(dict(schema=1, sample_index=index,
            observation_id='00000000-0000-0000-0000-00000000900'+str(index), stop_intent_id=stop_id,
            package=records.APP, original_pid=life['pid'], product_uid=life['uid'], owned_boot=deepcopy(identity),
            root_uid=0, sample_start_monotonic_ms=2200+index*20, sample_end_monotonic_ms=2210+index*20,
            original_pid_absent=True, all_owned_package_processes_absent=True, user0_force_stopped=True,
            original_driver_completed=True, raw_process_list_exported=False, native_cycle_acknowledged=False))
    return value, static, stop


class QuotaOriginalRecords(unittest.TestCase):
    def test_complete_immediate_quota_source_context_and_independent_projection(self):
        value, static, _ = fixture(); before = deepcopy(value)
        result = records.validate_completion(value, static)
        self.assertEqual(value, before)
        self.assertTrue(result['completed_driver_reported'])
        self.assertTrue(result['full_quota_scene_reported'])
        self.assertFalse(result['runtime_verified']); self.assertFalse(result['Native_cause_verified'])
        self.assertFalse(result['native_apply_ACK']); self.assertFalse(result['live_deadlines_independently_verified'])
        self.assertLess(value['failed_ready']['elapsed_ms']-value['ready']['elapsed_ms'], 30000)
        self.assertEqual(result['original_metadata_canonical_sha256'], hashlib.sha256(encode(value)).hexdigest())
        value['scope']['uid'] = 1
        self.assertNotEqual(result['scope']['uid'], 1)

    def test_complete_stop_reports_not_live_collector_admission_or_independent_syscalls(self):
        value, static, stop = fixture(); saved = deepcopy(stop)
        result = records.validate_snapshot_stop(value, static, stop)
        self.assertEqual(stop, saved)
        self.assertTrue(result['two_distinct_stopped_samples_reported'])
        for name in ('actual_separate_syscalls_verified','collector_bracketing_samples_verified',
                     'original_files_independently_verified','runtime_verified','native_apply_ACK'):
            self.assertFalse(result[name])

    def test_context_handshake_required_canonical_hash_scope_and_signal_path(self):
        for target in ('context', 'context_write', 'context_ready_signal', 'input_finish_signal'):
            value, static, _ = fixture()
            del value[target]
            with self.subTest(target=target), self.assertRaises(ValueError):
                records.validate_completion(value, static)
        for mutate in (
            lambda v: v['context'].update(device_id='f'*32),
            lambda v: v['context_write'].update(context_file_sha256='f'*64),
            lambda v: v['context_ready_signal']['intent'].update(arguments=['shell','-T','legacy ready']),
            lambda v: v['context_ready_signal']['result'].update(returncode=1),
            lambda v: v['context_ready_signal']['readback'].update(exact_static_bytes=None)):
            value, static, _ = fixture(); mutate(value)
            with self.assertRaises(ValueError):
                records.validate_completion(value, static)

    def test_wrong_product_owner_parent_input_phase_and_legacy_refused(self):
        for group, key, bad in (
            ('scope','product','0'*40),('scope','owner_run','99999-1'),('scope','uid',True),
            ('scope','android_device','f'*32),('parent','input_intent_id','00000000-0000-0000-0000-000000009999'),
            ('parent','phase','cycle-timeout-original'),('ready','phase','cycle-after-timeout-original'),
            ('phase_result','label','timeout-original'),('original_lifetime','uid',10001)):
            value, static, _ = fixture(); value[group][key] = bad
            with self.subTest(group=group,key=key), self.assertRaises(ValueError):
                records.validate_completion(value, static)

    def test_ready_geometry_types_exact_hit_and_age_guards(self):
        for key, bad in (('original',[0,0,20,20]),('visible',[0,0,101,80]),('visible',[0,0,0,0]),
                         ('visible',[0,0,True,80]),('x',50.0),('y',False),('uid',True),('window',65536)):
            value, static, _ = fixture(); value['ready'][key] = bad
            with self.subTest(key=key,bad=bad), self.assertRaises(ValueError):
                records.validate_completion(value, static)
        for owner in ('input_owner','input_owner_at_command'):
            for bad in (-1,2001,None,True):
                value, static, _ = fixture(); value[owner]['ready_age_ms'] = bad
                with self.assertRaises(ValueError):
                    records.validate_completion(value, static)

    def test_full_same_scan_counts_rule_and_private_partial_not_admitted(self):
        for key, bad in (('scene_counts',[1,1,1,0,0,0,0,0]),('scene_counts',[1,1,1,2,0,0,0,0]),
                         ('scene_counts',[1,1,1,True,0,0,0,0]),('scene_scan_elapsed_ms',1099),
                         ('public_alert_rule_id','QUOTA_EXCEEDED'),('public_alert_rule_id','complete-public-alert-ascii-ws-v1'),
                         ('quota_error_UI_observed',None),('raw_UI_recorded',True)):
            value, static, _ = fixture(); value['failed_ready'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                records.validate_completion(value, static)
        value, static, _ = fixture(); value['failed_ready']['warning_text'] = 'synthetic-private'
        with self.assertRaises(ValueError):
            records.validate_completion(value, static)

    def test_clock_order_long_overflow_and_no_late_tolerance(self):
        for group, key, bad in (
            ('ready','elapsed_ms',records.MAX),('input_clock','sample_end_monotonic_ms',1999),
            ('input_clock','sample_end_monotonic_ms',3001),('input_clock','sample_start_monotonic_ms',records.MAX),
            ('command_clock','sampled_elapsed_ms',1019),('command_clock','sample_start_monotonic_ms',2009),
            ('failed_clock','sample_start_monotonic_ms',2029),('failed_clock','sampled_elapsed_ms',3101),
            ('final_clock','final_owner_verified_monotonic_ms',44001),
            ('host_window','clock_reset_on_marker',True),('host_window','driver_failure_bound_ms',36001)):
            value, static, _ = fixture(); value[group][key] = bad
            with self.subTest(group=group,key=key), self.assertRaises(ValueError):
                records.validate_completion(value, static)

    def test_exact_failure_and_freshness_endpoints_and_large_long_domains(self):
        value, static, _ = fixture()
        value['failed_ready'].update(elapsed_ms=37000,scene_scan_elapsed_ms=37000)
        value['failed_clock']['sampled_elapsed_ms'] = 39000
        records.validate_completion(value, static)
        value['failed_ready'].update(elapsed_ms=37001,scene_scan_elapsed_ms=37001)
        with self.assertRaises(ValueError):
            records.validate_completion(value, static)
        value, static, _ = fixture()
        shift = records.MAX-42000-2000
        for group in ('input_clock','command_clock','failed_clock'):
            for key in ('sample_start_monotonic_ms','sample_end_monotonic_ms'):
                value[group][key] += shift
        for key in value['final_clock']:
            value['final_clock'][key] += shift
        value['host_window']['preinput_monotonic_ms'] += shift
        value['host_window']['host_completion_deadline_monotonic_ms'] += shift
        records.validate_completion(value, static)

    def test_original_command_owner_unknown_results_and_postinput_fail_closed(self):
        for group, key, bad in (
            ('input_owner','original_instrumentation_alive',False),
            ('input_owner_at_command','observed_after_private_command_intent',None),
            ('touch_result','unknown_result',True),('touch_result','returncode',False),
            ('touch_result','device_command_invoked',False),('post_input_owner','same_original_PID',False),
            ('post_input_owner','app_resumed',False),('final_owner','root_uid',False),
            ('final_owner','original_driver_completed',False)):
            value, static, _ = fixture(); value[group][key] = bad
            with self.subTest(group=group,key=key), self.assertRaises(ValueError):
                records.validate_completion(value, static)

    def test_static_driver_completion_unknown_early_cleanup_or_extra_stream_refused(self):
        for key in ('unknown_result','automatic_retry','early_cleanup_not_success'):
            value, static, _ = fixture(); value['instrument_result'][key] = True
            with self.assertRaises(ValueError):
                records.validate_completion(value, static)
        for static in (b'',b'private-output\n',b'\xff',b'\n'.join(records.LINES[::-1]), b'\n'.join(records.LINES)+b'\nextra\n'):
            value, _, _ = fixture()
            with self.assertRaises(ValueError):
                records.validate_completion(value, static)
        value, static, _ = fixture()
        wrapped = b'INSTRUMENTATION_STATUS: stream='+records.LINES[0]+b'\nINSTRUMENTATION_STATUS_CODE: 1\n'+records.LINES[1]+b'\nINSTRUMENTATION_CODE: -1\n'
        value['instrument_streams'].update(stdout_bytes=len(wrapped),stdout_sha256=hashlib.sha256(wrapped).hexdigest())
        records.validate_completion(value, wrapped)

    def test_instrument_stream_metadata_exact_empty_error_and_approved_stdout_digest(self):
        for key, bad in (('stderr_bytes',1),('stderr_bytes',False),('stderr_sha256','f'*64),
                         ('stdout_bytes',1),('stdout_sha256','f'*64),('raw_private_streams_exported',True)):
            value, static, _ = fixture(); value['instrument_streams'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                records.validate_completion(value,static)
        value, static, _ = fixture(); del value['instrument_streams']
        with self.assertRaises(ValueError):
            records.validate_completion(value,static)

    def test_stop_hash_scope_command_and_original_owner_cannot_be_substituted(self):
        for group, key, bad in (
            (None,'completion_metadata_canonical_sha256','f'*64),(None,'stop_intent_id',None),
            ('intent','arguments',['shell','-T','am force-stop --user 1 '+records.APP]),
            ('intent','original_driver_completed',False),('before_command_owner','app_resumed',False),
            ('at_command_owner','observed_after_private_command_intent',None),
            ('result','unknown_result',True),('result','automatic_retry',True),('result','stdout_bytes',True)):
            value, static, stop = fixture()
            (stop if group is None else stop[group])[key] = bad
            with self.subTest(group=group,key=key), self.assertRaises(ValueError):
                records.validate_snapshot_stop(value, static, stop)
        value, static, stop = fixture(); stop['scope']['save_operation_id'] = stop['scope']['setup_operation_id']
        with self.assertRaises(ValueError):
            records.validate_snapshot_stop(value, static, stop)

    def test_two_stop_samples_exact_identity_indices_absence_and_no_shared_id(self):
        for key, bad in (('sample_index',1),('observation_id',None),('original_pid_absent',None),
                         ('all_owned_package_processes_absent',False),('user0_force_stopped',False),
                         ('root_uid',False),('product_uid',10001),('raw_process_list_exported',True)):
            value, static, stop = fixture(); stop['observations'][1][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                records.validate_snapshot_stop(value, static, stop)
        value, static, stop = fixture(); stop['observations'][1] = deepcopy(stop['observations'][0])
        with self.assertRaises(ValueError):
            records.validate_snapshot_stop(value, static, stop)
        value, static, stop = fixture()
        stop['observations'][1]['sample_start_monotonic_ms'] = stop['observations'][0]['sample_end_monotonic_ms']
        records.validate_snapshot_stop(value, static, stop)

    def test_stop2s_command5s_observation_endpoints_overflow_and_reused_intent(self):
        value, static, stop = fixture()
        stop['command_clock']['returned_monotonic_ms'] = 4200
        for sample in stop['observations']:
            sample.update(sample_start_monotonic_ms=7200,sample_end_monotonic_ms=7200)
        records.validate_snapshot_stop(value, static, stop)
        for kind in ('command-late','sample-late','reused','overflow'):
            value, static, stop = fixture()
            if kind == 'command-late':
                stop['command_clock']['returned_monotonic_ms'] = 4201
            elif kind == 'sample-late':
                stop['observations'][1]['sample_end_monotonic_ms'] = 7201
            elif kind == 'reused':
                stop['stop_intent_id'] = value['scope']['parent_id']
            else:
                stop['command_clock'].update(started_monotonic_ms=records.MAX,returned_monotonic_ms=records.MAX,
                                             observation_deadline_monotonic_ms=records.MAX)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                records.validate_snapshot_stop(value, static, stop)

    def test_strict_JSON_duplicate_private_nonfinite_type_utf8_and_null_stop(self):
        value, static, stop = fixture(); bundle = dict(schema=1,completion=value,stop=stop)
        records.parse_original_records(encode(bundle),static,require_stop=True)
        for raw in (b'{"schema":1,"schema":1,"completion":{},"stop":null}',b'\xff',b'{"schema":NaN}',
                    encode(dict(bundle,private='synthetic')),encode(dict(bundle,schema=True)),encode(dict(bundle,schema=1.0))):
            with self.assertRaises(ValueError):
                records.parse_original_records(raw,static)
        bundle['stop'] = None
        records.parse_original_records(encode(bundle),static)
        with self.assertRaises(ValueError):
            records.parse_original_records(encode(bundle),static,require_stop=True)
        with self.assertRaises(ValueError):
            records.parse_original_records(encode(bundle),static,require_stop=1)

    def test_whole_bundle32768_boundary_and_unknown_objects_never_coerced(self):
        value, static, stop = fixture(); raw = encode(dict(schema=1,completion=value,stop=stop))
        self.assertLess(len(raw), records.MAX_BUNDLE)
        records.parse_original_records(raw+b' '*(records.MAX_BUNDLE-len(raw)),static,require_stop=True)
        with self.assertRaises(ValueError):
            records.parse_original_records(raw+b' '*(records.MAX_BUNDLE+1-len(raw)),static)
        class Unknown:
            def __str__(self): raise AssertionError('Unknown formatted')
            def __bool__(self): raise AssertionError('Unknown coerced')
            def __eq__(self,_): raise AssertionError('Unknown compared')
            def __hash__(self): raise AssertionError('Unknown hashed')
        for group, key in (('scope','uid'),('ready','elapsed_ms'),('context_write','context_file_sha256'),
                           ('input_owner','ready_age_ms'),('phase_result','passed')):
            value, static, _ = fixture(); value[group][key] = Unknown()
            with self.assertRaises(ValueError):
                records.validate_completion(value,static)

    def test_original_exclusive_record_buffers_and_canonical_metadata_are_distinct(self):
        value, static, stop = fixture()
        with tempfile.TemporaryDirectory(prefix='opennexus-quota-records-', dir=os.getenv('OPENNEXUS_QUOTA_RECORDS_TEST_ROOT')) as folder:
            root = Path(folder); self.assertEqual(root,root.resolve())
            reconstructed = {}
            for key, part in value.items():
                path = root/(key+'.json'); original_record(path,part)
                raw = path.read_bytes(); self.assertLessEqual(len(raw),4096)
                reconstructed[key] = json.loads(raw)
                with self.assertRaises(FileExistsError):
                    original_record(path,part)
                self.assertEqual(path.read_bytes(),raw)
            projected = records.validate_snapshot_stop(reconstructed,static,stop)
            self.assertFalse(projected['original_files_independently_verified'])
            ready_raw = (root/'ready.json').read_bytes()
            self.assertNotEqual(hashlib.sha256(ready_raw).hexdigest(),hashlib.sha256(encode(value['ready'])).hexdigest())
