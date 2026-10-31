"""Real owned tiny files/receipts, controlled vendor/host responses; never launches an AVD."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle
from test_android_release_sdk_avd_context import OwnedAvdContext, CONFIG


class OwnedConfigPreparation(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command
    prepare_probed = OwnedAvdContext.prepare_probed
    create_command = OwnedAvdContext.create_command

    def start(self):
        self.prepare_probed()
        self.name, self.config, self.locator, self.temporary = subject.config_owned.paths(self.ctx)

    def create(self):
        with patch.object(subject.subprocess, 'run', side_effect=self.create_command): return subject.create_avd(self.ctx)

    def receipt(self, suffix): return self.ctx['evidence'] / ('sdk-avd-config-prepare-' + suffix + '.json')

    def test_once_owned_replacement_then_binding_and_observe_use_exact_new_bytes(self):
        self.start(); before = {str(p): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()}
        value = self.create(); self.assertTrue(value['passed'], value)
        result = subject.config_owned.ready(self.ctx, subject)
        self.assertTrue(result['replace_returned']); self.assertTrue(result['temporary_absent']); self.assertFalse(result['runtime_verified'])
        self.assertFalse(self.temporary.exists()); current = self.config.read_bytes()
        self.assertIn(b'disk.dataPartition.size = 6442450944\n', current); self.assertIn(b'Unknown1 = \n', current)
        self.assertEqual(value['binding']['config_sha256'], hashlib.sha256(current).hexdigest())
        self.assertEqual(value['binding']['config_preparation_sha256'], value['config_preparation_sha256'])
        self.assertNotIn('private-value', '\n'.join(p.read_text() for p in self.ctx['evidence'].glob('sdk-*.json')))
        (self.parent / 'emulator.pid').write_bytes(b'321\n'); reads = []; original = subject.raw_file
        def read(path, *args):
            if path == self.config: reads.append(path)
            return original(path, *args)
        identity = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=17, state='S', executable=str(self.ctx['launcher']))
        with patch.object(subject, 'raw_file', side_effect=read), patch.object(subject, 'proc', return_value=identity):
            observed = subject.observe(self.ctx)
        self.assertEqual(reads, [self.config]); self.assertFalse(observed['runtime_verified'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()})

    def test_runtime_format_only_change_still_fails_full_raw_guard_before_PID(self):
        self.start(); self.assertTrue(self.create()['passed']); self.config.write_bytes(self.config.read_bytes() + b'# format only\n')
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        proc.assert_not_called(); facts = json.loads((self.ctx['evidence'] / 'sdk-avd-binding-failure.json').read_bytes())['facts']
        self.assertTrue(facts['ini_full_map_equal']); self.assertFalse(facts['config_hash_matches'])

    def test_existing_preparation_intent_refuses_before_vendor_command_and_is_preserved(self):
        self.start(); self.receipt('intent').write_bytes(b'original unknown intent')
        with patch.object(subject.subprocess, 'run') as run, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.create_avd(self.ctx)
        run.assert_not_called(); self.assertEqual(self.receipt('intent').read_bytes(), b'original unknown intent')

    def test_existing_temporary_file_is_never_deleted_or_overwritten(self):
        self.start()
        def command(argv, **kwargs):
            value = self.create_command(argv, **kwargs); self.temporary.write_bytes(b'original unowned temporary'); return value
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(self.config.read_bytes(), CONFIG)
        self.assertEqual(self.temporary.read_bytes(), b'original unowned temporary'); self.assertFalse(self.receipt('intent').exists())

    def test_source_same_length_change_after_attempt_is_not_replaced(self):
        self.start(); original = subject.emit
        changed = CONFIG.replace(b'private-value-0', b'private-value-X')
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'avd-config-prepare-attempt':
                stamp = self.config.stat().st_mtime_ns; self.config.write_bytes(changed); os.utime(self.config, ns=(stamp, stamp))
            return path
        with patch.object(subject, 'emit', side_effect=emit): value = self.create()
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_CONFIG_CHANGED')
        self.assertEqual(self.config.read_bytes(), changed); self.assertFalse(self.temporary.exists())
        self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())

    def test_partial_own_temporary_write_is_cleaned_without_changing_original_config(self):
        self.start(); original = subject.config_owned.os.fdopen
        class Partial:
            def __init__(self, file): self.file = file
            def __enter__(self): self.file.__enter__(); return self
            def __exit__(self, *args): return self.file.__exit__(*args)
            def write(self, data): self.file.write(data[:17]); raise OSError('private partial write')
        with patch.object(subject.config_owned.os, 'fdopen', side_effect=lambda *a, **k: Partial(original(*a, **k))):
            value = self.create()
        self.assertFalse(value['passed']); result = json.loads(self.receipt('result').read_bytes())
        self.assertFalse(result['replace_returned']); self.assertTrue(result['temporary_absent']); self.assertFalse(result['cleanup_unknown'])
        self.assertEqual(self.config.read_bytes(), CONFIG); self.assertNotIn('private partial', json.dumps(value))

    def test_unknown_replace_that_already_happened_does_not_bind_restore_or_repeat(self):
        self.start(); original = subject.config_owned.os.replace; calls = []
        def replace(a, b):
            calls.append((a, b)); original(a, b); raise OSError('private replacement result unknown')
        with patch.object(subject.config_owned.os, 'replace', side_effect=replace): value = self.create()
        self.assertEqual(len(calls), 1); self.assertFalse(value['passed'])
        self.assertIn(b'disk.dataPartition.size = 6442450944', self.config.read_bytes())
        self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())
        self.assertFalse(json.loads(self.receipt('result').read_bytes())['replace_returned'])
        with patch.object(subject.subprocess, 'run') as run, self.assertRaises(subject.kernel.ArchiveError): subject.create_avd(self.ctx)
        run.assert_not_called()

    def test_parent_replacement_before_commit_keeps_both_original_and_replacement(self):
        self.start(); original = subject.config_owned.os.fdopen; moved = None
        class Relocate:
            def __init__(inner, file): inner.file = file
            def __enter__(inner): return inner.file.__enter__()
            def __exit__(inner, *args):
                nonlocal moved
                result = inner.file.__exit__(*args); moved = self.config.parent.with_name('old-owned-avd')
                self.config.parent.rename(moved); self.config.parent.mkdir(); self.config.write_bytes(b'preserve replacement')
                return result
        with patch.object(subject.config_owned.os, 'fdopen', side_effect=lambda *a, **k: Relocate(original(*a, **k))):
            value = self.create()
        self.assertFalse(value['passed']); self.assertEqual(self.config.read_bytes(), b'preserve replacement')
        self.assertEqual((moved / 'config.ini').read_bytes(), CONFIG)
        self.assertTrue((moved / self.temporary.name).exists()); self.assertTrue(json.loads(self.receipt('result').read_bytes())['cleanup_unknown'])

    def test_unrelated_bytes_in_owned_temporary_are_preserved_as_cleanup_unknown(self):
        self.start(); original = subject.config_owned.os.fsync
        def fsync(fd):
            original(fd)
            if self.temporary.exists() and os.fstat(fd).st_ino == self.temporary.stat().st_ino:
                self.temporary.write_bytes(b'unrelated bytes')
        with patch.object(subject.config_owned.os, 'fsync', side_effect=fsync): value = self.create()
        self.assertFalse(value['passed']); self.assertEqual(self.temporary.read_bytes(), b'unrelated bytes')
        self.assertEqual(self.config.read_bytes(), CONFIG); self.assertTrue(json.loads(self.receipt('result').read_bytes())['cleanup_unknown'])

    def test_bad_partial_boolean_private_or_changed_receipts_never_become_ready(self):
        self.start(); self.assertTrue(self.create()['passed']); path = self.receipt('intent'); original = path.read_bytes()
        cases = [b'{']
        for mutate in [lambda v: v['owner'].update(uid=False), lambda v: v['plan'].update(keys=True),
                       lambda v: v['chain']['avd'].__setitem__(0, True), lambda v: v.update(private='never-export'),
                       lambda v: v.update(create_intent_sha256='0' * 64), lambda v: v['plan'].update(after_raw_sha256='0' * 64)]:
            value = json.loads(original); mutate(value); cases.append(json.dumps(value).encode())
        raw = self.config.read_bytes()
        for data in cases:
            path.write_bytes(data)
            with self.assertRaises(Exception): subject.config_owned.ready(self.ctx, subject)
            self.assertEqual(self.config.read_bytes(), raw); self.assertEqual(path.read_bytes(), data)

    def test_metadata_disappearance_cannot_downgrade_new_binding_to_legacy_path(self):
        self.start(); self.assertTrue(self.create()['passed'])
        for path in [self.ctx['evidence'] / 'sdk-avd-create-intent.json', self.receipt('intent'), self.receipt('result')]:
            path.rename(path.with_suffix('.fixture-held'))
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_JSON'):
            subject.observe(self.ctx)
        proc.assert_not_called()

    def test_config_tamper_after_result_is_refused_before_original_binding(self):
        self.start(); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'avd-config-prepare-result': self.config.write_bytes(self.config.read_bytes().replace(b'private-value-0', b'private-value-X'))
            return path
        with patch.object(subject, 'emit', side_effect=emit): value = self.create()
        self.assertFalse(value['passed']); self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())

    def test_owner_changed_after_config_attempt_never_reaches_replace_or_cleanup_delete(self):
        self.start(); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'avd-config-prepare-attempt': (ctx['root'] / 'owner.json').write_bytes(b'changed owner')
            return path
        with patch.object(subject, 'emit', side_effect=emit), patch.object(subject.config_owned.os, 'replace') as replace:
            value = self.create()
        replace.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(self.config.read_bytes(), CONFIG)
        self.assertTrue(json.loads(self.receipt('result').read_bytes())['cleanup_unknown'])

    def test_config_alias_is_refused_before_preparation_intent(self):
        self.start(); original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == self.config: return self.workspace / 'different-config'
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve): value = self.create()
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_CONFIG_OWNER')
        self.assertFalse(self.receipt('intent').exists()); self.assertEqual(self.config.read_bytes(), CONFIG)

    def test_late_temporary_change_after_receipt_read_is_not_committed_or_cleaned(self):
        self.start(); original = subject.raw_file; changed = []
        def read(path, *args):
            data = original(path, *args)
            if path == self.receipt('attempt') and self.temporary.exists() and not changed:
                self.temporary.write_bytes(b'unrelated late contents'); changed.append(path)
            return data
        with patch.object(subject, 'raw_file', side_effect=read), patch.object(subject.config_owned.os, 'replace') as replace:
            value = self.create()
        replace.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(len(changed), 1)
        self.assertEqual(self.config.read_bytes(), CONFIG); self.assertEqual(self.temporary.read_bytes(), b'unrelated late contents')
        self.assertTrue(json.loads(self.receipt('result').read_bytes())['cleanup_unknown'])

    def test_unknown_intent_write_keeps_original_and_is_not_reentered(self):
        self.start(); original = subject.emit
        def emit(ctx, name, value):
            if name == 'avd-config-prepare-intent':
                self.receipt('intent').write_bytes(b'{'); raise OSError('private intent outcome unknown')
            return original(ctx, name, value)
        with patch.object(subject, 'emit', side_effect=emit): value = self.create()
        self.assertFalse(value['passed']); self.assertEqual(self.receipt('intent').read_bytes(), b'{')
        self.assertEqual(self.config.read_bytes(), CONFIG); self.assertFalse(self.receipt('attempt').exists())
        with patch.object(subject.subprocess, 'run') as run, self.assertRaises(subject.kernel.ArchiveError): subject.create_avd(self.ctx)
        run.assert_not_called(); self.assertNotIn('private intent', json.dumps(value))

    def test_preparation_receipt_changed_after_binding_blocks_observe_before_PID(self):
        self.start(); self.assertTrue(self.create()['passed']); raw = self.receipt('result').read_bytes()
        self.receipt('result').write_bytes(raw.replace(b'"passed": true', b'"passed": false'))
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_CONFIG_NOT_READY'):
            subject.observe(self.ctx)
        proc.assert_not_called()


if __name__ == '__main__': unittest.main()
