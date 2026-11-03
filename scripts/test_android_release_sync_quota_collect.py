"""Controlled device responses and new synthetic files, never a live stop gate."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import android_release_observation as observation
import android_release_sync_quota_collect as collect
import android_release_sync_quota_snapshot as snapshot
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_snapshot import original_fixture


class FixtureAdmission(collect.OriginalQuotaAdmission):
    """A fixture dependency, not an original-record or actual stop verifier."""
    def __init__(self, scope, identity):
        self.scope, self.identity = deepcopy(scope), dict(identity)
        self.calls = 0
        self.result = None
        self.error = None

    def verify(self, *, scope, stage, identity):
        self.calls += 1
        assert scope == self.scope and identity == self.identity and stage in ('associated', 'quota-failed')
        if self.error:
            raise self.error
        # Mutating supplied copies cannot alter the collector's closed scope.
        scope['uid'] = 1
        identity['boot_id'] = 'changed-fixture-copy'
        return self.result


class Device:
    root = '/data/user/0/' + observation.APP + '/files/'

    def __init__(self, source, identity):
        self.files = {name: (source / name).read_bytes() if (source / name).exists() else None
                      for name in snapshot.FILES}
        self.files[snapshot.SPOOL_SOURCE] = self.files.pop(snapshot.SPOOL)
        self.identity = dict(identity)
        self.calls, self.heads = [], []
        self.stops = self.boots = 0
        self.alias = False
        self.links, self.sizes, self.inodes, self.symlinks = {}, {}, {}, set()
        self.root_uid = '0'
        self.on_head = self.on_stop = None
        self.bad_boot_at = None
        self.running_at = None
        self.force_stopped = True
        self.find_reply = None
        self.error = None
        self.bad_status = None

    def __call__(self, *args, **kwargs):
        if self.error:
            raise self.error
        command = args if args[:1] == ('emu',) else tuple(shlex.split(args[2]))
        self.calls.append(command)
        out, rc = b'', 0
        if command == ('emu', 'avd', 'name'):
            out = (self.identity['avd'] + '\nOK\n').encode()
        elif command[:1] == ('getprop',):
            key = dict(zip(('ro.kernel.qemu','ro.build.version.sdk','ro.build.type','ro.debuggable'),
                           ('qemu','api','build_type','debuggable')))[command[1]]
            out = (self.identity[key] + '\n').encode()
        elif command == ('cat', '/proc/sys/kernel/random/boot_id'):
            self.boots += 1
            out = ((self.identity['boot_id'] if self.boots != self.bad_boot_at else
                    '00000000-0000-0000-0000-000000000099') + '\n').encode()
        elif command == ('id', '-u'):
            out = (self.root_uid + '\n').encode()
        elif command == ('ps', '-A', '-o', 'NAME'):
            self.stops += 1
            if self.on_stop:
                self.on_stop(self)
            out = b'NAME\n' + ((observation.APP + ':worker\n').encode() if self.stops == self.running_at else b'')
        elif command == ('dumpsys', 'package', observation.APP):
            out = ('Packages:\n  Package [' + observation.APP + '] (1a2b):\n    User 0: stopped=' +
                   ('true' if self.force_stopped else 'false') + '\n').encode()
        elif command[:1] == ('find',):
            out = self.find_reply if self.find_reply is not None else (self.root + 'mobile-drafts.sqlite3\n').encode()
        elif command[:1] == ('realpath',):
            out = (command[1].replace('/data/user/0/', '/data/data/', 1) if self.alias else command[1]).encode() + b'\n'
        elif command[:1] == ('test',):
            name = command[-1]
            if command[1:3] == ('!', '-L'):
                rc = int(name in self.symlinks)
            elif command[1] == '-d':
                rc = 0
            else:
                relative = name[len(self.root):]
                rc = 0 if self.files.get(relative) is not None else 1
                if command[1] == '-e' and relative == self.bad_status:
                    rc = 2
        elif command[:2] == ('stat', '-c'):
            relative = command[3][len(self.root):]
            raw = self.files[relative]
            out = (str(self.sizes.get(relative, len(raw))) + ':' + str(self.links.get(relative, 1)) +
                   ':' + str(self.inodes.get(relative, sorted(self.files).index(relative) + 100)) + '\n').encode()
        elif command[:1] == ('sha256sum',):
            relative = command[1][len(self.root):]
            out = (hashlib.sha256(self.files[relative]).hexdigest() + '  ' + command[1] + '\n').encode()
        elif command[:2] == ('head', '-c'):
            relative = command[3][len(self.root):]
            self.heads.append(relative)
            out = self.files[relative][:int(command[2])]
            if self.on_head:
                out = self.on_head(self, relative, out)
        else:
            raise AssertionError('Unapproved fixture command')
        return SimpleNamespace(returncode=rc, stdout=out, stderr=b'')


@contextmanager
def task(stage='quota-failed', wal=False):
    with tempfile.TemporaryDirectory(prefix='opennexus-quota-collect-', dir=os.getenv('OPENNEXUS_QUOTA_TEST_ROOT')) as folder:
        root = Path(folder)
        assert root.is_absolute() and root.resolve() == root
        raw, scope, selected, expected, writers = original_fixture(root, stage, wal=wal)
        identity = json.loads((raw/'snapshot.json').read_bytes())['identity']
        tools, artifact = root/'tools', root/'artifact'
        tools.mkdir(); artifact.mkdir()
        device = Device(raw, identity)
        admission = FixtureAdmission(scope, identity)
        run, attempt = scope['owner_run'].split('-')
        with patch.dict(os.environ, GITHUB_RUN_ID=run, GITHUB_RUN_ATTEMPT=attempt), \
             patch.object(observation, 'require_hosted_emulator',
                          side_effect=lambda serial: observation.require(serial == 'emulator-5562', 'fixture serial')):
            def invoke(**changes):
                args = dict(scope=scope, stage=stage, identity=identity, admission=admission)
                args.update(changes)
                return collect.collect_stopped(device, 'emulator-5562', tools, artifact, **args)
            try:
                yield root, device, admission, invoke, scope, selected, expected
            finally:
                for writer in writers:
                    writer.close()


class QuotaCollector(unittest.TestCase):
    def test_both_stages_exact_original_writer_manifest_and_new_SQL_projection(self):
        for stage in ('associated', 'quota-failed'):
            with self.subTest(stage=stage), task(stage) as (root, device, admission, invoke, scope, selected, expected):
                output = invoke()
                self.assertEqual(admission.calls, 1)
                self.assertEqual(device.stops, 3)  # prior discovery plus two independent copy samples
                self.assertEqual(sorted(device.heads), sorted(name for name, raw in device.files.items() if raw is not None))
                self.assertEqual(len(device.heads), len(set(device.heads)))
                manifest = json.loads((output/'snapshot.json').read_bytes())
                self.assertEqual(set(manifest['files']), snapshot.FILES)
                self.assertNotIn('sync-spool', str(list(output.rglob('*'))))
                self.assertFalse(any(root.joinpath('artifact').iterdir()))
                stops = [i for i, c in enumerate(device.calls) if c[:1] == ('ps',)]
                reads = [i for i, c in enumerate(device.calls) if c[:1] == ('head',)]
                self.assertTrue(stops[1] < min(reads) <= max(reads) < stops[2])
                original_bytes = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
                result = snapshot.project_quota(output, root/'readonly-work', scope=scope, selected=selected, stage=stage)
                self.assertEqual(result.projection, expected)
                self.assertEqual(result.note, result.spool)
                self.assertEqual(result.spool, quota.BODY)
                self.assertFalse(result.evidence['completed_driver_and_stop_gate_verified'])
                self.assertFalse(result.evidence['runtime_verified'])
                self.assertEqual(original_bytes, {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()})

    def test_no_default_or_boolean_admission_and_preserved_guard_exception_before_device(self):
        for bad in (None, True, False, {}, object()):
            with self.subTest(type=type(bad)), task() as (_, device, _, invoke, *_):
                with self.assertRaises(RuntimeError):
                    invoke(admission=bad)
                self.assertEqual(device.calls, [])
        with self.assertRaises(TypeError):
            collect.OriginalQuotaAdmission()
        with task() as (_, device, admission, invoke, *_):
            error = ValueError('controlled admission error'); admission.error = error
            with self.assertRaises(ValueError) as caught:
                invoke()
            self.assertIs(caught.exception, error); self.assertEqual(device.calls, [])
        with task() as (_, device, admission, invoke, *_):
            admission.result = True
            with self.assertRaises(RuntimeError):
                invoke()
            self.assertEqual(device.calls, [])

    def test_typed_scope_identity_stage_and_disjoint_private_artifact_before_device(self):
        for field, bad in (('uid', True), ('api', 35), ('product', 'unknown'), ('avd', 'foreign')):
            with self.subTest(field=field), task() as (_, device, _, invoke, scope, *_):
                with self.assertRaises((RuntimeError, ValueError)):
                    invoke(scope=dict(scope, **{field: bad}))
                self.assertEqual(device.calls, [])
        with task() as (root, device, admission, invoke, scope, *_):
            for stage in ('timed-out', None, True):
                with self.assertRaises(RuntimeError):
                    invoke(stage=stage)
            (root/'tools'/'nested').mkdir()
            with self.assertRaises(RuntimeError):
                collect.collect_stopped(device, 'emulator-5562', root/'tools', root/'tools'/'nested',
                    scope=scope, stage='associated', identity=admission.identity, admission=admission)
            self.assertEqual(device.calls, [])

    def test_known_Android_alias_allowed_foreign_or_ambiguous_root_rejected(self):
        with task() as (_, device, _, invoke, *_):
            device.alias = True
            self.assertTrue((invoke()/'snapshot.json').is_file())
        for reply in (b'/data/user/0/foreign/files/mobile-drafts.sqlite3\n',
                      (Device.root+'mobile-drafts.sqlite3\n'+Device.root+'other/mobile-drafts.sqlite3\n').encode()):
            with self.subTest(reply=reply), task() as (_, device, _, invoke, *_):
                device.find_reply = reply
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertEqual(device.heads, [])

    def test_fresh_running_service_force_stop_and_boot_changes_reject_without_lifecycle(self):
        for kind in ('running-before', 'running-after', 'not-stopped', 'changed-boot'):
            with self.subTest(kind=kind), task() as (root, device, _, invoke, *_):
                if kind.startswith('running'):
                    device.running_at = 2 if kind == 'running-before' else 3
                elif kind == 'not-stopped':
                    device.force_stopped = False
                else:
                    device.bad_boot_at = 3
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertFalse(list((root/'tools').rglob('snapshot.json')))
                self.assertFalse(any(c[:1] in (('am',), ('root',), ('run-as',), ('sqlite3',)) for c in device.calls))

    def test_symlinked_parent_file_and_hardlink_refuse_before_its_bytes(self):
        for kind in ('parent', 'file', 'hardlink'):
            with self.subTest(kind=kind), task() as (_, device, _, invoke, *_):
                name = snapshot.SPOOL_SOURCE
                if kind == 'hardlink':
                    device.links[name] = 2
                else:
                    device.symlinks.add(Device.root + (name.rsplit('/', 1)[0] if kind == 'parent' else name))
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertNotIn(name, device.heads)

    def test_exact56B_wrong_digest_missing_required_and_unknown_absence_fail_closed(self):
        for kind in ('wrong-note', 'oversize-spool', 'missing-db', 'unknown-absence'):
            with self.subTest(kind=kind), task() as (root, device, _, invoke, *_):
                if kind == 'wrong-note':
                    device.files[snapshot.NOTE] = b'x' * 56
                elif kind == 'oversize-spool':
                    device.sizes[snapshot.SPOOL_SOURCE] = 57
                elif kind == 'missing-db':
                    device.files[snapshot.DATABASES[0]] = None
                else:
                    device.bad_status = snapshot.DATABASES[0] + '-wal'
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertFalse(list((root/'tools').rglob('snapshot.json')))

    def test_growth_changed_original_and_new_nullable_companion_no_success_manifest(self):
        for kind in ('growth', 'changed', 'new-wal'):
            with self.subTest(kind=kind), task() as (root, device, _, invoke, *_):
                if kind == 'growth':
                    device.on_head = lambda _, name, raw: raw + b'X' if name == snapshot.NOTE else raw
                elif kind == 'changed':
                    def changed(current, name, raw):
                        if name == snapshot.NOTE:
                            current.files[name] = b'x' * 56
                        return raw
                    device.on_head = changed
                else:
                    def new_wal(current):
                        if current.stops == 3:
                            current.files[snapshot.DATABASES[0] + '-wal'] = b'new'
                    device.on_stop = new_wal
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertFalse(list((root/'tools').rglob('snapshot.json')))

    def test_existing_partial_unknown_writer_no_overwrite_or_second_device_attempt(self):
        for target_kind in ('note', 'manifest'):
            with self.subTest(target_kind=target_kind), task() as (root, device, _, invoke, *_):
                error = OSError('controlled write outcome')
                writer = collect.exclusive
                def partial(path, raw):
                    selected = path.name == ('snapshot.json' if target_kind == 'manifest' else quota.NOTE)
                    if selected:
                        with path.open('xb') as stream:
                            stream.write(b'{')
                        raise error
                    writer(path, raw)
                with patch.object(collect, 'exclusive', side_effect=partial):
                    with self.assertRaises(OSError) as caught:
                        invoke()
                self.assertIs(caught.exception, error)
                calls = list(device.calls)
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertEqual(device.calls, calls)
                paths = list((root/'tools').rglob('snapshot.json'))
                self.assertEqual(paths[0].read_bytes(), b'{') if paths else self.assertEqual(target_kind, 'note')

    def test_original_device_exception_identity_and_no_secret_or_raw_stream_export(self):
        with task() as (_, device, _, invoke, *_):
            error = TimeoutError('controlled device failure'); device.error = error
            with self.assertRaises(TimeoutError) as caught:
                invoke()
            self.assertIs(caught.exception, error)
        with task() as (root, device, _, invoke, *_):
            output = invoke()
            self.assertTrue(list(output.parent.glob('stopped-check-*-processes.txt')))
            self.assertFalse(list(output.rglob('*processes*')))
            self.assertFalse(list(output.rglob('*package*')))
            self.assertEqual(list((root/'artifact').iterdir()), [])
            self.assertEqual(set(p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()),
                {name for name in snapshot.FILES if (output/name).exists()} | {'snapshot.json'})
            self.assertFalse(any('credentials' in ' '.join(c) for c in device.calls))

    def test_present_real_WAL_SHM_and_empty_companion_preserved_as_present(self):
        with task(wal=True) as (root, device, _, invoke, scope, selected, expected):
            for name in snapshot.DATABASES:
                self.assertIsNotNone(device.files[name+'-wal'])
                self.assertIsNotNone(device.files[name+'-shm'])
            output = invoke()
            self.assertEqual(len(device.heads), 8)
            result = snapshot.project_quota(output, root/'readonly-work', scope=scope,
                                            selected=selected, stage='quota-failed')
            self.assertEqual(result.projection, expected)
        with task() as (_, device, _, invoke, *_):
            device.files[snapshot.DATABASES[0]+'-wal'] = b''
            output = invoke()
            self.assertEqual(json.loads((output/'snapshot.json').read_bytes())['files'][snapshot.DATABASES[0]+'-wal'],
                             dict(bytes=0, sha256=hashlib.sha256(b'').hexdigest()))

    def test_root_size_inode_and_nonbytes_stream_guards_without_success_manifest(self):
        for kind in ('root', 'size', 'inode', 'nonbytes'):
            with self.subTest(kind=kind), task() as (root, device, _, invoke, *_):
                if kind == 'root':
                    device.root_uid = '1000'
                elif kind == 'size':
                    device.sizes[snapshot.DATABASES[0]] = snapshot.MAX_FILE + 1
                elif kind == 'inode':
                    def changed_inode(current, name, raw):
                        current.inodes[name] = 9000
                        return raw
                    device.on_head = changed_inode
                else:
                    device.on_head = lambda _, name, raw: bytearray(raw)
                with self.assertRaises(RuntimeError):
                    invoke()
                self.assertFalse(list((root/'tools').rglob('snapshot.json')))
                if kind in ('root', 'size'):
                    self.assertEqual(device.heads, [])

    def test_unknown_false_writer_return_cannot_be_a_success_or_retried(self):
        with task() as (root, device, _, invoke, *_):
            writer = collect.exclusive
            def unknown(path, raw):
                if path.name == quota.NOTE:
                    return False
                return writer(path, raw)
            with patch.object(collect, 'exclusive', side_effect=unknown):
                with self.assertRaises(RuntimeError):
                    invoke()
            self.assertFalse(list((root/'tools').rglob('snapshot.json')))
            calls = list(device.calls)
            with self.assertRaises(RuntimeError):
                invoke()
            self.assertEqual(device.calls, calls)
