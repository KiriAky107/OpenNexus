"""Owned layout integration uses real tiny files and original records; no SDK/device execution."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle, BOOT
from test_android_release_sdk_avd_context import OwnedAvdContext


class OwnedLayoutFlow(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command
    create_command = OwnedAvdContext.create_command

    def image(self):
        self.assertTrue(subject.prepare(self.ctx)['passed'])
        with patch.object(subject.subprocess, 'run', side_effect=self.command):
            self.assertTrue(subject.install_image(self.ctx)['passed'])

    def laid_out(self):
        self.image(); value = subject.prepare_layout(self.ctx)
        self.assertTrue(value['passed'], value.get('guard_code')); return value

    def probed(self):
        self.laid_out(); subject.select_launcher(self.ctx)
        with patch.object(subject.subprocess, 'run', side_effect=self.command):
            self.assertTrue(subject.probe(self.ctx)['passed'])

    def snapshot(self):
        return {p.relative_to(self.ctx['sdk']).as_posix(): p.read_bytes()
                for p in self.ctx['sdk'].rglob('*') if p.is_file()}

    def test_real_layout_records_to_original_probes_AVD_observer_and_absence_cleanup(self):
        before = self.snapshot(); self.probed()
        with patch.object(subject.subprocess, 'run', side_effect=self.create_command):
            created = subject.create_avd(self.ctx)
        self.assertTrue(created['passed'], created.get('guard_code'))
        dependencies = self.ctx['evidence'] / 'sdk-runtime-dependencies-result.json'
        self.assertEqual(created['lineage']['runtime-dependencies-result'], hashlib.sha256(dependencies.read_bytes()).hexdigest())
        (self.parent / 'emulator.pid').write_bytes(b'321\n')
        identity = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=17, state='S', executable=str(self.ctx['launcher']))
        with patch.object(subject, 'proc', return_value=identity): value = subject.observe(self.ctx)
        self.assertEqual(value['pid'], 321); self.assertFalse(value['runtime_verified'])
        with patch.object(subject, 'proc', return_value=None), patch.object(subject, 'sdk_processes', return_value=[]), \
             patch.object(subject.subprocess, 'run', side_effect=self.command):
            removed = subject.cleanup(self.ctx)
        self.assertTrue(removed['SDK_removed_here']); self.assertEqual(before, self.snapshot())
        self.assertFalse(self.ctx['root'].exists()); self.assertEqual(len(self.network), 1)

    def test_hosted_context_boot_and_original_owner_refuse_before_any_copy(self):
        self.image()
        for changes in ({'RUNNER_ENVIRONMENT': 'self-hosted'}, {'RUNTIME_SCENE': 'sync'}, {'GITHUB_SHA': 'b' * 40}):
            with patch.dict(subject.os.environ, changes), patch.object(subject.layout, 'prepare') as copy, self.assertRaises(subject.kernel.ArchiveError):
                subject.prepare_layout(self.ctx)
            copy.assert_not_called()
        with patch.object(subject, 'boot', return_value='22345678-1234-4234-8234-123456789abc'), \
             patch.object(subject.layout, 'prepare') as copy, self.assertRaises(subject.kernel.ArchiveError):
            subject.prepare_layout(self.ctx)
        copy.assert_not_called(); self.assertFalse((self.ctx['evidence'] / 'sdk-runtime-dependencies-intent.json').exists())

    def test_missing_layout_blocks_selection_image_root_and_AVD_without_commands(self):
        self.image()
        with patch.object(subject.subprocess, 'run') as command:
            for action in (subject.select_launcher, subject.image_root, subject.create_avd):
                with self.assertRaises((subject.kernel.ArchiveError, FileNotFoundError)): action(self.ctx)
        command.assert_not_called(); self.assertFalse((self.ctx['evidence'] / 'sdk-launch-selection-intent.json').exists())

    def test_unknown_original_intent_write_is_never_reentered_or_repaired(self):
        self.image(); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'runtime-dependencies-intent': raise OSError('private write result unknown')
            return path
        with patch.object(subject, 'emit', side_effect=emit), patch.object(subject.layout, 'prepare') as copy, self.assertRaises(OSError):
            subject.prepare_layout(self.ctx)
        copy.assert_not_called()
        with patch.object(subject.layout, 'prepare') as copy, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.prepare_layout(self.ctx)
        copy.assert_not_called(); self.assertFalse((self.ctx['evidence'] / 'sdk-runtime-dependencies-result.json').exists())

    def test_partial_copy_uses_kernel_owned_cleanup_and_never_stops_or_retries(self):
        self.image(); before = self.snapshot(); original = subject.layout._copy_file
        def copy(*args): original(*args); raise OSError('private fixture copy result unknown')
        with patch.object(subject.layout, '_copy_file', side_effect=copy), patch.object(subject.subprocess, 'run') as command:
            result = subject.prepare_layout(self.ctx)
        command.assert_not_called(); self.assertFalse(result['passed']); self.assertFalse(result['runtime_verified'])
        self.assertNotIn('private fixture', json.dumps(result)); self.assertEqual(before, self.snapshot())
        original_result = json.loads((self.ctx['evidence'] / 'sdk-layout-result.json').read_bytes())
        self.assertTrue(original_result['failed_copy_removed'])
        self.assertFalse((self.ctx['root'] / 'runtime/platform-tools').exists()); self.assertTrue(self.ctx['root'].exists())
        with self.assertRaises(subject.kernel.ArchiveError): subject.prepare_layout(self.ctx)

    def test_original_image_changed_during_copy_cannot_make_a_bound_layout(self):
        self.image(); original = subject.layout._copy_file; changed = False
        def copy(*args):
            nonlocal changed
            original(*args)
            if not changed:
                changed = True
                path = self.ctx['root'] / 'runtime/system-images/android-36/google_apis/x86_64/source.properties'
                path.write_bytes(path.read_bytes() + b'changed')
        with patch.object(subject.layout, '_copy_file', side_effect=copy): result = subject.prepare_layout(self.ctx)
        self.assertFalse(result['passed']); self.assertTrue(self.ctx['root'].exists())
        kernel_result = json.loads((self.ctx['evidence'] / 'sdk-layout-result.json').read_bytes())
        self.assertTrue(kernel_result['cleanup_unknown']); self.assertFalse(kernel_result['failed_copy_removed'])
        with self.assertRaises(subject.kernel.ArchiveError): subject.select_launcher(self.ctx)

    def test_source_or_target_or_lineage_change_rejects_without_reconstructing_records(self):
        self.laid_out(); root = self.ctx['root'] / 'runtime'
        wrapper = self.ctx['evidence'] / 'sdk-runtime-dependencies-result.json'; before = wrapper.read_bytes()
        for path in (self.adb, root / 'platform-tools/adb', self.ctx['evidence'] / 'sdk-image-result.json'):
            raw = path.read_bytes(); path.write_bytes(raw + b'changed')
            with self.assertRaises((subject.kernel.ArchiveError, json.JSONDecodeError)): subject.layout_ready(self.ctx)
            path.write_bytes(raw); self.assertEqual(wrapper.read_bytes(), before)
        value = json.loads(before); value['extra'] = 'private fixture'; wrapper.write_text(json.dumps(value))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_JSON'): subject.layout_ready(self.ctx)

    def test_existing_target_and_original_records_cannot_be_replaced_or_copied_twice(self):
        self.image(); target = self.ctx['root'] / 'runtime/platform-tools'; target.mkdir()
        keep = target / 'keep'; keep.write_bytes(b'original fixture')
        value = subject.prepare_layout(self.ctx); self.assertFalse(value['passed'])
        self.assertEqual(keep.read_bytes(), b'original fixture')
        with patch.object(subject.layout, 'prepare') as copy, self.assertRaises(subject.kernel.ArchiveError): subject.prepare_layout(self.ctx)
        copy.assert_not_called()

    def test_copy_changed_by_probe_refuses_second_command_and_version_receipt(self):
        self.laid_out(); subject.select_launcher(self.ctx)
        def command(argv, **kwargs):
            result = self.command(argv, **kwargs)
            (self.ctx['root'] / 'runtime/platform-tools/adb').write_bytes(b'changed')
            return result
        with patch.object(subject.subprocess, 'run', side_effect=command) as run: value = subject.probe(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(run.call_count, 1)
        self.assertFalse((self.parent / 'emulator-version.txt').exists())

    def test_copy_changed_by_AVD_command_refuses_before_original_binding_or_observation(self):
        self.probed()
        def command(argv, **kwargs):
            value = self.create_command(argv, **kwargs)
            path = self.ctx['root'] / 'runtime/platform-tools/adb'; before = path.stat()
            data = path.read_bytes(); path.write_bytes(data[:-1] + b'X'); os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return value
        with patch.object(subject.subprocess, 'run', side_effect=command) as run: value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(run.call_count, 1); self.assertIsNone(value['binding'])
        self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())

    def test_copied_dependency_process_is_busy_for_cleanup_but_never_an_emulator_owner(self):
        self.probed()
        with patch.object(subject.subprocess, 'run', side_effect=self.create_command): self.assertTrue(subject.create_avd(self.ctx)['passed'])
        executable = str(self.ctx['root'] / 'runtime/platform-tools/adb')
        self.assertTrue(subject.owns_dependency_executable(self.ctx, executable)); self.assertFalse(subject.owns_executable(self.ctx, executable))
        (self.parent / 'emulator.pid').write_bytes(b'321\n')
        identity = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=17, state='S', executable=executable)
        with patch.object(subject, 'proc', return_value=identity), self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_PROC_IDENTITY'):
            subject.observe(self.ctx)
        self.assertFalse((self.ctx['evidence'] / 'sdk-launch-observed.json').exists())
        proc_root = Path('/proc'); pid = proc_root / '321'; original_iter = Path.iterdir; original_stat = Path.stat
        def iterdir(path): return iter([pid]) if path == proc_root else original_iter(path)
        def stat(path, *args, **kwargs):
            if path == pid: return type('Stat', (), {'st_uid': self.ctx['owner']['uid']})()
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, 'iterdir', iterdir), patch.object(Path, 'stat', stat), \
             patch.object(subject, 'sdk_stat', return_value=(self.ctx['owner']['uid'], 17, b'S')), \
             patch.object(subject.os, 'readlink', return_value=executable):
            self.assertEqual(subject.sdk_processes(self.ctx), [321])

    def test_fixed_new_CLI_action_rejects_all_parameters_before_context(self):
        for args in (['prepare-layout', '/private'], ['prepare-layout', '--package', 'platform-tools'], ['prepare-any-layout']):
            with patch.object(subject, 'context') as context, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_UNKNOWN_ACTION'):
                subject.main(args)
            context.assert_not_called()
        self.image(); output = io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(subject.main(['prepare-layout']), 0)
        value = json.loads(output.getvalue()); self.assertTrue(value['passed']); self.assertFalse(value['runtime_verified'])


if __name__ == '__main__': unittest.main()
