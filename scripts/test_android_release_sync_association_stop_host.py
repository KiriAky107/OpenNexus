"""Source-owned handoff and controlled Stop reports, never real Stop/Native."""
from copy import copy, deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import android_release_sync_association_host as host
import android_release_sync_association_stop_host as stop_host
import android_release_sync_association_records as records
from test_android_release_sync_association_host import make as make_completion, Ports as CompletionPorts
from test_android_release_sync_association_records import identifier


class Ports(stop_host.OriginalAssociationStopPorts):
    def __init__(self, original, issuer):
        self.original,self.issuer=original,issuer
        self.time=original.time;self.step=1;self.clock_values=[];self.events=[];self.hooks={};self.files={}
        self.admit_result=self.intent_result=self.record_result=None
        self.guard_mode='once';self.response=dict(returncode=0,unknown_result=False,stdout=b'',stderr=b'')
        self.stop_delay=0;self.bad_facts={};self.guard=None;self.sample_arguments=[]

    def event(self,name):
        self.events.append(name)
        if name in self.hooks:self.hooks[name]()

    def admit(self, original_ports, **metadata):
        self.event('admit')
        if original_ports is not self.original:raise ValueError('Wrong private source')
        self.admission=deepcopy(metadata)
        return self.admit_result

    def monotonic_ms(self):
        self.event('clock')
        if self.clock_values:return self.clock_values.pop(0)
        self.time+=self.step;return self.time

    def product_owner(self):
        self.event('owner')
        return dict(original_lifetime=deepcopy(self.original.context['lifetime']),owned_boot=deepcopy(self.original.context['identity']),
            same_original_foreground_PID_UID_boot=True,app_resumed=True,root_uid=0,native_cycle_acknowledged=False)

    def record_intent(self,raw):
        self.event('intent')
        if 'intent' in self.files:raise FileExistsError('Existing original intent')
        self.files['intent']=raw;return self.intent_result

    def stop(self,arguments,before_send,**limits):
        self.event('stop');self.arguments=list(arguments);self.stop_limits=limits;self.guard=before_send
        if self.guard_mode=='once':before_send(list(arguments))
        elif self.guard_mode=='twice':before_send(list(arguments));before_send(list(arguments))
        elif self.guard_mode in ('bad','swallowed'):
            if self.guard_mode=='bad':before_send(['changed command'])
            else:
                try:before_send(['changed command'])
                except RuntimeError:pass
        self.command_start=self.time;self.time+=self.stop_delay
        return self.response

    def observe_stopped(self,index,observation_id,**context):
        self.event('sample'+str(index));self.sample_arguments.append((index,observation_id,deepcopy(context)))
        facts=dict(sample_index=index,observation_id=observation_id,original_pid=context['lifetime']['pid'],
            product_uid=context['lifetime']['uid'],owned_boot=context['identity'],root_uid=0,
            original_pid_absent=True,all_owned_package_processes_absent=True,user0_force_stopped=True,raw_process_list_exported=False)
        facts.update(self.bad_facts.get(index,{}));return facts

    def record(self,raw):
        self.event('record')
        if 'record' in self.files:raise FileExistsError('Existing stopped report')
        self.files['record']=raw;return self.record_result


def prepare():
    original,issuer=make_completion();report=issuer.run();lease=issuer.handoff();ports=Ports(original,issuer)
    return original,issuer,report,lease,ports


def make(ports,lease,**overrides):
    args=dict(handoff=lease,stop_intent_id=identifier(8),observation_ids=(identifier(9),identifier(10)))
    args.update(overrides);return stop_host.OriginalAssociationHostStop(ports,**args)


class AssociationOwnedStop(unittest.TestCase):
    def refused(self,ports,actor):
        with self.assertRaisesRegex(RuntimeError,'^SIGNED_ASSOCIATION_HOST_STOP_UNAVAILABLE$'):actor.run()
        events=list(ports.events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(events,ports.events);self.assertEqual(actor._state,'FAILED')

    def test_handoff_keeps_exact_original_writer_buffer_and_observations_without_calls(self):
        original,issuer=make_completion();report=issuer.run();events=list(original.events)
        raw=original.files['signed-association-bootstrap-completion.json']
        self.assertIs(issuer._writer_buffer,raw);self.assertEqual(issuer._writer_guard,original.last_clock)
        lease=issuer.handoff();self.assertEqual(events,original.events)
        ports=Ports(original,issuer);actor=make(ports,lease)
        self.assertEqual(ports.events,[]);self.assertIs(actor._completion_raw,raw)
        self.assertEqual(actor._writer_guard,original.last_clock)
        self.assertIs(actor._buffers[0],original.intent)
        self.assertEqual(actor._completion['driver']['first_exit_seen_monotonic_ms'],report['completion']['driver']['first_exit_seen_monotonic_ms'])

    def test_caller_returned_report_mutation_does_not_reconstruct_original(self):
        original,issuer=make_completion();report=issuer.run();report['completion']['original_lifetime']['pid']=99
        report['completion']['driver']['first_exit_seen_monotonic_ms']=999999
        ports=Ports(original,issuer);actor=make(ports,issuer.handoff());result=actor.run()
        self.assertEqual(result['records']['original_lifetime']['pid'],1234)
        self.assertNotEqual(result['records']['driver']['first_exit_seen_monotonic_ms'],999999)

    def test_only_DONE_writer_and_deadline_success_may_issue_handoff(self):
        original,issuer=make_completion()
        with self.assertRaises(RuntimeError):issuer.handoff()
        self.assertEqual(original.events,[])
        original=CompletionPorts();original.write_result=True
        original,issuer=make_completion(original)
        with self.assertRaises(RuntimeError):issuer.run()
        events=list(original.events)
        with self.assertRaises(RuntimeError):issuer.handoff()
        self.assertEqual(events,original.events);self.assertIsNone(issuer._writer_buffer)

    def test_double_handoff_or_claim_burns_source_permission_without_ports(self):
        _,issuer,_,lease,ports=prepare()
        with self.assertRaises(RuntimeError):issuer.handoff()
        with self.assertRaises(RuntimeError):make(ports,lease)
        self.assertEqual(ports.events,[])
        _,issuer,_,lease,ports=prepare();actor=make(ports,lease)
        with self.assertRaises(RuntimeError):make(ports,lease)
        self.refused(ports,actor);self.assertEqual(ports.events,[])

    def test_foreign_copy_unissued_or_metadata_token_is_not_a_source_handoff(self):
        with self.assertRaises(RuntimeError):host.OriginalAssociationCompletionHandoff(object(),object())
        for token in ({'passed':True},'digest',object(),object.__new__(host.OriginalAssociationCompletionHandoff)):
            original,issuer=make_completion();ports=Ports(original,issuer)
            with self.assertRaises(RuntimeError):make(ports,token)
            self.assertEqual(ports.events,[])
        _,_,_,lease,ports=prepare()
        with self.assertRaises(RuntimeError):make(ports,copy(lease))
        self.assertEqual(ports.events,[])

    def test_bad_constructor_ID_or_ports_consumes_once_before_any_action(self):
        cases=[dict(stop_intent_id=identifier(5)),dict(stop_intent_id=True),
               dict(observation_ids=(identifier(8),identifier(9))),dict(observation_ids=(identifier(9),identifier(9))),
               dict(observation_ids=[identifier(9),identifier(10)])]
        for kwargs in cases:
            _,issuer,_,lease,ports=prepare()
            with self.assertRaises(RuntimeError):make(ports,lease,**kwargs)
            with self.assertRaises(RuntimeError):make(ports,lease)
            self.assertEqual(ports.events,[]);self.assertEqual(issuer._state,'FAILED')
        _,issuer,_,lease,ports=prepare()
        with self.assertRaises(RuntimeError):make(object(),lease)
        self.assertEqual(issuer._state,'FAILED');self.assertEqual(ports.events,[])

    def test_success_one_stop_two_ordered_reports_and_all_independence_false(self):
        original,issuer,report,lease,ports=prepare();old_events=list(original.events)
        result=make(ports,lease).run();events=ports.events
        self.assertEqual(events.count('stop'),1);self.assertEqual(events.count('intent'),1);self.assertEqual(events.count('record'),1)
        self.assertLess(events.index('intent'),events.index('stop'));self.assertLess(events.index('stop'),events.index('sample1'))
        self.assertLess(events.index('sample1'),events.index('sample2'));self.assertLess(events.index('sample2'),events.index('record'))
        self.assertEqual(ports.stop_limits,dict(stop_intent_id=identifier(8),timeout_ms=2000))
        self.assertEqual([x[:2] for x in ports.sample_arguments],[(1,identifier(9)),(2,identifier(10))])
        stop=result['records']['stop'];self.assertEqual(stop['command_clock']['observation_deadline_monotonic_ms'],stop['command_clock']['started_monotonic_ms']+5000)
        self.assertEqual(result['records']['driver'],report['completion']['driver'])
        self.assertEqual(original.events,old_events);self.assertEqual(issuer._state,'STOP_REPORTED')
        self.assertTrue(result['source_owned_single_use_handoff_reported'])
        for k in ('actual_private_factory_bound','actual_separate_syscalls_verified','collector_bracketing_samples_verified','Native_or_client_ACK','runtime_verified'):
            self.assertIs(result[k],False)
        self.assertFalse(result['correlation']['actual_separate_syscalls_verified'])
        self.assertEqual(json.loads(ports.files['record']),result)

    def test_source_admission_and_private_intent_unknown_refuse_before_stop(self):
        for field,last in [('admit_result','admit'),('intent_result','intent')]:
            _,_,_,lease,ports=prepare();setattr(ports,field,True);actor=make(ports,lease);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],last);self.assertNotIn('stop',ports.events)
        _,_,_,lease,ports=prepare();ports.files['intent']=b'original';actor=make(ports,lease);self.refused(ports,actor)
        self.assertEqual(ports.files['intent'],b'original');self.assertNotIn('stop',ports.events)

    def test_guard_missing_repeated_bad_swallowed_or_reentrant_never_retries(self):
        for mode in ('missing','twice','bad','swallowed'):
            _,_,_,lease,ports=prepare();ports.guard_mode=mode;actor=make(ports,lease);self.refused(ports,actor)
            self.assertEqual(ports.events.count('stop'),1);self.assertNotIn('sample1',ports.events)
        _,_,_,lease,ports=prepare();actor=make(ports,lease)
        def reenter():
            try:actor.run()
            except RuntimeError:pass
        ports.hooks['stop']=reenter;self.refused(ports,actor);self.assertNotIn('sample1',ports.events)

    def test_late_original_driver_callback_revokes_handoff_without_new_clock(self):
        original,_,_,lease,ports=prepare();actor=make(ports,lease)
        def late():
            try:original.guard(list(original.arguments))
            except RuntimeError:pass
        ports.hooks['admit']=late;self.refused(ports,actor)
        self.assertEqual(ports.events[-1],'admit');self.assertNotIn('intent',ports.events)

    def test_unknown_stop_response_has_no_clock_sample_or_hash_followup(self):
        for change in (dict(returncode=False),dict(returncode=None),dict(unknown_result=True),dict(stdout=b'private'),
                       dict(stderr=b'private'),dict(private_extra=object())):
            _,_,_,lease,ports=prepare();ports.response.update(change);actor=make(ports,lease)
            with patch.object(records.hashlib,'sha256',side_effect=AssertionError('Unknown hashed')):
                self.refused(ports,actor)
            self.assertNotIn('sample1',ports.events);self.assertNotIn('record',ports.events)

    def test_bad_private_or_reused_sample_facts_stop_before_next_sample_or_hash(self):
        cases=[dict(original_pid_absent=False),dict(all_owned_package_processes_absent=False),dict(user0_force_stopped=False),
               dict(root_uid=1),dict(product_uid=99999),dict(original_pid=99),dict(raw_process_list_exported=0),
               dict(sample_index=2),dict(observation_id=identifier(10)),dict(private_extra=object())]
        for bad in cases:
            _,_,_,lease,ports=prepare();ports.bad_facts[1]=bad;actor=make(ports,lease)
            with patch.object(records.hashlib,'sha256',side_effect=AssertionError('Unknown facts hashed')):self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'sample1');self.assertNotIn('sample2',ports.events);self.assertNotIn('record',ports.events)
        _,_,_,lease,ports=prepare();ports.bad_facts[2]=dict(observation_id=identifier(9));actor=make(ports,lease)
        with patch.object(records.hashlib,'sha256',side_effect=AssertionError('Unknown facts hashed')):self.refused(ports,actor)
        self.assertEqual(ports.events[-1],'sample2');self.assertNotIn('record',ports.events)

    def test_readonly_same_original_owner_required_before_intent_and_command(self):
        for second in (False,True):
            _,_,_,lease,ports=prepare();normal=ports.product_owner
            def wrong():
                value=normal()
                if not second or ports.events.count('owner')==2:value['owned_boot']['boot_id']=identifier(99)
                return value
            ports.product_owner=wrong;actor=make(ports,lease);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'owner');self.assertNotIn('sample1',ports.events)

    def test_original_writer_clock_origin_reverse_bool_and_overflow_refuse(self):
        for mode in ('stale','reverse','bool','overflow'):
            _,issuer,_,lease,ports=prepare()
            if mode=='stale':ports.clock_values=[issuer._writer_guard-1]
            if mode=='reverse':ports.clock_values=[issuer._writer_guard+2,issuer._writer_guard+1]
            if mode=='bool':ports.clock_values=[True]
            if mode=='overflow':ports.step=0;ports.hooks['stop']=lambda:setattr(ports,'time',records.MAX-4999)
            actor=make(ports,lease);self.refused(ports,actor);self.assertNotIn('sample1',ports.events)

    def test_exact_2s_and_5s_metadata_limits_do_not_reuse_180s_deadline(self):
        _,_,_,lease,ports=prepare();ports.step=0;ports.time+=200000;ports.stop_delay=2000
        ports.hooks['sample2']=lambda:setattr(ports,'time',ports.command_start+5000)
        result=make(ports,lease).run();clock=result['records']['stop']['command_clock']
        self.assertEqual(clock['returned_monotonic_ms'],clock['started_monotonic_ms']+2000)
        self.assertEqual(result['records']['stop']['observations'][1]['sample_end_monotonic_ms'],clock['observation_deadline_monotonic_ms'])
        self.assertGreater(clock['started_monotonic_ms'],result['records']['driver']['deadline_monotonic_ms'])
        self.assertFalse(result['runtime_verified'])

    def test_late_stop_or_observation_or_final_writer_never_retries(self):
        for stage in ('stop','sample2','record'):
            _,_,_,lease,ports=prepare();ports.step=0
            if stage=='stop':ports.stop_delay=2001
            else:ports.hooks[stage]=lambda:setattr(ports,'time',ports.command_start+5001)
            actor=make(ports,lease);self.refused(ports,actor)
            self.assertEqual(ports.events.count('stop'),1);self.assertEqual(ports.events[-1],'clock')
            if stage!='record':self.assertNotIn('record',ports.events)

    def test_private_port_exceptions_and_unknown_writer_leave_originals_closed(self):
        for stage in ('admit','owner','intent','stop','sample1','sample2','record'):
            _,_,_,lease,ports=prepare()
            def fail():raise ValueError('synthetic private unknown')
            ports.hooks[stage]=fail;actor=make(ports,lease);self.refused(ports,actor);self.assertEqual(ports.events[-1],stage)
        _,_,_,lease,ports=prepare();ports.record_result=True;actor=make(ports,lease);self.refused(ports,actor)
        self.assertEqual(ports.events[-1],'record');self.assertEqual(ports.events.count('stop'),1)
        _,_,_,lease,ports=prepare();ports.files['record']=b'original';actor=make(ports,lease);self.refused(ports,actor)
        self.assertEqual(ports.files['record'],b'original')

    def test_hashing_occurs_only_after_both_closed_sample_reports(self):
        _,_,_,lease,ports=prepare();actor=make(ports,lease);original_sha=hashlib.sha256;calls=[]
        def sha(raw=b'',*args,**kwargs):
            self.assertIn('sample2',ports.events);calls.append(1);return original_sha(raw,*args,**kwargs)
        with patch.object(records.hashlib,'sha256',side_effect=sha):result=actor.run()
        self.assertTrue(calls);self.assertFalse(result['runtime_verified'])

    def test_controlled_source_uses_no_real_file_SQL_clock_process_or_device(self):
        original,_,_,lease,ports=prepare();events=list(original.events);actor=make(ports,lease)
        with patch('builtins.open',side_effect=AssertionError('IO')),patch('pathlib.Path.open',side_effect=AssertionError('IO')), \
             patch('sqlite3.connect',side_effect=AssertionError('SQL')),patch('time.monotonic',side_effect=AssertionError('Real clock')), \
             patch('subprocess.Popen',side_effect=AssertionError('Real process')):
            result=actor.run()
        self.assertEqual(original.events,events);self.assertFalse(result['runtime_verified'])

    def test_returned_packet_is_detached_and_second_run_or_late_guard_has_no_actions(self):
        _,_,_,lease,ports=prepare();actor=make(ports,lease);result=actor.run();raw=ports.files['record'];events=list(ports.events)
        self.assertIs(actor._stopped_raw,raw);result['records']['stop']['observations'][0]['root_uid']=99
        self.assertEqual(json.loads(raw)['records']['stop']['observations'][0]['root_uid'],0)
        with self.assertRaises(RuntimeError):actor.run()
        with self.assertRaises(RuntimeError):ports.guard(list(ports.arguments))
        self.assertEqual(ports.events,events)


if __name__=='__main__':unittest.main()
