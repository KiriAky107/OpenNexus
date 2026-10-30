"""Failure-only SDK binding facts from the existing config read; no SDK/device execution."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle


class AvdBindingFailureFacts(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command
    prepared_image = SDKLifecycle.prepared_image
    ready = SDKLifecycle.ready

    def bound(self):
        self.ready(observe=False)
        directory = self.parent / 'avd/opennexus-ci-123456-1.avd'; directory.mkdir()
        self.config = directory / 'config.ini'
        self.original = b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\nabi.type=x86_64\ntag.id=google_apis\n'
        self.config.write_bytes(self.original); subject.bind_avd(self.ctx)
        return self.ctx['evidence'] / 'sdk-avd-binding-failure.json'

    def test_original_rejection_keeps_only_same_read_hashes_and_fixed_selection_booleans(self):
        path = self.bound(); changed = self.original + b'unknown.private=do-not-export-fixture\n'
        self.config.write_bytes(changed); reads = []; original = subject.raw_file
        def read(file, *args):
            if file == self.config: reads.append(file)
            return original(file, *args)
        with patch.object(subject, 'raw_file', side_effect=read), patch.object(subject, 'proc') as proc, \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called(); self.assertEqual(reads, [self.config])
        value = json.loads(path.read_bytes()); self.assertFalse(value['whole_passed']); self.assertFalse(value['runtime_verified'])
        self.assertEqual(value['guard_code'], 'SDK_AVD_BINDING'); self.assertEqual(value['stage'], 'observe-launch-binding')
        facts = value['facts']; self.assertTrue(facts['config_read']); self.assertFalse(facts['config_hash_matches'])
        self.assertEqual(facts['expected_config_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(facts['observed_config_sha256'], hashlib.sha256(changed).hexdigest())
        self.assertEqual(facts['config_bytes'], len(changed))
        self.assertTrue(all(facts[k] for k in ['binding_name_matches', 'binding_runtime_false', 'binding_image_matches',
            'config_ascii', 'selected_keys_unique', 'image_sysdir_literal_matches', 'abi_x86_64', 'tag_google_apis']))
        self.assertNotIn('do-not-export-fixture', path.read_text()); self.assertNotIn('unknown.private', path.read_text())

    def test_matching_config_and_unknown_process_never_create_binding_failure_facts(self):
        path = self.bound(); (self.parent / 'emulator.pid').write_bytes(b'321\n')
        with patch.object(subject, 'proc', return_value=None), self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_PROC_IDENTITY'):
            subject.observe(self.ctx)
        self.assertFalse(path.exists()); self.assertFalse((self.ctx['evidence'] / 'sdk-launch-observed.json').exists())

    def test_early_binding_mismatch_preserves_original_short_circuit_and_no_config_read(self):
        path = self.bound(); binding_path = self.ctx['evidence'] / 'sdk-avd-binding.json'
        value = json.loads(binding_path.read_bytes()); value['avd'] = 'controlled-other-name'; binding_path.write_text(json.dumps(value))
        original = subject.raw_file; reads = []
        def read(file, *args):
            if file == self.config: reads.append(file)
            return original(file, *args)
        with patch.object(subject, 'raw_file', side_effect=read), patch.object(subject, 'proc') as proc, \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called(); self.assertEqual(reads, [])
        facts = json.loads(path.read_bytes())['facts']; self.assertFalse(facts['binding_name_matches']); self.assertFalse(facts['config_read'])
        self.assertIsNone(facts['observed_config_sha256']); self.assertIsNone(facts['config_hash_matches'])
        self.assertNotIn('controlled-other-name', path.read_text())

    def test_unknown_failure_fact_write_preserves_guard_and_never_rewrites_original_file(self):
        path = self.bound(); self.config.write_bytes(self.original + b'unknown=private-fixture\n'); original = subject.emit
        def emit(ctx, name, value):
            result = original(ctx, name, value)
            if name == 'avd-binding-failure': raise OSError('private write result unknown')
            return result
        with patch.object(subject, 'emit', side_effect=emit) as writer, patch.object(subject, 'proc') as proc, \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        self.assertEqual(writer.call_count, 1); proc.assert_not_called(); before = path.read_bytes()
        binding = json.loads((self.ctx['evidence'] / 'sdk-avd-binding.json').read_bytes())
        image = json.loads((self.ctx['evidence'] / 'sdk-image-result.json').read_bytes())
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.record_avd_binding_failure(self.ctx, binding, image, binding['avd'], subject.avd_config_facts(self.ctx, self.config.read_bytes()))
        self.assertEqual(path.read_bytes(), before); self.assertNotIn('private write', path.read_text())

    def test_memory_projection_and_writer_reject_private_or_ambiguous_fields(self):
        raw = b'image.sysdir.1=/unowned-private\nabi.type=x86_64\nabi.type=x86_64\ntag.id=google_apis\nsecret=private-body\n'
        facts = subject.avd_config_facts(self.ctx, raw)
        self.assertFalse(facts['selected_keys_unique']); self.assertFalse(facts['image_sysdir_literal_matches']); self.assertFalse(facts['abi_x86_64'])
        self.assertNotIn('private', json.dumps(facts))
        non_ascii = subject.avd_config_facts(self.ctx, raw + b'\xff')
        self.assertFalse(non_ascii['config_ascii']); self.assertFalse(non_ascii['selected_keys_unique'])
        binding = dict(avd='opennexus-ci-123456-1', runtime_verified=False, image_properties_sha256='b' * 64, config_sha256='a' * 64)
        image = dict(image_properties=dict(sha256='b' * 64)); facts['private'] = 'body'
        with patch.object(subject, 'emit') as writer, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.record_avd_binding_failure(self.ctx, binding, image, binding['avd'], facts)
        writer.assert_not_called()

    def test_invalid_expected_hash_is_unavailable_without_exporting_or_changing_guard(self):
        path = self.bound(); binding_path = self.ctx['evidence'] / 'sdk-avd-binding.json'
        value = json.loads(binding_path.read_bytes()); value['config_sha256'] = 'private-invalid-original-value'; binding_path.write_text(json.dumps(value))
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called(); self.assertFalse(path.exists())


if __name__ == '__main__': unittest.main()
