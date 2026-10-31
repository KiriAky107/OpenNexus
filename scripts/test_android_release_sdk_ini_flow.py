"""Original bind/observe reads plus bounded diagnostic sidecars; no SDK/device execution."""
import hashlib
import json
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle


class IniDiagnosticFlow(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command
    prepared_image = SDKLifecycle.prepared_image
    ready = SDKLifecycle.ready

    def config_ready(self):
        self.ready(observe=False)
        directory = self.parent / 'avd/opennexus-ci-123456-1.avd'; directory.mkdir()
        self.config = directory / 'config.ini'
        self.original = (b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\n'
                         b'abi.type=x86_64\ntag.id=google_apis\nUnknown=private-fixture-do-not-export\n')
        self.config.write_bytes(self.original)
        self.sidecar = self.ctx['evidence'] / 'sdk-avd-config-semantics.json'
        self.failure = self.ctx['evidence'] / 'sdk-avd-binding-failure.json'

    def bound(self):
        self.config_ready(); self.binding = subject.bind_avd(self.ctx)

    def reject(self):
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called()
        return json.loads(self.failure.read_bytes())['facts']

    def test_prelaunch_sidecar_uses_only_original_bind_read_and_bound_raw_hash(self):
        self.config_ready(); original = subject.raw_file; reads = []
        def read(path, *args):
            if path == self.config: reads.append(path)
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): self.binding = subject.bind_avd(self.ctx)
        self.assertEqual(reads, [self.config]); value = json.loads(self.sidecar.read_bytes())
        self.assertEqual(value['owner'], self.ctx['owner']); self.assertEqual(value['stage'], 'bind-original-config')
        self.assertEqual(value['config_sha256'], self.binding['config_sha256'])
        self.assertEqual(value['projection']['source_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(subject.expected_ini_projection(self.ctx, self.binding), value['projection'])
        self.assertTrue(value['projection']['available']); self.assertFalse(value['runtime_verified'])
        self.assertNotIn('private-fixture', self.sidecar.read_text()); self.assertNotIn('Unknown', self.sidecar.read_text())

    def test_same_semantics_still_refuses_raw_change_and_does_not_read_config_twice(self):
        self.bound()
        changed = (b'; synthetic vendor-style formatting\r\nUnknown = private-fixture-do-not-export\r\n'
                   b'tag.id = google_apis\r\nabi.type = x86_64\r\n'
                   b'image.sysdir.1 = system-images/android-36/google_apis/x86_64/\r\n')
        self.config.write_bytes(changed); reads = []; original = subject.raw_file
        def read(path, *args):
            if path in (self.config, self.sidecar): reads.append(path)
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): facts = self.reject()
        self.assertEqual(reads.count(self.config), 1); self.assertEqual(reads.count(self.sidecar), 1)
        self.assertFalse(facts['config_hash_matches']); self.assertFalse(facts['selected_keys_unique'])
        self.assertTrue(facts['ini_origin_record_valid']); self.assertTrue(facts['ini_full_map_equal'])
        self.assertTrue(all(facts['ini_projection'][k] for k in ('available', 'image_literal_matches', 'abi_x86_64', 'tag_google_apis')))
        self.assertNotIn('private-fixture', self.failure.read_text()); self.assertNotIn('Unknown', self.failure.read_text())
        self.assertFalse(json.loads(self.failure.read_bytes())['whole_passed'])

    def test_an_unknown_added_key_changes_complete_map_and_is_never_ignored(self):
        self.bound(); self.config.write_bytes(self.original + b'Another=private-added-fixture\n')
        facts = self.reject(); self.assertTrue(facts['ini_origin_record_valid']); self.assertFalse(facts['ini_full_map_equal'])
        self.assertNotEqual(facts['expected_ini_projection']['canonical_sha256'], facts['ini_projection']['canonical_sha256'])
        self.assertEqual(facts['ini_projection']['key_count'], facts['expected_ini_projection']['key_count'] + 1)
        self.assertNotIn('private-added', self.failure.read_text()); self.assertNotIn('Another', self.failure.read_text())

    def test_wrong_owner_hash_types_partial_duplicate_or_private_sidecar_is_unavailable(self):
        self.bound(); original = self.sidecar.read_bytes(); base = json.loads(original)
        cases = []
        for mutate in [lambda v: v['owner'].update(uid=False), lambda v: v.update(config_sha256='0' * 64),
                       lambda v: v['projection'].update(source_sha256='0' * 64),
                       lambda v: v['projection'].update(key_count=True), lambda v: v.update(private='do-not-export')]:
            value = json.loads(original); mutate(value); cases.append(json.dumps(value).encode())
        cases += [b'{', original.rstrip()[:-1] + b',"stage":"bind-original-config"}']
        for raw in cases:
            self.sidecar.write_bytes(raw)
            self.assertIsNone(subject.expected_ini_projection(self.ctx, self.binding))
            self.assertEqual(self.sidecar.read_bytes(), raw)
        self.config.write_bytes(self.original + b'Other=x\n')
        facts = self.reject(); self.assertFalse(facts['ini_origin_record_valid']); self.assertIsNone(facts['ini_full_map_equal'])
        self.assertIsNone(facts['expected_ini_projection'])

    def test_partial_optional_write_never_retries_or_changes_original_bind_and_guard(self):
        self.config_ready(); original = subject.emit; sidecar_attempts = []
        def emit(ctx, name, value):
            if name == 'avd-config-semantics':
                sidecar_attempts.append(name)
                with self.sidecar.open('xb') as output: output.write(b'{')
                raise OSError('private optional write unknown')
            return original(ctx, name, value)
        with patch.object(subject, 'emit', side_effect=emit): self.binding = subject.bind_avd(self.ctx)
        self.assertEqual(sidecar_attempts, ['avd-config-semantics']); self.assertFalse(self.binding['runtime_verified'])
        self.assertEqual(self.sidecar.read_bytes(), b'{'); self.config.write_bytes(self.original + b'Other=x\n')
        facts = self.reject(); self.assertFalse(facts['ini_origin_record_valid']); self.assertIsNone(facts['ini_full_map_equal'])
        self.assertEqual(self.sidecar.read_bytes(), b'{'); self.assertNotIn('private optional', self.failure.read_text())

    def test_existing_sidecar_is_preserved_without_replacement_or_bind_reentry(self):
        self.config_ready(); self.sidecar.write_bytes(b'preserve-original-fixture')
        self.binding = subject.bind_avd(self.ctx); self.assertEqual(self.sidecar.read_bytes(), b'preserve-original-fixture')
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'): subject.bind_avd(self.ctx)
        self.assertEqual(self.sidecar.read_bytes(), b'preserve-original-fixture')
        self.config.write_bytes(self.original + b'Other=x\n'); self.assertFalse(self.reject()['ini_origin_record_valid'])

    def test_unknown_metadata_read_is_not_retried_and_preserves_original_failure(self):
        self.bound(); self.config.write_bytes(self.original + b'Other=x\n'); original = subject.raw_file; reads = []
        def read(path, *args):
            if path == self.sidecar:
                reads.append(path); raise PermissionError('private unavailable sidecar')
            return original(path, *args)
        with patch.object(subject, 'raw_file', side_effect=read): facts = self.reject()
        self.assertEqual(reads, [self.sidecar]); self.assertFalse(facts['ini_origin_record_valid'])
        self.assertIsNone(facts['ini_full_map_equal']); self.assertNotIn('private unavailable', self.failure.read_text())

    def test_forged_current_projection_is_rejected_without_exporting_private_fields(self):
        self.bound(); self.config.write_bytes(self.original + b'Other=x\n'); observed = subject.avd_config_facts(self.ctx, self.config.read_bytes())
        observed['ini_projection']['key_count'] = True
        with patch.object(subject, 'avd_config_facts', return_value=observed), patch.object(subject, 'proc') as proc, \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'): subject.observe(self.ctx)
        proc.assert_not_called(); self.assertFalse(self.failure.exists())

    def test_unavailable_current_full_map_stays_null_instead_of_discarding_duplicate_key(self):
        self.bound(); self.config.write_bytes(self.original + b'Unknown=private-duplicate-fixture\n')
        facts = self.reject(); self.assertTrue(facts['ini_origin_record_valid'])
        self.assertFalse(facts['ini_projection']['available']); self.assertEqual(facts['ini_projection']['unavailable_reason'], 'duplicate')
        self.assertIsNone(facts['ini_projection']['canonical_sha256']); self.assertIsNone(facts['ini_full_map_equal'])
        self.assertNotIn('private-duplicate', self.failure.read_text())

    def test_original_success_is_still_original_owner_record_without_failure_facts(self):
        self.bound(); (self.parent / 'emulator.pid').write_bytes(b'321\n')
        identity = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=17, state='S', executable=str(self.ctx['launcher']))
        with patch.object(subject, 'proc', return_value=identity): result = subject.observe(self.ctx)
        self.assertEqual(set(result), {'owner', 'pid', 'uid', 'start_ticks', 'runtime_verified'})
        self.assertFalse(result['runtime_verified']); self.assertTrue(self.sidecar.exists()); self.assertFalse(self.failure.exists())


if __name__ == '__main__': unittest.main()
