"""Synthetic boundary checks; never connect to ADB or claim release execution."""
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from android_release_observation import APP, DATABASES, StoppedReleaseObserver, admit_files, admit_identity, admit_root

ROOT = '/data/user/0/' + APP + '/files/'
IDENTITY = dict(avd='opennexus-ci-123-1', qemu='1', api='36', build_type='userdebug', debuggable='1',
    boot_id='12345678-1234-1234-1234-123456789abc')
ENV = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1')


class Device:
    def __init__(self):
        self.calls, self.rooted, self.active = [], False, False
        self.identity = deepcopy(IDENTITY)
        self.stopped = True
        self.files = {ROOT + name: b'original-' + name.encode() for name in DATABASES}
        self.files[ROOT + 'vault/release-owned-original.md'] = b'# original\r\nChinese \xe4\xb8\xad\xe6\x96\x87\r\n'
        self.tamper = False

    def __call__(self, *args, check=True, **kwargs):
        self.calls.append(args)
        rc, out = 0, b''
        if args == ('emu', 'avd', 'name'): out = (self.identity['avd'] + '\nOK\n').encode()
        elif args == ('root',): self.rooted = True; out = b'restarting adbd as root\n'
        elif args == ('wait-for-device',): pass
        else:
            assert args[:2] == ('shell', '-T'), args
            cmd = shlex.split(args[2])
            if cmd[0] == 'getprop':
                key = {'ro.kernel.qemu':'qemu', 'ro.build.version.sdk':'api', 'ro.build.type':'build_type',
                    'ro.debuggable':'debuggable'}[cmd[1]]
                out = self.identity[key].encode()
            elif cmd == ['cat', '/proc/sys/kernel/random/boot_id']: out = self.identity['boot_id'].encode()
            elif cmd == ['id', '-u']: out = b'0' if self.rooted else b'2000'
            elif cmd == ['ps', '-A', '-o', 'NAME']:
                out = ('NAME\ninit\nadbd\n' + (APP + ':worker\n' if self.active else '')).encode()
            elif cmd == ['dumpsys', 'package', APP]: out = ('  User 0: installed=true stopped=' + str(self.stopped).lower() + ' enabled=0\n').encode()
            elif cmd == ['find', '/data/user/0/' + APP, '-name', 'mobile-drafts.sqlite3', '-type', 'f']:
                out = (ROOT + 'mobile-drafts.sqlite3\n').encode()
            elif cmd[0] == 'test':
                name = cmd[-1]; exists = name in self.files
                rc = int(not (exists if cmd[1] in ('-e', '-f') else not exists if cmd[2] == '-e' else True))
            elif cmd[0] == 'realpath': out = cmd[1].encode()
            elif cmd[:3] == ['stat', '-c', '%s']: out = str(len(self.files[cmd[3]])).encode()
            elif cmd[0] == 'sha256sum': out = (hashlib.sha256(self.files[cmd[1]]).hexdigest() + '  ' + cmd[1]).encode()
            elif cmd[0] == 'cat': out = self.files[cmd[1]] + (b'changed' if self.tamper else b'')
            else: raise AssertionError(cmd)
        if check and rc: raise RuntimeError('fake command failed')
        return CompletedProcess(args, rc, out, b'')


@contextmanager
def observer():
    with tempfile.TemporaryDirectory() as temp, patch('android_runtime_probe.sys.platform', 'linux'), patch.dict(os.environ, ENV):
        device = Device()
        target = Path(temp) / 'evidence'
        yield StoppedReleaseObserver(device, 'emulator-5562', target, ['release-owned-original.md']), device


class Boundaries(unittest.TestCase):
    def test_foreign_physical_and_production_images_rejected(self):
        for key, value in [('avd','user-phone'), ('qemu','0'), ('api','35'), ('build_type','user'), ('debuggable','0'), ('boot_id','unknown')]:
            data = dict(IDENTITY, **{key:value})
            with self.subTest(key=key), self.assertRaises(RuntimeError): admit_identity(data, '123', '1')
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(RuntimeError):
            StoppedReleaseObserver(Device(), 'phone-serial', Path(temp)/'out', ['release-owned-original.md'])

    def test_raw_paths_credentials_and_duplicate_fixtures_refused(self):
        for root in ['/data/user/0/' + APP + '.dev/files/', ROOT + '../', ROOT + './', '/data/user/10/' + APP + '/files/', ROOT + 'bad//']:
            with self.subTest(root=root), self.assertRaises(RuntimeError): admit_root(root)
        for files in [['../release-owned-x.md'], ['mobile-sync-auth.sqlite3'], ['release-owned-x.md']*2, [[]], ['release-owned-x.md/shadow']]:
            with self.subTest(files=files), self.assertRaises(RuntimeError): admit_files(files)
        with observer() as (view, device):
            with self.assertRaises(RuntimeError): view.copy_stopped(ROOT + '../', 'before')
            self.assertEqual(device.calls, [])

    def test_root_intent_durable_no_retry_after_changed_owner(self):
        with observer() as (view, device):
            original = device.__call__
            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                if args == ('wait-for-device',): device.identity['boot_id'] = '87654321-1234-1234-1234-123456789abc'
                return result
            view.device = changed
            with self.assertRaises(RuntimeError): view.start_root_observation()
            self.assertTrue((view.output/'root-intent.json').is_file())
            with self.assertRaises(RuntimeError): view.start_root_observation()
            self.assertEqual(device.calls.count(('root',)), 1)

    def test_active_worker_refuses_copy_without_stopping_it(self):
        with observer() as (view, device):
            view.start_root_observation(); device.active = True
            with self.assertRaisesRegex(RuntimeError, 'APP_NOT_STOPPED'): view.copy_stopped(ROOT, 'active')
            self.assertFalse((view.output/'active').exists())
            self.assertFalse(any('force-stop' in str(call) for call in device.calls))

    def test_changed_original_is_preserved_as_failure(self):
        with observer() as (view, device):
            view.start_root_observation(); device.tamper = True
            with self.assertRaisesRegex(RuntimeError, 'ORIGINAL_CHANGED'): view.copy_stopped(ROOT, 'changed')
            self.assertFalse((view.output/'changed/snapshot.json').exists())

    def test_native_root_is_discovered_only_inside_exact_stopped_app(self):
        with observer() as (view, device):
            view.start_root_observation()
            self.assertEqual(view.discover_data_root(), ROOT)
            device.active = True
            with self.assertRaisesRegex(RuntimeError, 'APP_NOT_STOPPED'): view.discover_data_root()

    def test_absent_process_is_not_proof_scheduled_work_cannot_start(self):
        with observer() as (view, device):
            view.start_root_observation(); device.stopped = False
            with self.assertRaisesRegex(RuntimeError, 'PACKAGE_NOT_FORCE_STOPPED'): view.copy_stopped(ROOT, 'idle')
            self.assertFalse((view.output/'idle').exists())

    def test_actual_synthetic_wal_rows_and_raw_bytes_are_retained(self):
        with observer() as (view, device), tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'example.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA journal_mode=WAL'); db.execute('CREATE TABLE sample(value)')
                db.execute('INSERT INTO sample VALUES(?)', ('unconfirmed draft \U0001f600',)); db.commit()
                for suffix in ('','-wal','-shm'):
                    device.files[ROOT + DATABASES[0] + suffix] = Path(str(path)+suffix).read_bytes()
                view.start_root_observation()
                result = view.copy_stopped(ROOT, 'snapshot')
                # Even mode=ro can update a WAL index. Keep raw evidence immutable;
                # inspect only another working copy, never the retained snapshot.
                working = Path(temp)/'inspection'; working.mkdir()
                local = working/DATABASES[0]
                for suffix in ('', '-wal', '-shm'):
                    shutil.copyfile(str(view.output/'snapshot'/DATABASES[0])+suffix, str(local)+suffix)
                with closing(sqlite3.connect(local.resolve().as_uri()+'?mode=ro', uri=True)) as copy:
                    self.assertEqual(copy.execute('SELECT value FROM sample').fetchall(), [('unconfirmed draft \U0001f600',)])
                self.assertFalse(result['credential_store_copied'])
                self.assertFalse(result['normal_phone_permissions_verified'])
                for relative, item in result['files'].items():
                    if item is not None:
                        self.assertEqual((view.output/'snapshot'/relative).read_bytes(), device.files[ROOT+relative])
                with self.assertRaises(FileExistsError): view.copy_stopped(ROOT, 'snapshot')
                self.assertFalse(any('chmod' in str(call) or 'run-as' in str(call) for call in device.calls))


if __name__ == '__main__': unittest.main()
