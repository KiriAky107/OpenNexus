"""Opaque complete-map deltas from original reads; no SDK/device execution or raw key export."""
import hashlib
import json
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch

import android_release_sdk_ini as ini
import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle
from test_android_release_sdk_ini_flow import IniDiagnosticFlow

CTX = dict(root=PurePosixPath('/owned/sdk-owned-123456-1'))
DATA = b'First=private-same-value\nSecond=private-same-value\nEmpty=\nUnknown=private-value\nToRemove=old\n'


class OpaqueIniFingerprints(unittest.TestCase):
    def test_formatting_preserves_every_opaque_pair_and_values_are_key_bound(self):
        a = ini.fingerprints(CTX, DATA)
        b = ini.fingerprints(CTX, b'; comment\r\nToRemove = old\r\nUnknown = private-value\r\nEmpty = \r\nSecond = private-same-value\r\nFirst = private-same-value\r\n')
        self.assertTrue(ini.fingerprints_valid(a)); self.assertTrue(ini.fingerprints_valid(b))
        self.assertEqual(a['pairs'], b['pairs']); self.assertEqual(a['pairs_sha256'], b['pairs_sha256'])
        self.assertNotEqual(a['source_sha256'], b['source_sha256'])
        self.assertEqual(len({r[1] for r in a['pairs']}), 5)
        self.assertNotIn('private', json.dumps(a)); self.assertNotIn('Unknown', json.dumps(a))
        self.assertFalse(a['runtime_verified'])

    def test_delta_covers_unknown_changes_additions_removals_and_unchanged_entries(self):
        before = ini.fingerprints(CTX, DATA)
        after = ini.fingerprints(CTX, DATA.replace(b'Unknown=private-value', b'Unknown=private-changed').replace(b'ToRemove=old\n', b'') + b'New=private-added\n')
        delta = ini.fingerprint_delta(before, after)
        self.assertTrue(delta['available']); self.assertEqual((delta['before_keys'], delta['after_keys'], delta['unchanged_keys']), (5, 5, 3))
        self.assertEqual(len(delta['added_key_sha256']), 1); self.assertEqual(len(delta['removed_key_sha256']), 1)
        self.assertEqual(len(delta['changed']), 1)
        self.assertEqual(delta['changed'][0]['key_sha256'], hashlib.sha256(b'SDK_INI_KEY\0Unknown').hexdigest())
        self.assertNotIn('Unknown', json.dumps(delta)); self.assertNotIn('private', json.dumps(delta))

    def test_limits_or_invalid_complete_map_never_return_a_partial_fingerprint_set(self):
        for raw in [DATA + b'Unknown=duplicate\n', DATA + b'\xff', b''.join(('Key'+str(i)+'=x\n').encode() for i in range(ini.FP_MAX_KEYS + 1))]:
            fp = ini.fingerprints(CTX, raw)
            self.assertFalse(fp['available']); self.assertIsNone(fp['pairs']); self.assertTrue(ini.fingerprints_valid(fp))
            self.assertFalse(ini.fingerprint_delta(fp, ini.fingerprints(CTX, DATA))['available'])

    def test_malformed_private_extra_duplicate_or_bad_digest_cannot_leak_through_delta(self):
        original = ini.fingerprints(CTX, DATA)
        for mutate in [lambda v: v.update(private='never-export'), lambda v: v.update(key_count=True),
                       lambda v: v.update(pairs_sha256='0' * 64), lambda v: v['pairs'][0].__setitem__(0, 'private-row'),
                       lambda v: v['pairs'].__setitem__(0, v['pairs'][1])]:
            value = json.loads(json.dumps(original)); mutate(value)
            self.assertFalse(ini.fingerprints_valid(value)); delta = ini.fingerprint_delta(value, original)
            self.assertFalse(delta['available']); self.assertIsNone(delta['changed']); self.assertNotIn('private', json.dumps(delta))

    def test_largest_complete_set_fits_original_bounded_sidecar_reader_without_IO(self):
        raw = b''.join(('Key'+str(i)+'=value\n').encode() for i in range(ini.FP_MAX_KEYS))
        with patch('builtins.open', side_effect=AssertionError('no file IO')): value = ini.fingerprints(CTX, raw)
        self.assertTrue(ini.fingerprints_valid(value)); self.assertEqual(len(value['pairs']), ini.FP_MAX_KEYS)
        self.assertLess(len(json.dumps(dict(owner={}, fingerprints=value)).encode()), 60000)


class OpaqueIniFlow(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command
    prepared_image = SDKLifecycle.prepared_image
    ready = SDKLifecycle.ready
    config_ready = IniDiagnosticFlow.config_ready
    bound = IniDiagnosticFlow.bound
    reject = IniDiagnosticFlow.reject

    def fp_path(self): return self.ctx['evidence'] / 'sdk-avd-config-fingerprints.json'

    def test_prelaunch_opaque_sidecar_comes_from_original_read_and_never_exports_keys(self):
        self.config_ready(); reads = []; original = subject.raw_file
        def read(path, *args):
            if path == self.config: reads.append(path)
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): self.binding = subject.bind_avd(self.ctx)
        self.assertEqual(reads, [self.config]); value = json.loads(self.fp_path().read_bytes())
        projection = subject.expected_ini_projection(self.ctx, self.binding)
        self.assertEqual(value['fingerprints'], subject.expected_ini_fingerprints(self.ctx, self.binding, projection))
        self.assertEqual(value['fingerprints']['source_sha256'], self.binding['config_sha256'])
        self.assertNotIn('private-fixture', self.fp_path().read_text()); self.assertNotIn('Unknown', self.fp_path().read_text())

    def test_failure_delta_is_complete_opaque_and_does_not_change_raw_rejection(self):
        self.bound(); self.config.write_bytes(self.original.replace(b'private-fixture-do-not-export', b'private-new-fixture'))
        reads = []; original = subject.raw_file
        def read(path, *args):
            if path in (self.config, self.sidecar, self.fp_path()): reads.append(path)
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): facts = self.reject()
        self.assertEqual(reads.count(self.config), 1); self.assertEqual(reads.count(self.fp_path()), 1)
        self.assertTrue(facts['ini_fingerprint_origin_valid']); delta = facts['ini_key_delta']
        self.assertTrue(delta['available']); self.assertEqual(len(delta['changed']), 1); self.assertEqual(delta['unchanged_keys'], 3)
        self.assertEqual(delta['added_key_sha256'], []); self.assertEqual(delta['removed_key_sha256'], [])
        self.assertEqual(delta['before_pairs_sha256'], json.loads(self.fp_path().read_bytes())['fingerprints']['pairs_sha256'])
        self.assertFalse(facts['config_hash_matches']); self.assertFalse(facts['ini_full_map_equal'])
        self.assertNotIn('private', self.failure.read_text()); self.assertNotIn('Unknown', self.failure.read_text())

    def test_partial_bad_owner_raw_hash_canonical_or_fingerprint_sidecar_stays_unavailable(self):
        self.bound(); path = self.fp_path(); original = path.read_bytes(); projection = subject.expected_ini_projection(self.ctx, self.binding)
        cases = [b'{']
        for mutate in [lambda v: v['owner'].update(uid=False), lambda v: v.update(config_sha256='0' * 64),
                       lambda v: v['fingerprints'].update(canonical_sha256='0' * 64),
                       lambda v: v['fingerprints'].update(pairs_sha256='0' * 64), lambda v: v.update(private='do-not-export')]:
            value = json.loads(original); mutate(value); cases.append(json.dumps(value).encode())
        for raw in cases:
            path.write_bytes(raw); self.assertIsNone(subject.expected_ini_fingerprints(self.ctx, self.binding, projection)); self.assertEqual(path.read_bytes(), raw)
        self.config.write_bytes(self.original + b'Other=x\n'); facts = self.reject()
        self.assertFalse(facts['ini_fingerprint_origin_valid']); self.assertFalse(facts['ini_key_delta']['available'])

    def test_partial_opaque_write_is_not_retried_and_does_not_recreate_metadata(self):
        self.config_ready(); original = subject.emit; attempted = []
        def emit(ctx, name, value):
            if name == 'avd-config-fingerprints':
                attempted.append(name)
                with self.fp_path().open('xb') as out: out.write(b'{')
                raise OSError('private fingerprint write unknown')
            return original(ctx, name, value)
        with patch.object(subject, 'emit', side_effect=emit): self.binding = subject.bind_avd(self.ctx)
        self.assertEqual(attempted, ['avd-config-fingerprints']); self.config.write_bytes(self.original + b'Other=x\n')
        facts = self.reject(); self.assertTrue(facts['ini_origin_record_valid']); self.assertFalse(facts['ini_key_delta']['available'])
        self.assertEqual(self.fp_path().read_bytes(), b'{'); self.assertNotIn('private fingerprint', self.failure.read_text())

    def test_unknown_opaque_metadata_read_occurs_once_without_retry_or_original_error_change(self):
        self.bound(); self.config.write_bytes(self.original + b'Other=x\n'); original = subject.raw_file; attempts = []
        def read(path, *args):
            if path == self.fp_path(): attempts.append(path); raise PermissionError('private read unknown')
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): facts = self.reject()
        self.assertEqual(attempts, [self.fp_path()]); self.assertFalse(facts['ini_key_delta']['available'])
        self.assertNotIn('private read', self.failure.read_text())

    def test_forged_current_pairs_are_refused_without_a_failure_export_or_owner_read(self):
        self.bound(); self.config.write_bytes(self.original + b'Other=x\n'); observed = subject.avd_config_facts(self.ctx, self.config.read_bytes())
        observed['ini_fingerprints']['pairs'][0][1] = 'private-bad-row'
        with patch.object(subject, 'avd_config_facts', return_value=observed), patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called(); self.assertFalse(self.failure.exists())

    def test_format_only_delta_has_no_added_removed_or_changed_keys_but_guard_still_rejects(self):
        self.bound(); changed = b'\n'.join(line.replace(b'=', b' = ', 1) for line in self.original.split(b'\n'))
        self.config.write_bytes(changed); facts = self.reject(); delta = facts['ini_key_delta']
        self.assertTrue(facts['ini_full_map_equal']); self.assertFalse(facts['config_hash_matches'])
        self.assertTrue(delta['available']); self.assertEqual(delta['changed'], []); self.assertEqual(delta['added_key_sha256'], [])
        self.assertEqual(delta['removed_key_sha256'], []); self.assertEqual(delta['unchanged_keys'], 4)


if __name__ == '__main__': unittest.main()
