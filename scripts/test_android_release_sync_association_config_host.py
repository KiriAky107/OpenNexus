"""RAM controlled config ports; no private file, process or device acceptance."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch

import android_release_sync_association_config_host as config
import android_release_sync_quota_records as common


def identifier(value):
    return '00000000-0000-0000-0000-' + format(value, '012x')


def context():
    return dict(launch_scope=dict(schema=1, owner_run='99999999999-1',
        avd='opennexus-ci-99999999999-1', boot_id=identifier(1), api=36, uid=10123,
        product=common.quota.PRODUCT, sync=common.quota.SYNC, remote_vault='a'*32,
        android_device='b'*32, setup_device='c'*32, login_operation_id=identifier(2),
        setup_operation_id=identifier(3), save_operation_id=identifier(4)),
        vault_name='Owned Android Sync ' + 'd'*32, command_id=identifier(6))


class Ports(config.OriginalAssociationConfigPorts):
    def __init__(self):
        self.events, self.files, self.hooks = [], {}, {}
        self.time, self.step = 1000, 1
        self.clocks = []
        self.mode = 'once'
        self.admit_result = self.owner_result = self.intent_result = self.result_result = None
        self.response = None
        self.guard = None

    def event(self, name):
        self.events.append(name)
        if name in self.hooks:
            self.hooks[name]()

    def admit(self, **value):
        self.event('admit')
        self.admitted = value
        return self.admit_result

    def monotonic_ms(self):
        self.event('clock')
        if self.clocks:
            return self.clocks.pop(0)
        self.time += self.step
        return self.time

    def verify_config_owner(self):
        self.event('owner')
        return self.owner_result

    def record_config_intent(self, raw):
        self.event('intent')
        if 'intent' in self.files:
            raise FileExistsError('controlled existing private intent')
        self.files['intent'] = raw
        return self.intent_result

    def stage_config(self, arguments, original_input, before_send, **limits):
        self.event('stage')
        self.arguments, self.input, self.guard, self.limits = arguments, original_input, before_send, limits
        if self.mode == 'once':
            before_send(list(arguments), original_input)
        elif self.mode == 'double':
            before_send(list(arguments), original_input)
            before_send(list(arguments), original_input)
        elif self.mode == 'copied-input':
            before_send(list(arguments), memoryview(original_input).tobytes())
        elif self.mode == 'changed-argv':
            before_send(['private changed command'], original_input)
        elif self.mode == 'swallowed':
            try:
                before_send(['private changed command'], original_input)
            except RuntimeError:
                pass
        elif self.mode != 'missing':
            raise AssertionError('unknown controlled mode')
        self.event('sent')
        return self.response if self.response is not None else dict(returncode=0,
            unknown_result=False, stdout=b'', stderr=b'')

    def record_config_result(self, raw):
        self.event('result')
        if 'result' in self.files:
            raise FileExistsError('controlled existing private result')
        self.files['result'] = raw
        return self.result_result


def make(ports=None, **overrides):
    ports = ports or Ports()
    value = context()
    value.update(overrides)
    return ports, config.OriginalAssociationConfigStaging(ports, **value)


class ConfigHost(unittest.TestCase):
    def refused(self, ports, actor):
        with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_STAGING_UNAVAILABLE$'):
            actor.run()
        self.assertEqual(actor._state, 'FAILED')
        self.assertIsNone(actor._approved)
        after = list(ports.events)
        with self.assertRaises(RuntimeError):
            actor.run()
        self.assertEqual(ports.events, after)

    def test_constructor_is_pure_and_context_has_no_future_ids_or_alias(self):
        value = context()
        ports = Ports()
        actor = config.OriginalAssociationConfigStaging(ports, **value)
        value['launch_scope']['uid'] = 99999
        self.assertEqual(ports.events, [])
        self.assertEqual(actor._context['launch_scope']['uid'], 10123)
        self.assertEqual(len(actor._context['launch_scope']), 14)
        self.assertEqual(actor._state, 'NEW')

    def test_original_two_field_input_and_journal_result_buffers_are_same_objects(self):
        ports, actor = make()
        self.assertIsNone(actor.run())
        intent, raw, result, guard = actor._approved
        self.assertIs(raw, ports.input)
        self.assertIs(intent, ports.files['intent'])
        self.assertIs(result, ports.files['result'])
        self.assertEqual(raw, json.dumps(dict(vault_id='a'*32,
            vault_name='Owned Android Sync ' + 'd'*32)).encode())
        self.assertLessEqual(len(raw), 2048)
        self.assertEqual(actor._state, 'DONE')
        self.assertEqual(guard, 1008)

    def test_sequence_has_dedicated_intent_and_two_independent_owner_gates(self):
        ports, actor = make()
        actor.run()
        self.assertEqual([x for x in ports.events if x != 'clock'],
            ['admit', 'owner', 'intent', 'stage', 'owner', 'sent', 'result'])
        self.assertEqual(ports.arguments, list(config._ARGUMENTS))
        self.assertEqual(ports.limits, dict(command_id=identifier(6), timeout_ms=9997,
            deadline_ms=11001))
        journal = json.loads(ports.files['intent'])
        self.assertEqual(journal['stdin_bytes'], len(ports.input))
        self.assertNotIn('stdin', journal)
        self.assertNotIn('sha256', journal)
        self.assertIn('set -C', ports.arguments[2])
        self.assertIn('test ! -L', ports.arguments[2])

    def test_required_ports_have_no_concrete_fallback_or_public_raw_lease(self):
        with self.assertRaises(TypeError):
            config.OriginalAssociationConfigPorts()
        self.assertEqual({name for name in vars(config.OriginalAssociationConfigStaging)
            if not name.startswith('_')}, {'run'})

    def test_bad_exact_context_name_and_fresh_id_refuse_before_ports(self):
        class Private:
            def __str__(self):
                raise AssertionError('private coercion')
        for change in (dict(vault_name=Private()), dict(vault_name='Other ' + 'd'*32),
                       dict(command_id=identifier(3)), dict(command_id=True),
                       dict(launch_scope={**context()['launch_scope'], 'future_uuid':Private()})):
            with self.subTest(change=list(change)):
                ports = Ports()
                with self.assertRaisesRegex(RuntimeError, '^SIGNED_ASSOCIATION_CONFIG_STAGING_UNAVAILABLE$'):
                    make(ports, **change)
                self.assertEqual(ports.events, [])

    def test_admission_context_mutation_cannot_change_actual_input_or_journal(self):
        ports, actor = make()
        ports.hooks['owner'] = lambda: ports.admitted['launch_scope'].update(remote_vault='f'*32)
        actor.run()
        self.assertEqual(json.loads(ports.input)['vault_id'], 'a'*32)
        self.assertEqual(json.loads(ports.files['intent'])['launch_scope']['remote_vault'], 'a'*32)

    def test_missing_double_changed_or_copied_input_guard_burns_without_result(self):
        for mode in ('missing', 'double', 'changed-argv', 'copied-input', 'swallowed'):
            with self.subTest(mode=mode):
                ports, actor = make()
                ports.mode = mode
                self.refused(ports, actor)
                self.assertNotIn('result', ports.files)

    def test_late_callback_revokes_done_without_any_new_port(self):
        ports, actor = make()
        actor.run()
        before = list(ports.events)
        with self.assertRaises(RuntimeError):
            ports.guard(ports.arguments, ports.input)
        self.assertEqual(ports.events, before)
        self.assertIsNone(actor._approved)
        self.assertEqual(actor._state, 'FAILED')

    def test_unknown_private_or_extra_response_is_rejected_before_later_clock(self):
        class Private:
            def __str__(self):
                raise AssertionError('private response coercion')
            def __bytes__(self):
                raise AssertionError('private response coercion')
        good = dict(returncode=0, unknown_result=False, stdout=b'', stderr=b'')
        candidates = [Private(), {**good, 'returncode':True}, {**good, 'unknown_result':True},
            {**good, 'stdout':Private()}, {**good, 'stderr':b'private response'},
            {**good, 'extra':Private()}, {**good, 'returncode':None}]
        for response in candidates:
            ports, actor = make()
            ports.response = response
            self.refused(ports, actor)
            self.assertEqual(ports.events[-1], 'sent')
            self.assertNotIn('result', ports.files)

    def test_non_none_admit_owner_and_journal_returns_close_before_next_operation(self):
        for attribute, last in (('admit_result','admit'),('owner_result','owner'),
                                ('intent_result','intent'),('result_result','result')):
            ports, actor = make()
            setattr(ports, attribute, False)
            self.refused(ports, actor)
            self.assertEqual(ports.events[-1], last)

    def test_writer_failure_and_swallowed_reentry_are_once_only(self):
        for name in ('admit', 'owner', 'intent', 'stage', 'result'):
            ports, actor = make()
            def reenter():
                try:
                    actor.run()
                except RuntimeError:
                    pass
            ports.hooks[name] = reenter
            self.refused(ports, actor)
        for name in ('intent', 'result'):
            ports, actor = make()
            ports.files[name] = b'controlled original remains'
            self.refused(ports, actor)
            self.assertEqual(ports.files[name], b'controlled original remains')

    def test_original_ten_second_endpoint_is_inclusive_for_result_and_writer(self):
        ports, actor = make()
        ports.clocks = [1000]*6 + [11000, 11000]
        actor.run()
        self.assertEqual(actor._approved[-1], 11000)
        self.assertEqual(json.loads(ports.files['result'])['deadline_monotonic_ms'], 11000)

    def test_late_result_writer_or_send_guard_never_retains_success(self):
        for clocks in ([1000]*6+[11001], [1000]*6+[11000,11001],
                       [1000]*5+[11000]):
            ports, actor = make()
            ports.clocks = list(clocks)
            self.refused(ports, actor)
        ports, actor = make()
        ports.hooks['result'] = lambda: setattr(ports, 'time', 999999)
        self.refused(ports, actor)

    def test_bool_overflow_reverse_and_private_clock_refuse_without_fabrication(self):
        for clocks in ([True], [-1], [common.MAX-9999], [1000,999], [object()]):
            ports, actor = make()
            ports.clocks = list(clocks)
            self.refused(ports, actor)
            self.assertNotIn('stage', ports.events)

    def test_all_independent_acceptance_false_with_no_hash_file_SQL_process_or_real_clock(self):
        ports, actor = make()
        with patch('builtins.open', side_effect=AssertionError), \
             patch.object(Path, 'open', side_effect=AssertionError), \
             patch.object(os, 'open', side_effect=AssertionError), \
             patch.object(sqlite3, 'connect', side_effect=AssertionError), \
             patch.object(hashlib, 'sha256', side_effect=AssertionError), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError), \
             patch.object(time, 'monotonic', side_effect=AssertionError):
            actor.run()
        result = json.loads(ports.files['result'])
        for key in ('actual_private_factory_verified','independent_private_file_or_syscall_verified',
                    'device_config_readback_verified','native_client_acknowledged','runtime_acceptance'):
            self.assertIs(result[key], False)


if __name__ == '__main__':
    unittest.main()
