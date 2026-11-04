"""New quota input-port sequencing only; no real ADB, service, clock or Native."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from android_release_observation import exclusive
import android_release_sync_quota_host_prefix as host
import android_release_sync_quota_records as records
import android_release_sync_quota_upload as upload
from test_android_release_sync_quota_records import fixture, signal


class Unknown:
    def __str__(self):raise AssertionError('Unknown value formatted')
    def __repr__(self):raise AssertionError('Unknown value represented')
    def __eq__(self, other):raise AssertionError('Unknown value compared')
    def __hash__(self):raise AssertionError('Unknown value hashed')


class Ports(host.OriginalQuotaInputPorts):
    def __init__(self, directory):
        self.directory = directory
        self.data, self.static, _ = fixture()
        self.base = {k:deepcopy(v) for k,v in self.data['scope'].items() if k not in ('parent_id','input_id','window')}
        self.identity = deepcopy(self.data['identity']);self.life = deepcopy(self.data['original_lifetime'])
        self.events = [];self.buffers = {};self.now = 100;self.clocks = []
        self.admit_result = None;self.record_result = None;self.factory_result = None
        self.skip_guard = self.repeat_guard = self.swallow_guard = self.ready_missing = False
        self.raise_at = None;self.failure = OSError('controlled original outcome unknown')
        self.closed_at = None;self.actor = None;self.owner_number = 0

    def event(self, name):
        self.events.append(name)
        if self.closed_at == name:self.actor.close()
        if self.raise_at == name:raise self.failure

    def admit(self, base, identity, lifetime):
        self.event('admit')
        if base != self.base or identity != self.identity or lifetime != self.life:raise AssertionError('Wrong base callback')
        return self.admit_result

    def monotonic_ms(self):
        self.event('mono');return self.now

    def owner(self):
        self.owner_number += 1;self.event('owner-'+str(self.owner_number))

    def wait_json(self, path, deadline_ms, owner):
        if path.endswith('-intent.json'):
            self.event('wait-parent');owner();self.now = 200;return self.data['parent']
        if not path.endswith('-cycle-start-ready.json'):raise AssertionError('Unapproved device file')
        self.event('wait-ready');owner();self.now = 1900
        return None if self.ready_missing else self.data['ready']

    def freeze_context(self, parent, **kwargs):
        self.event('freeze-context')
        context = upload.context_from_parent(parent, **kwargs)
        raw = upload.encoded(context);self.buffers['original-context-buffer'] = raw
        exclusive(self.directory/upload.CONTEXT, raw)
        return (context,hashlib.sha256(raw).hexdigest()) if self.factory_result is None else self.factory_result

    def record(self, name, raw):
        self.event('record-'+name)
        if type(raw) is not bytes:raise AssertionError('Not one closed byte buffer')
        self.buffers[name] = raw;exclusive(self.directory/(name+'.json'), raw)
        return self.record_result

    def signal(self, name, deadline_ms):
        kind = 'context' if '-context-' in name else 'finish'
        self.event('signal-'+kind);self.now += 1
        return signal(name)

    def clock(self):
        index = len(self.clocks)
        self.event('clock-'+str(index+1))
        value = self.data['input_clock' if index == 0 else 'command_clock']
        self.clocks.append(deepcopy(value));self.now = value['sample_end_monotonic_ms']
        return value

    def tap(self, arguments, before_send, *, timeout_ms):
        self.event('tap-entry')
        if type(timeout_ms) is not int or timeout_ms != 5000:raise AssertionError('Original command timeout widened')
        if arguments != ('shell','-T','input touchscreen tap 50 40'):raise AssertionError('Foreign tap')
        if not self.skip_guard:before_send()
        if self.repeat_guard:
            try:before_send()
            except RuntimeError:
                if not self.swallow_guard:raise
        self.event('tap-send')
        return deepcopy(self.data['touch_result'])

    def post_input_owner(self):
        self.event('post-owner');return self.data['post_input_owner']


class QuotaHostInputPrefix(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ['ONX_QUOTA_HOST_PREFIX_ROOT'])
        self.assertTrue(parent.is_absolute() and parent.is_dir())
        self.temp = tempfile.TemporaryDirectory(prefix='case-',dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.ports = Ports(Path(self.temp.name))

    def actor(self, **changes):
        args = dict(base=self.ports.base,identity=self.ports.identity,lifetime=self.ports.life)
        args.update(changes)
        self.ports.actor = host.OriginalQuotaInputPrefix(self.ports,**args)
        return self.ports.actor

    def test_real_small_writer_order_same_buffer_single_input_and_completion_is_separate(self):
        actor = self.actor();before = deepcopy(self.ports.data)
        result = actor.run();events = self.ports.events
        order = ['wait-parent','record-parent','freeze-context','record-context-write','signal-context',
                 'wait-ready','clock-1','record-touch-intent','tap-entry','clock-2','tap-send','post-owner','signal-finish']
        self.assertEqual(sorted(events.index(item) for item in order),[events.index(item) for item in order])
        self.assertEqual(events.count('tap-send'),1);self.assertEqual(len(self.ports.clocks),2)
        self.assertEqual(events.count('signal-context'),1);self.assertEqual(events.count('signal-finish'),1)
        self.assertEqual(self.ports.data,before)
        context_bytes = (self.ports.directory/upload.CONTEXT).read_bytes()
        self.assertEqual(context_bytes,self.ports.buffers['original-context-buffer'])
        self.assertEqual(result['records']['context_write']['context_file_sha256'],hashlib.sha256(context_bytes).hexdigest())
        self.assertTrue(result['input_prefix_only']);self.assertFalse(result['completed_driver_reported'])
        self.assertFalse(result['runtime_verified']);self.assertFalse(result['native_cycle_acknowledged'])
        with self.assertRaises(ValueError):records.validate_completion(result['records'],self.ports.static)
        whole = deepcopy(before);whole.update(result['records'])
        projected = records.validate_completion(whole,self.ports.static)
        self.assertTrue(projected['completed_driver_reported']);self.assertFalse(projected['runtime_verified'])
        original = result['records']['ready']['original'][:]
        self.ports.data['ready']['original'][0] = -123
        self.assertEqual(result['records']['ready']['original'],original)
        count = len(events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(len(events),count)

    def test_invalid_base_or_unbound_ports_refuse_before_callbacks(self):
        for key,value in [('uid',True),('owner_run',Unknown()),('product',Unknown()),('private',Unknown()),('parent_id','bad')]:
            base = dict(self.ports.base);base[key] = value
            with self.assertRaises((RuntimeError,ValueError)):self.actor(base=base)
        with self.assertRaises(RuntimeError):host.OriginalQuotaInputPrefix(True,base=self.ports.base,identity=self.ports.identity,lifetime=self.ports.life)
        self.assertEqual(self.ports.events,[])

    def test_unknown_parent_never_calls_context_factory(self):
        self.ports.data['parent']['private'] = Unknown()
        with self.assertRaises((RuntimeError,ValueError)):self.actor().run()
        self.assertNotIn('freeze-context',self.ports.events)
        self.assertEqual(list(self.ports.directory.iterdir()),[])

    def test_unknown_factory_return_never_signals_and_existing_context_is_preserved(self):
        self.ports.factory_result = (Unknown(),Unknown())
        actor = self.actor()
        with self.assertRaises((RuntimeError,ValueError)):actor.run()
        self.assertNotIn('signal-context',self.ports.events)
        original = (self.ports.directory/upload.CONTEXT).read_bytes()
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual((self.ports.directory/upload.CONTEXT).read_bytes(),original)

    def test_existing_original_record_stops_without_overwrite_or_factory(self):
        path = self.ports.directory/'parent.json';exclusive(path,b'original')
        with self.assertRaises(FileExistsError):self.actor().run()
        self.assertEqual(path.read_bytes(),b'original')
        self.assertNotIn('freeze-context',self.ports.events)

    def test_complete_bad_or_missing_ready_refuses_before_clock_or_input(self):
        self.ports.ready_missing = True
        with self.assertRaises(ValueError):self.actor().run()
        self.assertEqual(self.ports.clocks,[]);self.assertNotIn('tap-entry',self.ports.events)
        # This returns None only after the original supplied wait has ended;
        # it is not an assertion that any device file was absent.

    def test_wrong_ready_scope_is_not_repaired_or_sampled(self):
        self.ports.data['ready']['uid'] = True
        with self.assertRaises((RuntimeError,ValueError)):self.actor().run()
        self.assertEqual(self.ports.events.count('wait-ready'),1)
        self.assertNotIn('clock-1',self.ports.events);self.assertNotIn('tap-entry',self.ports.events)

    def test_boolean_clock_never_sends_input(self):
        self.ports.data['input_clock']['sampled_elapsed_ms'] = True
        with self.assertRaises(ValueError):self.actor().run()
        self.assertNotIn('tap-entry',self.ports.events)

    def test_final_clock_stale_after_private_intent_never_sends_touch(self):
        self.ports.data['command_clock']['sampled_elapsed_ms'] = 3001
        with self.assertRaises(RuntimeError):self.actor().run()
        self.assertIn('record-touch-intent',self.ports.events)
        self.assertNotIn('tap-send',self.ports.events);self.assertNotIn('signal-finish',self.ports.events)

    def test_swallowed_duplicate_guard_is_terminal(self):
        self.ports.repeat_guard = self.ports.swallow_guard = True
        with self.assertRaises(RuntimeError):self.actor().run()
        self.assertNotIn('post-owner',self.ports.events);self.assertNotIn('signal-finish',self.ports.events)
        self.assertEqual(len(self.ports.clocks),2)

    def test_unknown_after_send_preserves_original_exception_without_followup(self):
        self.ports.raise_at = 'tap-send';actor = self.actor()
        with self.assertRaises(OSError) as caught:actor.run()
        self.assertIs(caught.exception,self.ports.failure)
        self.assertEqual(self.ports.events[-1],'tap-send')
        count = len(self.ports.events)
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(len(self.ports.events),count)

    def test_callback_closure_does_not_trigger_more_io(self):
        self.ports.closed_at = 'freeze-context'
        with self.assertRaises(RuntimeError):self.actor().run()
        self.assertEqual(self.ports.events[-1],'freeze-context')
        self.assertNotIn('signal-context',self.ports.events)

    def test_unknown_post_owner_never_emits_input_finish(self):
        self.ports.data['post_input_owner']['app_resumed'] = None
        with self.assertRaises(ValueError):self.actor().run()
        self.assertEqual(self.ports.events.count('tap-send'),1)
        self.assertNotIn('signal-finish',self.ports.events)

    def test_missing_before_send_callback_cannot_be_repaired_after_return(self):
        self.ports.skip_guard = True
        with self.assertRaises(RuntimeError):self.actor().run()
        self.assertEqual(self.ports.events.count('tap-entry'),1)
        self.assertNotIn('post-owner',self.ports.events);self.assertNotIn('signal-finish',self.ports.events)

    def test_false_writer_return_after_original_file_write_never_retries(self):
        self.ports.record_result = False;actor = self.actor()
        with self.assertRaises(RuntimeError):actor.run()
        self.assertEqual(self.ports.events[-1],'record-parent')
        self.assertTrue((self.ports.directory/'parent.json').is_file())
        self.assertNotIn('freeze-context',self.ports.events)
        with self.assertRaises(RuntimeError):actor.run()

    def test_reverse_final_bracket_refuses_before_touch(self):
        self.ports.data['command_clock'].update(sample_start_monotonic_ms=1999,sample_end_monotonic_ms=2000)
        with self.assertRaises(RuntimeError):self.actor().run()
        self.assertNotIn('tap-send',self.ports.events)

    def test_overflow_of_host_completion_bound_is_not_clamped(self):
        self.ports.data['input_clock'].update(sample_start_monotonic_ms=records.MAX-41000,sample_end_monotonic_ms=records.MAX-40990)
        with self.assertRaises(ValueError):self.actor().run()
        self.assertNotIn('tap-entry',self.ports.events)


if __name__=='__main__':unittest.main()
