"""Real tiny records and controlled original proc reads; no actual Linux/SDK."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
import android_release_sdk_proc_facts as facts
import android_release_sdk_proc_observation as observation


class ProcSequence:
    def __init__(self, *, failed=None, missing=True, exists=True, zombie=False, changed=False, exists_error=None):
        self.failed = failed
        self.error = FileNotFoundError('private proc path') if missing else PermissionError('private executable')
        self.directory_exists = exists
        self.exists_error = exists_error
        self.zombie = zombie
        self.changed = changed
        self.events = []
        self.stats = self.reads = 0

    def event(self, step):
        self.events.append(step)
        if self.failed == step:
            raise self.error

    def __truediv__(self, value):
        if value == '321':
            return self
        if value == 'stat':
            return SimpleNamespace(read_bytes=self.read_bytes)
        if value == 'exe':
            return 'fixed-executable-path-token'
        raise AssertionError('Unexpected proc path')

    def stat(self):
        self.stats += 1
        self.event('DIRECTORY_BEFORE_STAT' if self.stats == 1 else 'DIRECTORY_AFTER_STAT')
        return SimpleNamespace(st_uid=1001)

    def read_bytes(self):
        self.reads += 1
        self.event('FIRST_STAT_READ' if self.reads == 1 else 'SECOND_STAT_READ')
        ticks = b'18' if self.changed and self.reads == 2 else b'17'
        return b'321 (controlled) ' + (b'Z' if self.zombie else b'S') + b' ' + b'0 ' * 18 + ticks + b' 0 0'

    def readlink(self, path):
        if path != 'fixed-executable-path-token':
            raise AssertionError('Unexpected executable lookup')
        self.event('EXECUTABLE_READLINK')
        return '/owned-sdk/runtime/emulator/emulator'

    def exists(self):
        self.events.append('EXISTS_AFTER_FILE_MISSING')
        if self.exists_error is not None:
            raise self.exists_error
        return self.directory_exists

    def call(self, holder=None, pid=321):
        with patch.object(subject, 'Path', return_value=self), patch.object(subject.os, 'readlink', side_effect=self.readlink):
            return subject.proc(pid, observation=holder)


class ProcReadObservation(unittest.TestCase):
    def test_all_five_missing_reads_keep_default_calls_guard_and_one_exists(self):
        for index, step in enumerate(facts.STEPS):
            default = ProcSequence(failed=step)
            with self.assertRaisesRegex(subject.kernel.ArchiveError, '^SDK_PROC_UNKNOWN$'):
                default.call()
            holder = observation.Observer()
            enabled = ProcSequence(failed=step)
            with self.assertRaisesRegex(subject.kernel.ArchiveError, '^SDK_PROC_UNKNOWN$'):
                enabled.call(holder)
            self.assertEqual(enabled.events, default.events)
            self.assertEqual(enabled.events, list(facts.STEPS[:index + 1]) + ['EXISTS_AFTER_FILE_MISSING'])
            self.assertEqual(facts.project(holder.fact)['read_step'], step)

    def test_unknown_absent_invalid_and_successful_calls_reset_stale_facts(self):
        holder = observation.Observer()
        for step in facts.STEPS:
            holder.fact = facts.capture('FIRST_STAT_READ', True, True)
            sequence = ProcSequence(failed=step, missing=False)
            with self.assertRaises(PermissionError) as caught:
                sequence.call(holder)
            self.assertIs(caught.exception, sequence.error)
            self.assertIsNone(holder.fact)
        holder.fact = facts.capture('FIRST_STAT_READ', True, True)
        sequence = ProcSequence(failed='FIRST_STAT_READ', exists=False)
        self.assertIsNone(sequence.call(holder)); self.assertIsNone(holder.fact)
        holder.fact = facts.capture('FIRST_STAT_READ', True, True)
        sequence = ProcSequence()
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAUNCH_PID'):
            sequence.call(holder, True)
        self.assertEqual(sequence.events, []); self.assertIsNone(holder.fact)
        for zombie in (False, True):
            sequence = ProcSequence(zombie=zombie); result = sequence.call(holder)
            self.assertEqual(result['state'], 'Z' if zombie else 'S'); self.assertIsNone(holder.fact)
            self.assertEqual('EXECUTABLE_READLINK' in sequence.events, not zombie)
        sequence = ProcSequence(changed=True)
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_PROC_IDENTITY'):
            sequence.call(holder)
        self.assertNotIn('DIRECTORY_AFTER_STAT', sequence.events); self.assertIsNone(holder.fact)

    def test_unknown_exists_or_optional_capture_keeps_original_exception(self):
        holder = observation.Observer(); error = OSError('private exists output')
        sequence = ProcSequence(failed='EXECUTABLE_READLINK', exists_error=error)
        with self.assertRaises(OSError) as caught:
            sequence.call(holder)
        self.assertIs(caught.exception, error); self.assertIsNone(holder.fact)
        sequence = ProcSequence(failed='EXECUTABLE_READLINK')
        with patch.object(subject.proc_facts, 'capture', side_effect=RuntimeError('private optional error')):
            with self.assertRaisesRegex(subject.kernel.ArchiveError, '^SDK_PROC_UNKNOWN$'):
                sequence.call(holder)
        self.assertIsNone(holder.fact); self.assertEqual(sequence.events.count('EXISTS_AFTER_FILE_MISSING'), 1)


class CleanupProcObservation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='onx-sdk-proc-observation-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve(); parent = self.folder / 'runtime'
        evidence = parent / 'origin-proof'; evidence.mkdir(parents=True)
        root = parent / 'sdk-owned-123456-1'; root.mkdir()
        owner = dict(GITHUB_RUN_ID='123456', GITHUB_RUN_ATTEMPT='1', GITHUB_SHA='a' * 40,
                     uid=self.folder.stat().st_uid, host_boot_id='12345678-1234-4234-8234-123456789abc')
        self.ctx = dict(owner=owner, parent=parent, evidence=evidence, root=root, sdk=self.folder / 'global-sdk')
        self.ctx['sdk'].mkdir(); self.global_file = self.ctx['sdk'] / 'preserve'; self.global_file.write_bytes(b'global original')
        (root / 'owner.json').write_bytes(json.dumps({k: owner[k] for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}, sort_keys=True).encode())
        kernel = evidence / 'sdk-kernel-result.json'; kernel.write_bytes(b'{"controlled":true}')
        subject.emit(self.ctx, 'prepare-result', dict(owner=owner, passed=True,
            root_identity=[root.stat().st_dev, root.stat().st_ino], launcher={},
            kernel_sha256=hashlib.sha256(kernel.read_bytes()).hexdigest(), runtime_verified=False))
        self.previous = dict(owner=owner, pid=321, uid=owner['uid'], start_ticks=17, runtime_verified=False)
        self.launch = subject.emit(self.ctx, 'launch-observed', self.previous)
        self.launch_sha = hashlib.sha256(self.launch.read_bytes()).hexdigest()
        self.sidecar = evidence / ('sdk-' + observation.NAME + '.json')

    def rejected(self, pid, *, observation=None):
        self.assertEqual(pid, 321)
        if observation is not None:
            observation.fact = facts.capture('EXECUTABLE_READLINK', True, True)
        raise subject.kernel.ArchiveError('SDK_PROC_UNKNOWN')

    def run_rejected(self):
        with patch.object(subject, 'boot', return_value=self.ctx['owner']['host_boot_id']), \
             patch.object(subject, 'proc', side_effect=self.rejected) as proc, \
             patch.object(subject, 'sdk_processes') as scan, patch.object(subject, 'adb_absent') as adb, \
             patch.object(subject.shutil, 'rmtree') as remove:
            result = subject.cleanup(self.ctx)
        proc.assert_called_once(); scan.assert_not_called(); adb.assert_not_called(); remove.assert_not_called()
        self.assertEqual(result['guard_code'], 'SDK_PROC_UNKNOWN'); self.assertFalse(result['passed'])
        self.assertFalse(result['SDK_removed_here']); self.assertTrue(result['no_stop_or_kill'])
        self.assertTrue(self.ctx['root'].exists()); self.assertEqual(self.global_file.read_bytes(), b'global original')
        return result

    def test_original_false_record_precedes_exclusive_scope_bound_fact_and_one_launch_read(self):
        reads = []; emitted = []; original_read = subject.raw_file; original_emit = subject.emit
        def read(path, *args):
            reads.append(path); return original_read(path, *args)
        def emit(ctx, name, value):
            if name == observation.NAME:
                self.assertTrue((ctx['evidence'] / 'sdk-cleanup-result.json').is_file())
                self.assertFalse(json.loads((ctx['evidence'] / 'sdk-cleanup-result.json').read_bytes())['passed'])
            emitted.append(name); return original_emit(ctx, name, value)
        with patch.object(subject, 'raw_file', side_effect=read), patch.object(subject, 'emit', side_effect=emit):
            result = self.run_rejected()
        self.assertEqual(reads.count(self.launch), 1)
        self.assertEqual(emitted, ['cleanup-intent', 'cleanup-result', observation.NAME])
        value = subject.read_cleanup_proc_failure(self.ctx, self.previous, self.launch_sha, result)
        self.assertTrue(observation.validate(value, self.ctx['owner'], self.previous, self.launch_sha, result))
        self.assertEqual(value['launch_record_raw_sha256'], self.launch_sha)
        self.assertFalse(value['current_process_identity_verified']); self.assertFalse(value['process_absence_verified'])
        self.assertLess(len(self.sidecar.read_bytes()), 4096)

    def test_launch_reader_has_original_closed_validation_and_same_buffer_SHA(self):
        original = subject.raw_file
        with patch.object(subject, 'raw_file', wraps=original) as read:
            previous, digest = subject.read_launch_for_cleanup(self.ctx)
        read.assert_called_once_with(self.launch); self.assertEqual(previous, self.previous); self.assertEqual(digest, self.launch_sha)
        with patch.object(subject.hashlib, 'sha256', side_effect=RuntimeError('private hash error')):
            previous, digest = subject.read_launch_for_cleanup(self.ctx)
        self.assertEqual(previous, self.previous); self.assertIsNone(digest)
        original_bytes = self.launch.read_bytes()
        for raw in (b'{"owner":', original_bytes.replace(b'"pid": 321', b'"pid": 321,"pid": 321'),
                    json.dumps(dict(self.previous, private='private raw metadata')).encode()):
            self.launch.write_bytes(raw)
            with self.assertRaises((subject.kernel.ArchiveError, json.JSONDecodeError)):
                subject.read_launch_for_cleanup(self.ctx)

    def test_missing_optional_SHA_or_observer_allocation_cannot_replace_original_result(self):
        original = subject.read_launch_for_cleanup
        def unknown_sha(ctx):
            value, _ = original(ctx); return value, None
        with patch.object(subject, 'read_launch_for_cleanup', side_effect=unknown_sha):
            result = self.run_rejected()
        self.assertFalse(self.sidecar.exists()); self.assertEqual(result['failure_stage'], 'original-pid')

    def test_optional_observer_factory_failure_keeps_original_false_result(self):
        with patch.object(subject.proc_observation, 'Observer', side_effect=RuntimeError('private allocation')):
            self.run_rejected()
        self.assertFalse(self.sidecar.exists())

    def test_original_result_emitter_unknown_write_has_no_optional_record(self):
        original = subject.emit; error = OSError('private original result write')
        def emit(ctx, name, value):
            if name == 'cleanup-result':
                (ctx['evidence'] / 'sdk-cleanup-result.json').write_bytes(b'{"passed":')
                raise error
            return original(ctx, name, value)
        with patch.object(subject, 'boot', return_value=self.ctx['owner']['host_boot_id']), \
             patch.object(subject, 'proc', side_effect=self.rejected), patch.object(subject, 'emit', side_effect=emit), \
             patch.object(subject.proc_observation, 'project') as project:
            with self.assertRaises(OSError) as caught:
                subject.cleanup(self.ctx)
        self.assertIs(caught.exception, error); project.assert_not_called(); self.assertFalse(self.sidecar.exists())
        self.assertEqual((self.ctx['evidence'] / 'sdk-cleanup-result.json').read_bytes(), b'{"passed":')

    def test_partial_optional_write_retains_original_result_and_never_reenters(self):
        original = subject.emit; writes = []
        def emit(ctx, name, value):
            if name == observation.NAME:
                writes.append(name)
                with self.sidecar.open('xb') as output:
                    output.write(b'{"schema":')
                raise OSError('private optional write')
            return original(ctx, name, value)
        with patch.object(subject, 'emit', side_effect=emit):
            self.run_rejected()
        self.assertEqual(writes, [observation.NAME]); self.assertEqual(self.sidecar.read_bytes(), b'{"schema":')
        with patch.object(subject, 'proc') as proc, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.cleanup(self.ctx)
        proc.assert_not_called(); self.assertEqual(self.sidecar.read_bytes(), b'{"schema":')

    def test_existing_optional_original_is_not_overwritten_or_retried(self):
        self.sidecar.write_bytes(b'keep original optional fixture')
        self.run_rejected(); self.assertEqual(self.sidecar.read_bytes(), b'keep original optional fixture')

    def test_reader_rejects_partial_private_duplicate_type_and_scope_changes(self):
        result = self.run_rejected(); raw = self.sidecar.read_bytes(); value = json.loads(raw)
        bad_values = [dict(value, private='private output'), dict(value, schema=True), dict(value, whole_cleanup_passed=0),
                      dict(value, launch_record_raw_sha256='0' * 64)]
        wrong_owner = deepcopy(value); wrong_owner['owner']['uid'] = False; bad_values.append(wrong_owner)
        wrong_previous = deepcopy(value); wrong_previous['previous_launch_identity']['pid'] = True; bad_values.append(wrong_previous)
        for data in [b'{"schema":', raw.replace(b'"schema": 1', b'"schema": 1,"schema":1', 1),
                     *[json.dumps(bad).encode() for bad in bad_values]]:
            self.sidecar.write_bytes(data)
            with self.assertRaises((subject.kernel.ArchiveError, json.JSONDecodeError)):
                subject.read_cleanup_proc_failure(self.ctx, self.previous, self.launch_sha, result)
        self.sidecar.write_bytes(raw)
        for previous, digest, changed in ((dict(self.previous, start_ticks=18), self.launch_sha, result),
                                         (self.previous, '0' * 64, result),
                                         (self.previous, self.launch_sha, dict(result, no_stop_or_kill=False))):
            with self.assertRaises(subject.kernel.ArchiveError):
                subject.read_cleanup_proc_failure(self.ctx, previous, digest, changed)

    def test_other_stage_or_unobserved_fact_has_no_record(self):
        def unknown(pid, *, observation=None):
            raise subject.kernel.ArchiveError('SDK_PROC_UNKNOWN')
        with patch.object(subject, 'boot', return_value=self.ctx['owner']['host_boot_id']), patch.object(subject, 'proc', side_effect=unknown):
            result = subject.cleanup(self.ctx)
        self.assertFalse(result['passed']); self.assertFalse(self.sidecar.exists())
        holder = observation.Observer(); holder.fact = facts.capture('FIRST_STAT_READ', True, True)
        for changes in (dict(failure_stage='sdk-processes'), dict(passed=True), dict(SDK_removed_here=True), dict(private='private')):
            self.assertIsNone(observation.project(holder, self.ctx['owner'], self.previous, self.launch_sha, dict(result, **changes)))

    def test_successful_cleanup_still_needs_two_scans_and_ADB_then_no_failure_metadata(self):
        with patch.object(subject, 'boot', return_value=self.ctx['owner']['host_boot_id']), \
             patch.object(subject, 'proc', return_value=None) as proc, patch.object(subject, 'sdk_processes', return_value=[]) as scan, \
             patch.object(subject, 'adb_absent', return_value=dict(owned_serial_absent=True, all_other_devices_absent=True)) as adb:
            result = subject.cleanup(self.ctx)
        self.assertTrue(result['passed']); self.assertTrue(result['SDK_removed_here'])
        self.assertEqual(proc.call_count, 2); self.assertEqual(scan.call_count, 2); adb.assert_called_once()
        self.assertFalse(self.ctx['root'].exists()); self.assertFalse(self.sidecar.exists())
        self.assertEqual(self.global_file.read_bytes(), b'global original')


if __name__ == '__main__':
    unittest.main()
