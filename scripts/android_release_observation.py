"""Narrow, read-only originals for a stopped release app on an owned hosted AVD.

This is test infrastructure, not a release-phone permission or runtime result.
The caller must durably finish its GUI phase and stop the app before snapshotting.
No credential store, arbitrary path, SQL mutation or app permission change is exposed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex

from android_runtime_probe import require, require_hosted_emulator

APP = 'cc.kronecker.opennexus'
DATABASES = ('mobile-drafts.sqlite3', 'vault/.ainote/host.sqlite3')
MAX_FILE = 32 * 1024 * 1024


def admit_identity(identity, run, attempt):
    require(type(identity) is dict and set(identity) == {'avd', 'qemu', 'api', 'build_type', 'debuggable', 'boot_id'},
        'RELEASE_OBSERVATION_IDENTITY_SHAPE')
    require(re.fullmatch(r'[1-9][0-9]{0,19}', run) and re.fullmatch(r'[1-9][0-9]{0,5}', attempt),
        'RELEASE_OBSERVATION_RUN')
    require(identity['avd'] == f'opennexus-ci-{run}-{attempt}' and identity['qemu'] == '1'
        and identity['api'] == '36' and identity['build_type'] in ('eng', 'userdebug')
        and identity['debuggable'] == '1' and type(identity['boot_id']) is str
        and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', identity['boot_id']),
        'RELEASE_OBSERVATION_OWN_USERDEBUG_AVD')
    return identity


def admit_files(files):
    require(type(files) is list and 1 <= len(files) <= 8 and all(type(name) is str for name in files)
        and len(set(files)) == len(files),
        'RELEASE_OBSERVATION_OWN_FILES')
    for name in files:
        require(type(name) is str and re.fullmatch(r'release-owned-[a-z0-9-]{1,48}\.(md|canvas|png)', name),
            'RELEASE_OBSERVATION_EXACT_FIXTURE_FILE')
    return tuple('vault/' + name for name in files)


def admit_root(root):
    require(type(root) is str and re.fullmatch(r'/data/user/0/cc\.kronecker\.opennexus/(?:[A-Za-z0-9_-]+/){1,5}', root),
        'RELEASE_OBSERVATION_EXACT_PRIVATE_ROOT')
    return root


def exclusive(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def require_stopped_package(state):
    """Read only this package's installed User0 state, not Queries headings."""
    error = 'RELEASE_OBSERVATION_PACKAGE_NOT_FORCE_STOPPED'
    require(type(state) is str and len(state) <= 1024 * 1024, error)
    lines = state.splitlines()
    sections = [i for i, line in enumerate(lines) if line == 'Packages:']
    require(len(sections) == 1, error)
    start = sections[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i] and not lines[i][0].isspace()), len(lines))
    headers = [(i, re.fullmatch(r'  Package \[([A-Za-z0-9_.]+)\] \([0-9a-f]+\):', lines[i]))
               for i in range(start, end)]
    headers = [(i, match.group(1)) for i, match in headers if match]
    own = [i for i, name in headers if name == APP]
    require(len(own) == 1, error)
    begin = own[0] + 1
    finish = next((i for i, _ in headers if i >= begin), end)
    users = [line.strip() for line in lines[begin:finish] if re.match(r'^\s*User 0:', line)]
    require(len(users) == 1 and re.findall(r'(?:^|\s)stopped=(\S+)', users[0]) == ['true'], error)


class StoppedReleaseObserver:
    def __init__(self, device, serial, output, files):
        require_hosted_emulator(serial)
        self.device, self.serial, self.output = device, serial, Path(output)
        self.files = admit_files(files)
        self.allowed = set(self.files) | {db + suffix for db in DATABASES for suffix in ('', '-wal', '-shm')}
        self.identity = None
        self.stopped_checks = 0
        require(not self.output.exists(), 'RELEASE_OBSERVATION_ORIGINAL_OUTPUT_EXISTS')
        self.output.mkdir(parents=True)

    def shell(self, *args, check=True):
        return self.device('shell', '-T', shlex.join(args), check=check)

    def text(self, *args):
        return self.shell(*args).stdout.decode('utf-8', 'strict').strip()

    def observe_identity(self):
        require_hosted_emulator(self.serial)
        avd = self.device('emu', 'avd', 'name').stdout.decode('ascii', 'strict').splitlines()
        require(len(avd) == 2 and avd[1] == 'OK', 'RELEASE_OBSERVATION_AVD_REPLY')
        value = dict(avd=avd[0], qemu=self.text('getprop', 'ro.kernel.qemu'),
            api=self.text('getprop', 'ro.build.version.sdk'), build_type=self.text('getprop', 'ro.build.type'),
            debuggable=self.text('getprop', 'ro.debuggable'), boot_id=self.text('cat', '/proc/sys/kernel/random/boot_id'))
        return admit_identity(value, os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'])

    def start_root_observation(self):
        require(self.identity is None and not (self.output / 'root-intent.json').exists(),
            'RELEASE_OBSERVATION_ROOT_NOT_REPLAYABLE')
        before = self.observe_identity()
        exclusive(self.output / 'root-intent.json', json.dumps(dict(before=before,
            package=APP, allowed_relative_files=sorted(self.allowed), root_only_owned_AVD=True,
            normal_phone_root_or_permissions_claimed=False)).encode())
        result = self.device('root', check=False)
        exclusive(self.output / 'root-original.stdout', result.stdout)
        exclusive(self.output / 'root-original.stderr', result.stderr)
        require(result.returncode == 0, 'RELEASE_OBSERVATION_ROOT_UNAVAILABLE_NO_FALLBACK')
        self.device('wait-for-device', timeout=30)
        after = self.observe_identity()
        require(after == before and self.text('id', '-u') == '0', 'RELEASE_OBSERVATION_ROOT_IDENTITY_CHANGED')
        self.identity = before
        exclusive(self.output / 'root-confirmed.json', json.dumps(after).encode())

    def require_stopped(self):
        require(self.identity is not None and self.observe_identity() == self.identity,
            'RELEASE_OBSERVATION_OWNER_CHANGED')
        # Root sees all processes. Include the app's colon-suffixed service processes.
        self.stopped_checks += 1
        prefix = 'stopped-check-' + str(self.stopped_checks).zfill(4)
        raw_processes = self.shell('ps', '-A', '-o', 'NAME').stdout
        exclusive(self.output / (prefix + '-processes.txt'), raw_processes)
        processes = raw_processes.decode('utf-8', 'strict').strip().splitlines()
        require(processes and processes[0].strip() == 'NAME'
            and not any(name == APP or name.startswith(APP + ':') for name in processes[1:]),
            'RELEASE_OBSERVATION_APP_NOT_STOPPED')
        # No process is insufficient: a scheduled worker could start mid-copy.
        # Refuse until the caller's explicit force-stop is visible for user0.
        raw_state = self.shell('dumpsys', 'package', APP).stdout
        exclusive(self.output / (prefix + '-package.txt'), raw_state)
        require_stopped_package(raw_state.decode('utf-8', 'strict'))

    def path(self, root, relative):
        admit_root(root)
        require(relative in self.allowed, 'RELEASE_OBSERVATION_PATH_NOT_ADMITTED')
        name = root + relative
        require(self.text('realpath', '/data/user/0/' + APP) == '/data/data/' + APP
            or self.text('realpath', '/data/user/0/' + APP) == '/data/user/0/' + APP,
            'RELEASE_OBSERVATION_APP_ROOT_ALIAS')
        resolved = self.text('realpath', name)
        # Android can resolve /data/user/0 to its /data/data alias, but not outside this exact app.
        aliases = (name, name.replace('/data/user/0/', '/data/data/', 1))
        require(resolved in aliases and self.shell('test', '!', '-L', name, check=False).returncode == 0,
            'RELEASE_OBSERVATION_SYMLINK_OR_FOREIGN_FILE')
        return name

    def discover_data_root(self):
        self.require_stopped()
        raw = self.shell('find', '/data/user/0/' + APP, '-name', 'mobile-drafts.sqlite3', '-type', 'f').stdout
        require(len(raw) <= 8192, 'RELEASE_OBSERVATION_ROOT_DISCOVERY_BOUND')
        paths = raw.decode('utf-8', 'strict').splitlines()
        require(len(paths) == 1 and paths[0].endswith('/mobile-drafts.sqlite3'),
            'RELEASE_OBSERVATION_SINGLE_NATIVE_DRAFT_ROOT')
        root = paths[0][:-len('mobile-drafts.sqlite3')]
        if root.startswith('/data/data/' + APP + '/'):
            root = root.replace('/data/data/', '/data/user/0/', 1)
        admit_root(root)
        self.path(root, 'mobile-drafts.sqlite3')
        return root

    def copy_stopped(self, root, label):
        admit_root(root)
        require(type(label) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label), 'RELEASE_OBSERVATION_LABEL')
        self.require_stopped()
        directory = self.output / label
        directory.mkdir()
        items, before = {}, {}
        # Probe absence separately; a failing cat must never become file content.
        for relative in sorted(self.allowed):
            name = root + relative
            status = self.shell('test', '-e', name, check=False)
            require(status.returncode in (0, 1), 'RELEASE_OBSERVATION_FILE_STATUS_UNKNOWN')
            if status.returncode == 1:
                require(relative.endswith(('-wal', '-shm')), 'RELEASE_OBSERVATION_ORIGINAL_MISSING')
                items[relative] = None
                continue
            name = self.path(root, relative)
            require(self.shell('test', '-f', name, check=False).returncode == 0, 'RELEASE_OBSERVATION_NOT_REGULAR')
            size = self.text('stat', '-c', '%s', name)
            require(re.fullmatch(r'[0-9]{1,9}', size) and int(size) <= MAX_FILE, 'RELEASE_OBSERVATION_SIZE')
            hashed = self.text('sha256sum', name).split()
            require(len(hashed) == 2 and hashed[1] == name and re.fullmatch(r'[0-9a-f]{64}', hashed[0]),
                'RELEASE_OBSERVATION_HASH')
            raw = self.shell('cat', name).stdout
            require(len(raw) == int(size) and hashlib.sha256(raw).hexdigest() == hashed[0],
                'RELEASE_OBSERVATION_ORIGINAL_CHANGED')
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            exclusive(target, raw)
            before[relative] = hashed[0]
            items[relative] = dict(bytes=len(raw), sha256=hashed[0])
        self.require_stopped()
        for relative, item in items.items():
            if item is None:
                require(self.shell('test', '!', '-e', root + relative, check=False).returncode == 0,
                    'RELEASE_OBSERVATION_ABSENT_WAL_CHANGED')
            else:
                name = self.path(root, relative)
                require(self.text('sha256sum', name).split() == [before[relative], name],
                    'RELEASE_OBSERVATION_SNAPSHOT_CHANGED')
        result = dict(package=APP, identity=self.identity, files=items, app_stopped_before_and_after=True,
            credential_store_copied=False, SQL_executed_on_device=False, normal_phone_permissions_verified=False)
        exclusive(directory / 'snapshot.json', json.dumps(result, sort_keys=True).encode())
        return result
