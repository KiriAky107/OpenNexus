"""Controlled ports only; no managed process, device, original files or Stop."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import android_release_sync_association_host as host
import android_release_sync_association_records as records
from test_android_release_sync_association_records import fixture, identifier


class Ports(host.OriginalAssociationCompletionPorts):
    def __init__(self):
        full,self.intent,self.stdout,self.stderr=fixture()
        self.context=dict(launch_scope={k:v for k,v in full['capture_scope'].items()
            if k not in ('association_operation_id','preview_fingerprint')},identity=full['identity'],
            lifetime=full['original_lifetime'],collection_id=full['collection_id'],command_id=full['driver']['command_id'])
        self.events=[];self.time=1000;self.step=1;self.clock_values=[];self.polls=[0];self.files={}
        self.hooks={};self.guard=None;self.guard_mode='once';self.admitted=None;self.launch_result=None;self.wait_result=None;self.write_result=None
        self.response=None;self.stream_pair=None;self.delay=0;self.admit_mutation=False

    def event(self,name):
        self.events.append(name)
        if name in self.hooks:self.hooks[name]()

    def admit(self,**context):
        self.event('admit');self.seen_context=deepcopy(context)
        if self.admit_mutation:context['lifetime']['pid']=99;context['identity']['boot_id']=identifier(99)
        return self.admitted

    def monotonic_ms(self):
        self.event('clock')
        if self.clock_values:return self.clock_values.pop(0)
        self.time+=self.step
        self.last_clock=self.time
        return self.time

    def product_owner(self):
        self.event('owner')
        return dict(original_lifetime=deepcopy(self.context['lifetime']),owned_boot=deepcopy(self.context['identity']),
            same_original_foreground_PID_UID_boot=True,app_resumed=True,root_uid=0,native_cycle_acknowledged=False)

    def instrument(self,arguments,before_send,**limits):
        self.event('instrument');self.arguments=list(arguments);self.instrument_limits=dict(limits);self.guard=before_send
        if self.guard_mode=='once':before_send(list(arguments))
        elif self.guard_mode=='twice':before_send(list(arguments));before_send(list(arguments))
        elif self.guard_mode in ('bad','swallowed'):
            if self.guard_mode=='bad':before_send(['private changed arguments'])
            else:
                try:before_send(['private changed arguments'])
                except RuntimeError:pass
        return self.launch_result

    def poll_driver(self):
        self.event('poll');self.poll_return_time=self.time+self.step
        return self.polls.pop(0) if self.polls else None

    def wait_turn(self,**limits):
        self.event('wait');self.wait_limits=limits;self.time+=limits['maximum_ms'];return self.wait_result

    def streams(self):
        self.event('streams');return self.stream_pair if self.stream_pair is not None else (self.stdout,self.stderr)

    def read_intent(self,arguments,**limits):
        self.event('read');self.read_arguments=list(arguments);self.read_limits=limits;self.time+=self.delay
        return self.response if self.response is not None else dict(returncode=0,unknown_result=False,stdout=self.intent,stderr=b'')

    def record(self,name,raw):
        self.event('record')
        if name in self.files:raise FileExistsError('synthetic existing original')
        self.files[name]=raw
        return self.write_result


def make(ports=None,**overrides):
    ports=ports or Ports();context=deepcopy(ports.context);context.update(overrides)
    return ports,host.OriginalAssociationHostCompletion(ports,**context)


class AssociationHost(unittest.TestCase):
    def refused(self,ports,actor):
        with self.assertRaisesRegex(RuntimeError,'^SIGNED_ASSOCIATION_HOST_COMPLETION_UNAVAILABLE$'):actor.run()
        before=list(ports.events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(ports.events,before)
        self.assertEqual(actor._state,'FAILED')

    def test_constructor_has_no_port_clock_or_device_side_effect(self):
        ports,actor=make();self.assertEqual(ports.events,[]);self.assertEqual(actor._state,'NEW')

    def test_success_original_order_first_exit_and_one_each_command_read_writer(self):
        ports,actor=make();result=actor.run();events=ports.events
        self.assertEqual(actor._state,'DONE');self.assertEqual(result['profile'],records.COMPLETION_PROFILE)
        self.assertTrue(result['completion_only']);self.assertIs(result['stop_reported'],False);self.assertFalse(result['runtime_verified'])
        self.assertEqual(events.count('instrument'),1);self.assertEqual(events.count('poll'),1)
        self.assertEqual(events.count('streams'),1);self.assertEqual(events.count('read'),1);self.assertEqual(events.count('record'),1)
        self.assertEqual(events[events.index('poll')+1],'clock')
        self.assertLess(events.index('instrument'),events.index('poll'));self.assertLess(events.index('poll'),events.index('streams'))
        self.assertLess(events.index('streams'),events.index('read'));self.assertLess(events.index('read'),events.index('record'))
        driver=result['completion']['driver'];self.assertEqual(driver['first_exit_seen_monotonic_ms'],ports.poll_return_time)
        self.assertEqual(driver['arguments'],ports.arguments);self.assertEqual(driver['deadline_monotonic_ms'],driver['started_monotonic_ms']+180000)
        self.assertEqual(ports.instrument_limits,dict(command_id=identifier(7),timeout_ms=180000,deadline_ms=driver['deadline_monotonic_ms']))
        self.assertEqual(ports.read_limits,dict(bound_bytes=2048,timeout_ms=1000));self.assertEqual(ports.read_arguments,result['completion']['intent_read']['arguments'])
        self.assertEqual(len(ports.seen_context['launch_scope']),14)
        self.assertNotIn('association_operation_id',ports.seen_context['launch_scope'])
        self.assertEqual(result['completion']['capture_scope']['association_operation_id'],identifier(5))
        self.assertEqual(json.loads(next(iter(ports.files.values()))),result)

    def test_pending_local_polls_do_not_resend_command_or_reset_deadline(self):
        ports=Ports();ports.polls=[None,None,0];ports,actor=make(ports);result=actor.run()
        self.assertEqual(ports.events.count('poll'),3);self.assertEqual(ports.events.count('wait'),2)
        self.assertEqual(ports.events.count('instrument'),1)
        self.assertEqual(ports.wait_limits,dict(maximum_ms=50,deadline_ms=result['completion']['driver']['deadline_monotonic_ms']))

    def test_context_and_returned_metadata_are_detached_from_callers_and_ports(self):
        ports=Ports();ports.admit_mutation=True;context=deepcopy(ports.context);actor=host.OriginalAssociationHostCompletion(ports,**context)
        context['lifetime']['pid']=99;context['launch_scope']['uid']=99999
        result=actor.run();self.assertEqual(result['completion']['original_lifetime']['pid'],1234)
        result['completion']['driver']['arguments'][0]='changed'
        self.assertNotEqual(json.loads(next(iter(ports.files.values())))['completion']['driver']['arguments'][0],'changed')

    def test_invalid_constructor_context_refuses_before_any_port(self):
        for key,value in [('collection_id',identifier(1)),('command_id',identifier(6)),('command_id',True)]:
            ports=Ports()
            with self.assertRaises((RuntimeError,ValueError)):make(ports,**{key:value})
            self.assertEqual(ports.events,[])
        ports=Ports();bad=dict(ports.context['launch_scope'],association_operation_id=identifier(5))
        with self.assertRaises(ValueError):make(ports,launch_scope=bad)
        self.assertEqual(ports.events,[])

    def test_required_abstract_ports_have_no_default_success_adapter(self):
        with self.assertRaises(TypeError):host.OriginalAssociationCompletionPorts()
        ports=Ports()
        with self.assertRaises(RuntimeError):host.OriginalAssociationHostCompletion(object(),**ports.context)

    def test_missing_repeated_bad_or_swallowed_guard_refuses_without_poll(self):
        for mode in ('missing','twice','bad','swallowed'):
            ports=Ports();ports.guard_mode=mode;ports,actor=make(ports);self.refused(ports,actor)
            self.assertNotIn('poll',ports.events);self.assertNotIn('streams',ports.events);self.assertNotIn('read',ports.events)
            if mode in ('bad','swallowed'):self.assertEqual(ports.events[-1],'instrument')

    def test_command_guard_cannot_reenter_even_if_port_swallows_error(self):
        ports,actor=make()
        def reenter():
            try:actor.run()
            except RuntimeError:pass
        ports.hooks['instrument']=reenter;self.refused(ports,actor);self.assertNotIn('poll',ports.events)

    def test_unknown_admission_launch_and_wait_results_close_before_next_port(self):
        for field,last in [('admitted','admit'),('launch_result','instrument'),('wait_result','wait')]:
            ports=Ports();setattr(ports,field,True)
            if field=='wait_result':ports.polls=[None]
            ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],last if last!='instrument' else 'clock')
            self.assertNotIn('streams',ports.events)

    def test_owner_is_readonly_product_only_before_and_after_driver_exit(self):
        for late in (False,True):
            ports=Ports();original=ports.product_owner
            def bad_owner():
                result=original()
                if not late or 'read' in ports.events:result['original_lifetime']['start_ticks']=99
                return result
            ports.product_owner=bad_owner;ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'owner');self.assertNotIn('record',ports.events)

    def test_bad_nonNone_poll_refuses_before_followup_clock_or_stream(self):
        for value in (False,True,1,-1,{},'private',object()):
            ports=Ports();ports.polls=[value];ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'poll');self.assertNotIn('streams',ports.events)

    def test_bad_or_private_stream_pair_refuses_before_any_read_or_clock(self):
        for pair in ([b'a',b''],(b'private',b''),(b'',b''),(b'private',b'private'),(b'a',)):
            ports=Ports();ports.stream_pair=pair;ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'streams');self.assertNotIn('read',ports.events);self.assertFalse(ports.files)

    def test_unknown_partial_duplicate_or_wrong_intent_never_gets_followup_port(self):
        for changes in [dict(returncode=False),dict(unknown_result=True),dict(stderr=b'private'),dict(stdout=b'{'),
                        dict(stdout=b''),dict(stdout=b'\xff'),dict(stdout=b'{"private":"secret"}'),dict(private_extra='secret')]:
            ports=Ports();ports.response=dict(returncode=0,unknown_result=False,stdout=ports.intent,stderr=b'');ports.response.update(changes)
            ports,actor=make(ports);self.refused(ports,actor);self.assertEqual(ports.events[-1],'read');self.assertNotIn('record',ports.events)
        for change in ('duplicate','wrong-vault','reused-ID'):
            ports=Ports();value=json.loads(ports.intent)
            if change=='duplicate':ports.intent=ports.intent[:-1]+b',"operation_id":"private"}'
            else:
                value['remote_vault' if change=='wrong-vault' else 'operation_id']='b'*32 if change=='wrong-vault' else identifier(6)
                ports.intent=json.dumps(value).encode()
            ports,actor=make(ports);self.refused(ports,actor);self.assertEqual(ports.events[-1],'read')

    def test_invalid_reverse_or_overflow_host_clock_has_no_retry(self):
        for values in ([True],[-1],[records.MAX-179999],[1000,999]):
            ports=Ports();ports.clock_values=list(values);ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],'clock');self.assertNotIn('instrument',ports.events)

    def test_exact_180s_and_1s_original_metadata_endpoints(self):
        ports=Ports();ports.step=0;ports.delay=1000
        ports.hooks['record']=lambda:setattr(ports,'time',181000)
        ports,actor=make(ports);result=actor.run();self.assertEqual(ports.time,181000)
        read=result['completion']['intent_read'];self.assertEqual(read['returned_monotonic_ms']-read['started_monotonic_ms'],1000)

    def test_late_admit_read_or_writer_closes_without_followup(self):
        for stage in ('admit','read','record'):
            ports=Ports();ports.step=0
            if stage=='read':ports.delay=1001
            else:ports.hooks[stage]=lambda:setattr(ports,'time',181001)
            ports,actor=make(ports);self.refused(ports,actor);self.assertEqual(ports.events[-1],'clock')
            if stage!='record':self.assertNotIn('record',ports.events)

    def test_stagnant_pending_clock_is_bounded_and_not_absence_or_success(self):
        ports=Ports();ports.step=0;ports.polls=[]
        def stalled_wait(**limits):ports.event('wait');return None
        ports.wait_turn=stalled_wait;ports,actor=make(ports);self.refused(ports,actor)
        self.assertEqual(ports.events.count('poll'),3601);self.assertNotIn('streams',ports.events)

    def test_existing_or_unknown_writer_does_not_read_back_or_retry(self):
        for existing in (False,True):
            ports=Ports()
            if existing:ports.files['signed-association-bootstrap-completion.json']=b'original'
            else:ports.write_result=True
            ports,actor=make(ports);self.refused(ports,actor);self.assertEqual(ports.events[-1],'record')
            self.assertEqual(ports.events.count('record'),1)
            if existing:self.assertEqual(ports.files['signed-association-bootstrap-completion.json'],b'original')

    def test_private_port_exception_is_static_and_no_failure_followup_occurs(self):
        for stage in ('admit','owner','instrument','poll','streams','read','record'):
            ports=Ports()
            def private_failure():raise ValueError('synthetic-private-password-message')
            ports.hooks[stage]=private_failure;ports,actor=make(ports);self.refused(ports,actor)
            self.assertEqual(ports.events[-1],stage)

    def test_source_producer_uses_zero_SHA_and_no_real_IO_SQL_clock_process(self):
        ports,actor=make()
        with patch.object(records.hashlib,'sha256',side_effect=AssertionError('SHA')), \
             patch('builtins.open',side_effect=AssertionError('IO')), patch('pathlib.Path.open',side_effect=AssertionError('IO')), \
             patch('sqlite3.connect',side_effect=AssertionError('SQL')), patch('time.monotonic',side_effect=AssertionError('Real clock')), \
             patch('subprocess.Popen',side_effect=AssertionError('Real process')):
            result=actor.run()
        self.assertFalse(result['runtime_verified']);self.assertIs(result['stop_reported'],False)

    def test_approved_private_RAM_and_late_guard_remain_closed_without_new_actions(self):
        ports,actor=make();result=actor.run();events=list(ports.events)
        self.assertEqual(actor._approved,(ports.intent,ports.stdout,ports.stderr))
        self.assertFalse(any(type(v) is bytes for v in result.values()))
        with self.assertRaises(RuntimeError):ports.guard(list(ports.arguments))
        self.assertEqual(events,ports.events);self.assertEqual(ports.events.count('instrument'),1)


if __name__=='__main__':unittest.main()
