"""Continuation-only synthetic ports; no real ADB, child process or Native."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

from android_release_observation import exclusive
import android_release_sync_quota_host_completion as host
import android_release_sync_quota_records as records
from test_android_release_sync_quota_records import fixture


SUFFIX = ('failed_ready','failed_clock','instrument_result','instrument_streams',
          'final_clock','final_owner','phase_result')


class Unknown:
    def __str__(self):raise AssertionError('Unknown formatted')
    def __repr__(self):raise AssertionError('Unknown represented')
    def __eq__(self,other):raise AssertionError('Unknown compared')
    def __hash__(self):raise AssertionError('Unknown hashed')


class Ports(host.OriginalQuotaCompletionPorts):
    def __init__(self,directory):
        self.directory = directory;self.data,self.stdout,_ = fixture()
        self.wrapper = dict(schema=1,records={k:deepcopy(v) for k,v in self.data.items() if k not in SUFFIX},
            input_prefix_only=True,completed_driver_reported=False,original_files_independently_verified=False,
            live_deadlines_independently_verified=False,native_cycle_acknowledged=False,runtime_verified=False)
        self.events = [];self.now = 2040;self.polls = [0];self.index = 0;self.owner_count = 0
        self.err = b'';self.bad_streams = None;self.failed = deepcopy(self.data['failed_ready'])
        self.sample = deepcopy(self.data['failed_clock']);self.owner_change = None
        self.raise_at = self.close_at = self.reenter_at = None;self.actor = None
        self.failure = OSError('controlled unknown original result');self.record_result = None
        self.wait_advance = 50;self.poll_advance = 10;self.owner_advance = 0

    def event(self,name):
        self.events.append(name)
        if self.close_at == name:self.actor.close()
        if self.reenter_at == name:
            try:self.actor.run()
            except RuntimeError:pass
        if self.raise_at == name:raise self.failure

    def monotonic_ms(self):
        self.event('mono');return self.now

    def product_owner(self):
        self.owner_count += 1;self.event('product-owner');self.now += self.owner_advance
        value = dict(original_lifetime=deepcopy(self.data['original_lifetime']),
            owned_boot=deepcopy(self.data['identity']),same_original_foreground_PID_UID_boot=True,
            app_resumed=True,root_uid=0,managed_driver_not_sampled=True,native_cycle_acknowledged=False)
        if self.owner_change is not None:value.update(self.owner_change)
        return value

    def wait_failed(self,path,deadline_ms,owner):
        self.event('wait-failed')
        if path != 'no_backup/owned-sync-quota-'+records.PHASE+'-failed-ready.json':raise AssertionError('Wrong file')
        if deadline_ms != 40000:raise AssertionError('Failure wait reset')
        owner();self.now = 2100;owner()
        return self.failed

    def clock(self):
        self.event('failure-clock');self.now = self.sample['sample_end_monotonic_ms']
        return self.sample

    def poll_driver(self):
        self.event('poll-driver');self.now += self.poll_advance
        result = self.polls[min(self.index,len(self.polls)-1)];self.index += 1
        return result

    def wait_turn(self,*,maximum_ms,deadline_ms):
        self.event('host-wait')
        if maximum_ms != 50 or deadline_ms != 44000:raise AssertionError('New deadline or wider wait')
        self.now += self.wait_advance

    def streams(self):
        self.event('streams')
        return self.bad_streams if self.bad_streams is not None else (self.stdout,self.err)

    def record(self,name,raw):
        self.event('record-'+name);exclusive(self.directory/(name+'.json'),raw)
        return self.record_result


class QuotaHostCompletion(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ['ONX_QUOTA_HOST_COMPLETION_ROOT'])
        self.assertTrue(parent.is_absolute() and parent.is_dir())
        self.temp = tempfile.TemporaryDirectory(prefix='case-',dir=parent)
        self.addCleanup(self.temp.cleanup);self.ports = Ports(Path(self.temp.name))

    def actor(self,wrapper=None):
        self.ports.actor = host.OriginalQuotaHostCompletion(self.ports,
            input_prefix=self.ports.wrapper if wrapper is None else wrapper)
        return self.ports.actor

    def refused(self):
        with self.assertRaises((RuntimeError,ValueError)):self.actor().run()

    def test_original_exclusive_writer_and_full_closed_completion_order(self):
        before = deepcopy(self.ports.wrapper);actor = self.actor();result = actor.run()
        events = self.ports.events
        for a,b in [('wait-failed','failure-clock'),('failure-clock','poll-driver'),
                    ('poll-driver','streams'),('streams','record-failed-ready')]:
            self.assertLess(events.index(a),events.index(b))
        self.assertEqual(events.count('failure-clock'),1);self.assertEqual(events.count('poll-driver'),1)
        self.assertEqual(events.count('streams'),1);self.assertEqual(len(list(self.ports.directory.iterdir())),8)
        self.assertEqual(result['original_stdout'],self.ports.stdout)
        projection = records.validate_completion(result['records'],result['original_stdout'])
        self.assertEqual(projection,result['completion_projection']);self.assertTrue(projection['completed_driver_reported'])
        self.assertFalse(result['runtime_verified']);self.assertFalse(result['owned_stop_verified'])
        self.assertFalse(projection['Native_cause_verified']);self.assertFalse(projection['native_apply_ACK'])
        self.assertEqual(self.ports.wrapper,before)
        count = len(events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(len(events),count)

    def test_driver_already_finished_during_failed_read_is_first_polled_after_clock(self):
        # Local child existence here is synthetic; the readonly guards never poll it.
        self.ports.polls = [0];result = self.actor().run()
        self.assertEqual(self.ports.events.count('poll-driver'),1)
        at = result['records']['final_clock']['driver_exit_seen_monotonic_ms']
        self.assertEqual(at,2120)
        self.assertGreaterEqual(at,result['records']['failed_clock']['sample_end_monotonic_ms'])

    def test_pending_driver_uses_only_bounded_host_wait_and_readonly_guard(self):
        self.ports.polls = [None,0];result = self.actor().run()
        self.assertEqual(self.ports.events.count('host-wait'),1)
        self.assertEqual(self.ports.events.count('failure-clock'),1)
        self.assertEqual(result['records']['final_clock']['driver_exit_seen_monotonic_ms'],2180)

    def test_supplied_prefix_is_closed_before_any_callback(self):
        for key,value in [('private',Unknown()),('runtime_verified',True),('schema',True)]:
            wrapper = deepcopy(self.ports.wrapper);wrapper[key] = value
            with self.assertRaises((RuntimeError,ValueError)):self.actor(wrapper)
        wrapper = deepcopy(self.ports.wrapper);wrapper['records']['context_write']['context_file_sha256'] = Unknown()
        with self.assertRaises((RuntimeError,ValueError)):self.actor(wrapper)
        self.assertEqual(self.ports.events,[])

    def test_wrong_prefix_identity_geometry_and_clock_never_repaired(self):
        for group,key,value in [('scope','window',True),('ready','x',Unknown()),
            ('original_lifetime','uid',10001),('input_clock','sample_end_monotonic_ms',5000),
            ('input_finish_signal','readback',None),('input_owner','original_instrumentation_alive',None)]:
            wrapper = deepcopy(self.ports.wrapper);wrapper['records'][group][key] = value
            with self.assertRaises((RuntimeError,ValueError)):self.actor(wrapper)
        self.assertEqual(self.ports.events,[])

    def test_complete_invalid_failed_ready_refuses_before_clock_or_poll(self):
        self.ports.failed['scene_counts'] = [1,1,1,0,0,0,0,0];self.refused()
        self.assertNotIn('failure-clock',self.ports.events);self.assertNotIn('poll-driver',self.ports.events)
        self.assertEqual(self.ports.events.count('wait-failed'),1)

    def test_missing_wait_result_never_becomes_absence_or_success(self):
        self.ports.failed = None;self.refused()
        self.assertNotIn('failure-clock',self.ports.events)
        self.assertEqual(list(self.ports.directory.iterdir()),[])

    def test_wrong_failed_scope_private_unknown_and_boolean_are_terminal(self):
        for key,value in [('uid',True),('window',99),('private',Unknown()),('elapsed_ms',Unknown())]:
            self.ports.failed = deepcopy(self.ports.data['failed_ready']);self.ports.failed[key] = value
            self.refused()
        self.assertNotIn('failure-clock',self.ports.events);self.assertNotIn('streams',self.ports.events)

    def test_failure_clock_stale_reverse_boolean_and_long_refuse_before_poll(self):
        for key,value in [('sampled_elapsed_ms',3101),('sample_end_monotonic_ms',2099),
                          ('sample_start_monotonic_ms',True),('sampled_elapsed_ms',records.MAX+1)]:
            self.ports.now = 2040;self.ports.sample = deepcopy(self.ports.data['failed_clock']);self.ports.sample[key] = value
            self.refused()
        self.assertNotIn('poll-driver',self.ports.events)

    def test_managed_nonzero_bool_or_unknown_never_reads_private_stream(self):
        for code in [1,True,Unknown()]:
            self.ports.now = 2040;self.ports.polls = [code];self.refused()
        self.assertNotIn('streams',self.ports.events);self.assertEqual(list(self.ports.directory.iterdir()),[])

    def test_private_stream_and_nonempty_stderr_never_hash_or_record(self):
        self.ports.stdout += b'synthetic-private\n';self.refused()
        self.assertFalse(any(e.startswith('record-') for e in self.ports.events))

    def test_unknown_stream_object_or_stderr_rejected_without_formatting(self):
        for streams in [(Unknown(),b''),(self.ports.stdout,Unknown()),(self.ports.stdout,b'x')]:
            self.ports.now = 2040;self.ports.bad_streams = streams;self.refused()
        self.assertFalse(any(e.startswith('record-') for e in self.ports.events))

    def test_readonly_owner_cannot_claim_driver_alive_or_other_product(self):
        self.ports.owner_change = dict(managed_driver_not_sampled=False);self.refused()
        self.assertNotIn('failure-clock',self.ports.events)
        self.ports.now = 2040;self.ports.owner_change = dict(original_lifetime=dict(self.ports.data['original_lifetime'],pid=9999))
        self.refused();self.assertNotIn('streams',self.ports.events)

    def test_final_owner_change_after_valid_stream_cannot_record_success(self):
        original = self.ports.streams
        def streams():
            result = original();self.ports.owner_change = dict(root_uid=1);return result
        self.ports.streams = streams;self.refused()
        self.assertIn('streams',self.ports.events)
        self.assertFalse(any(e.startswith('record-') for e in self.ports.events))

    def test_no_deadline_reset_or_slow_host_wait(self):
        self.ports.polls = [None,0];self.ports.wait_advance = 51;self.refused()
        self.assertNotIn('streams',self.ports.events)

    def test_stalled_host_clock_has_finite_poll_bound(self):
        self.ports.polls = [None];self.ports.poll_advance = self.ports.wait_advance = 0;self.refused()
        self.assertEqual(self.ports.events.count('poll-driver'),841)
        self.assertNotIn('streams',self.ports.events)

    def test_observation_after_original_42_second_deadline_is_not_clamped(self):
        self.ports.poll_advance = 42000;self.refused()
        self.assertNotIn('streams',self.ports.events)

    def test_exact_failure_36_second_and_host_42_second_endpoints(self):
        self.ports.failed.update(elapsed_ms=37000,scene_scan_elapsed_ms=37000)
        self.ports.sample.update(sample_start_monotonic_ms=40000,
            sample_end_monotonic_ms=40010,sampled_elapsed_ms=39000)
        self.ports.poll_advance = 3990
        result = self.actor().run()
        self.assertEqual(result['records']['final_clock'],dict(driver_exit_seen_monotonic_ms=44000,
            final_owner_verified_monotonic_ms=44000))
        self.assertFalse(result['runtime_verified'])

    def test_final_owner_after_deadline_and_oversized_static_stream_refuse(self):
        original = self.ports.streams
        def slow_owner():
            value = original();self.ports.owner_advance = 44000;return value
        self.ports.streams = slow_owner;self.refused()
        self.assertFalse(any(e.startswith('record-') for e in self.ports.events))
        self.ports.now = 2040;self.ports.owner_advance = 0;self.ports.streams = original
        self.ports.stdout += b'\n'*4096;self.refused()
        self.assertFalse(any(e.startswith('record-') for e in self.ports.events))

    def test_unknown_original_error_preserved_without_followup(self):
        self.ports.raise_at = 'streams';actor = self.actor()
        with self.assertRaises(OSError) as caught:actor.run()
        self.assertIs(caught.exception,self.ports.failure)
        self.assertEqual(self.ports.events[-1],'streams')
        count = len(self.ports.events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(len(self.ports.events),count)

    def test_swallowed_reentry_is_terminal_before_streams(self):
        self.ports.reenter_at = 'poll-driver';self.refused()
        self.assertEqual(self.ports.events[-1],'poll-driver')
        self.assertNotIn('streams',self.ports.events)

    def test_callback_close_never_triggers_final_owner_or_artifact(self):
        self.ports.close_at = 'streams';self.refused()
        self.assertEqual(self.ports.events[-1],'streams')

    def test_existing_local_record_preserved_without_retry_or_failure_snapshot(self):
        file = self.ports.directory/'failed-ready.json';exclusive(file,b'original')
        with self.assertRaises(FileExistsError):self.actor().run()
        self.assertEqual(file.read_bytes(),b'original')
        self.assertEqual(self.ports.events[-1],'record-failed-ready')
        self.assertEqual(len(list(self.ports.directory.iterdir())),1)

    def test_unknown_writer_return_after_write_has_no_second_write(self):
        self.ports.record_result = False;self.refused()
        self.assertEqual(self.ports.events[-1],'record-failed-ready')
        self.assertEqual(len(list(self.ports.directory.iterdir())),1)

    def test_input_and_returned_arrays_have_no_caller_aliases(self):
        actor = self.actor();self.ports.wrapper['records']['ready']['original'][0] = -500
        result = actor.run();self.assertEqual(result['records']['ready']['original'],[0,0,100,80])
        self.ports.failed['scene_counts'][0] = 9
        self.assertEqual(result['records']['failed_ready']['scene_counts'],[1,1,1,1,0,0,0,0])


if __name__ == '__main__':unittest.main()
