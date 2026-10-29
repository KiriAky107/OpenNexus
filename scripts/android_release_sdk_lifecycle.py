"""Closed hosted timeout SDK preparation, launch binding and read-only cleanup."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import android_release_sdk_archive as kernel

require = kernel.require
PREFIX = 'sdk-'
SERIAL = 'emulator-5562'
VERSION_SCOPE = 'One admitted original fixed SDK version prefix; other streams length/hash only'
SAFE = kernel.SAFE_CODES | frozenset('''SDK_CONTEXT SDK_BOOT SDK_JSON SDK_FILE_ALIAS
SDK_ORIGINAL_RECORD_EXISTS SDK_PREPARE_RESULT SDK_ROOT_OWNER SDK_LAUNCHER_CHANGED
SDK_LAUNCH_INTENT SDK_LAUNCH_PID SDK_PROC_UNKNOWN SDK_PROC_IDENTITY SDK_PROC_LIMIT
SDK_ADB_UNKNOWN SDK_AVD_PRESENT SDK_CLEANUP_DEADLINE SDK_CLEANUP_CLOCK
SDK_SDK_PROCESS_PRESENT SDK_CLEANUP_OWNER SDK_ORIGINAL_PID_REUSED SDK_UNKNOWN_ACTION
SDK_PROBE_IDENTITY SDK_COMMAND_UNKNOWN SDK_VERSION_UNAVAILABLE SDK_GLOBAL_TOOLS
SDK_LICENSE SDK_REGISTERED_EMULATOR SDK_IMAGE_IDENTITY SDK_AVD_BINDING'''.split())


def boot():
    data = Path('/proc/sys/kernel/random/boot_id').read_bytes()
    require(len(data) == 37 and data.endswith(b'\n'), 'SDK_BOOT')
    raw = data[:-1].decode('ascii')
    require(str(uuid.UUID(raw)) == raw, 'SDK_BOOT')
    return raw


def context():
    require(sys.platform == 'linux' and all(os.environ.get(k) == v for k, v in (
        ('GITHUB_ACTIONS', 'true'), ('RUNNER_ENVIRONMENT', 'github-hosted'),
        ('RUNNER_OS', 'Linux'), ('RUNTIME_SCENE', 'sync-timeout'))), 'SDK_CONTEXT')
    owner = {k: os.environ.get(k, '') for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    require(re.fullmatch(r'[1-9][0-9]{0,19}', owner['GITHUB_RUN_ID']) and
            re.fullmatch(r'[1-9][0-9]{0,3}', owner['GITHUB_RUN_ATTEMPT']) and
            re.fullmatch(r'[0-9a-f]{40}', owner['GITHUB_SHA']), 'SDK_CONTEXT')
    workspace = Path(os.environ.get('GITHUB_WORKSPACE', ''))
    sdk = Path(os.environ.get('ANDROID_HOME', ''))
    for path, raw in ((workspace, os.environ.get('GITHUB_WORKSPACE', '')), (sdk, os.environ.get('ANDROID_HOME', ''))):
        require(path.is_absolute() and str(path) == raw and '..' not in path.parts and
                path.is_dir() and path.resolve() == path and not path.is_symlink(), 'SDK_CONTEXT')
    parent = workspace / '.build/android-release-runtime'
    evidence = parent / 'origin-proof'
    require(parent.is_dir() and parent.resolve() == parent and evidence.is_dir()
            and evidence.resolve() == evidence, 'SDK_CONTEXT')
    for key, name in (('ANDROID_AVD_HOME', 'avd'), ('ANDROID_EMULATOR_HOME', 'emulator-home')):
        require(os.environ.get(key) == str(parent / name) and (parent / name).is_dir()
                and (parent / name).resolve() == parent / name, 'SDK_CONTEXT')
    owner.update(uid=os.getuid(), host_boot_id=boot())
    root = parent / ('sdk-owned-' + owner['GITHUB_RUN_ID'] + '-' + owner['GITHUB_RUN_ATTEMPT'])
    return dict(owner=owner, parent=parent, evidence=evidence, root=root, sdk=sdk,
                launcher=root / 'runtime/emulator/emulator')


def emit(ctx, name, value):
    path = ctx['evidence'] / (PREFIX + name + '.json')
    require(path.parent.resolve() == path.parent and not path.parent.is_symlink(), 'SDK_FILE_ALIAS')
    require(not path.exists() and not path.is_symlink(), 'SDK_ORIGINAL_RECORD_EXISTS')
    with path.open('xb') as output:
        output.write((json.dumps(value, sort_keys=True) + '\n').encode())
        output.flush(); os.fsync(output.fileno())
    return path


def raw_file(path, limit=65536):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path and
            path.stat().st_size <= limit, 'SDK_FILE_ALIAS')
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'SDK_FILE_ALIAS')
    return data


def read(ctx, name, keys):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'SDK_JSON'); result[key] = value
        return result
    def constant(_): raise kernel.ArchiveError('SDK_JSON')
    result = json.loads(raw_file(ctx['evidence'] / (PREFIX + name + '.json')),
                        object_pairs_hook=pairs, parse_constant=constant)
    require(type(result) is dict and set(result) == set(keys) and result.get('owner') == ctx['owner'], 'SDK_JSON')
    return result


def fingerprint(path):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path and
            20 <= path.stat().st_size <= kernel.MAX_FILE, 'SDK_LAUNCHER_CHANGED')
    before = path.stat()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns), 'SDK_LAUNCHER_CHANGED')
    return dict(bytes=before.st_size, sha256=digest)


def root_owner(ctx, prepared):
    root = ctx['root']
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root, 'SDK_ROOT_OWNER')
    actual = root.stat()
    require([actual.st_dev, actual.st_ino] == prepared['root_identity'], 'SDK_ROOT_OWNER')
    expected = {k: ctx['owner'][k] for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    require(raw_file(root / 'owner.json') == json.dumps(expected, sort_keys=True).encode(), 'SDK_ROOT_OWNER')


def owned_prepared(ctx):
    value = read(ctx, 'prepare-result', ('owner', 'passed', 'root_identity', 'launcher', 'kernel_sha256', 'runtime_verified'))
    require(value['passed'] is True and value['runtime_verified'] is False and
            hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-kernel-result.json')).hexdigest() == value['kernel_sha256'],
            'SDK_PREPARE_RESULT')
    root_owner(ctx, value)
    return value


def prepared(ctx):
    value = owned_prepared(ctx)
    require(fingerprint(ctx['launcher']) == value['launcher'], 'SDK_LAUNCHER_CHANGED')
    return value


def validate_probe(ctx):
    value = read(ctx, 'probe-result', ('owner', 'passed', 'commands', 'runtime_verified'))
    require(value['passed'] is True and value['runtime_verified'] is False and
            type(value['commands']) is list and len(value['commands']) == 2, 'SDK_PROBE_IDENTITY')
    for index, option in enumerate(('-version', '-accel-check')):
        record = value['commands'][index]
        keys = {'argv', 'attempted', 'returncode', 'stdout_bytes', 'stdout_sha256', 'stderr_bytes', 'stderr_sha256'}
        if index == 0: keys.add('version_file_scope')
        require(type(record) is dict and set(record) == keys and
                record['argv'] == [str(ctx['launcher']), option] and record['attempted'] is True and
                type(record['returncode']) is int and record['returncode'] == 0, 'SDK_PROBE_IDENTITY')
        if index == 0: require(record['version_file_scope'] == VERSION_SCOPE, 'SDK_PROBE_IDENTITY')
        for channel in ('stdout', 'stderr'):
            require(type(record[channel + '_bytes']) is int and 0 <= record[channel + '_bytes'] <= 65536 and
                    re.fullmatch(r'[0-9a-f]{64}', record[channel + '_sha256']), 'SDK_PROBE_IDENTITY')
    version = raw_file(ctx['parent'] / 'emulator-version.txt', 128)
    require(re.fullmatch(rb'Android emulator version 37\.2\.12(?:\.0)? \(build_id 16428233\)\n', version),
            'SDK_PROBE_IDENTITY')
    return value


def prepare(ctx):
    require(not list(ctx['evidence'].glob('sdk-*.json')) and all(
        not (ctx['parent'] / name).exists() and not (ctx['parent'] / name).is_symlink()
        for name in ('emulator.pid', 'emulator.log', 'emulator-version.txt')), 'SDK_ORIGINAL_RECORD_EXISTS')
    for name in ('platform-tools/adb', 'platforms/android-36/android.jar', 'build-tools/36.0.0/aapt2',
                 'build-tools/36.0.0/apksigner', 'cmdline-tools/latest/bin/sdkmanager', 'cmdline-tools/latest/bin/avdmanager'):
        path = ctx['sdk'] / name
        require(path.is_file() and path.resolve().is_relative_to(ctx['sdk']) and path.stat().st_size > 0,
                'SDK_GLOBAL_TOOLS')
    emit(ctx, 'prepare-intent', dict(owner=ctx['owner'], fixed=kernel.FIXED, automatic_retry=False))
    result = kernel.prepare_owned()
    path = emit(ctx, 'kernel-result', result)
    root = ctx['root']
    passed = result.get('passed') is True
    identity = [root.stat().st_dev, root.stat().st_ino] if passed else None
    value = dict(owner=ctx['owner'], passed=passed, root_identity=identity,
                 launcher=fingerprint(ctx['launcher']) if passed else None,
                 kernel_sha256=hashlib.sha256(raw_file(path)).hexdigest(), runtime_verified=False)
    emit(ctx, 'prepare-result', value)
    return value


def select_launcher(ctx):
    value = prepared(ctx)
    image_ready(ctx)
    emit(ctx, 'launch-selection-intent', dict(owner=ctx['owner'], launcher=value['launcher'], automatic_retry=False))
    prepared(ctx)
    return str(ctx['launcher'])


def image_ready(ctx):
    prepared(ctx)
    value = read(ctx, 'image-result', ('owner', 'passed', 'commands', 'image_properties',
                                      'emulator_registered_version', 'licenses', 'runtime_verified'))
    require(value['passed'] is True and value['runtime_verified'] is False and
            value['emulator_registered_version'] == kernel.FIXED['version'], 'SDK_IMAGE_IDENTITY')
    private = ctx['root'] / 'runtime'
    manager = str(ctx['sdk'] / 'cmdline-tools/latest/bin/sdkmanager')
    commands = [[manager, '--sdk_root=' + str(private), '--list_installed'],
                [manager, '--sdk_root=' + str(private), '--install', 'system-images;android-36;google_apis;x86_64']]
    intent = read(ctx, 'image-intent', ('owner', 'argv', 'automatic_retry'))
    require(intent['argv'] == commands and intent['automatic_retry'] is False and
            type(value['commands']) is list and len(value['commands']) == 2, 'SDK_IMAGE_IDENTITY')
    for record, command in zip(value['commands'], commands):
        require(type(record) is dict and set(record) == {'argv', 'attempted', 'returncode', 'stdout_bytes',
                'stdout_sha256', 'stderr_bytes', 'stderr_sha256'} and record['argv'] == command and
                record['attempted'] is True and type(record['returncode']) is int and record['returncode'] == 0,
                'SDK_IMAGE_IDENTITY')
        for channel in ('stdout', 'stderr'):
            require(type(record[channel + '_bytes']) is int and 0 <= record[channel + '_bytes'] <= 4 * 1024 ** 2
                    and re.fullmatch(r'[0-9a-f]{64}', record[channel + '_sha256']), 'SDK_IMAGE_IDENTITY')
    require(type(value['licenses']) is dict and 'android-sdk-license' in value['licenses'] and
            set(value['licenses']) <= {'android-sdk-license', 'android-sdk-preview-license'}, 'SDK_LICENSE')
    for name, original in value['licenses'].items():
        data = raw_file(private / 'licenses' / name)
        require(original == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()), 'SDK_LICENSE')
    path = ctx['root'] / 'runtime/system-images/android-36/google_apis/x86_64/source.properties'
    require(type(value['image_properties']) is dict and set(value['image_properties']) == {'api', 'abi', 'tag', 'bytes', 'sha256'},
            'SDK_IMAGE_IDENTITY')
    data = raw_file(path, 16384)
    require(value['image_properties']['bytes'] == len(data) and hashlib.sha256(data).hexdigest() == value['image_properties']['sha256'] and
            value['image_properties']['api'] == '36' and value['image_properties']['abi'] == 'x86_64' and
            value['image_properties']['tag'] == 'google_apis', 'SDK_IMAGE_IDENTITY')
    return value


def bind_avd(ctx):
    image = image_ready(ctx)
    name = 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' + ctx['owner']['GITHUB_RUN_ATTEMPT']
    path = ctx['parent'] / 'avd' / (name + '.avd') / 'config.ini'
    data = raw_file(path)
    values = {}
    for line in data.decode('ascii').splitlines():
        if '=' not in line: continue
        key, value = line.split('=', 1)
        if key in ('image.sysdir.1', 'abi.type', 'tag.id'):
            require(key not in values, 'SDK_AVD_BINDING'); values[key] = value
    require(set(values) == {'image.sysdir.1', 'abi.type', 'tag.id'} and values['abi.type'] == 'x86_64'
            and values['tag.id'] == 'google_apis', 'SDK_AVD_BINDING')
    name_in_config = values['image.sysdir.1']
    posix = PurePosixPath(name_in_config)
    require('..' not in posix.parts and '\\' not in name_in_config and ':' not in name_in_config and
            str(posix) == name_in_config.rstrip('/'), 'SDK_AVD_BINDING')
    source = Path(name_in_config) if posix.is_absolute() else (ctx['root'] / 'runtime').joinpath(*posix.parts)
    require(source.resolve() == ctx['root'] / 'runtime/system-images/android-36/google_apis/x86_64', 'SDK_AVD_BINDING')
    value = dict(owner=ctx['owner'], avd=name, config_sha256=hashlib.sha256(data).hexdigest(),
                 image_properties_sha256=image['image_properties']['sha256'], runtime_verified=False)
    emit(ctx, 'avd-binding', value)
    return value


def install_image(ctx):
    prepared(ctx)
    private = ctx['root'] / 'runtime'
    manager = ctx['sdk'] / 'cmdline-tools/latest/bin/sdkmanager'
    commands = [[str(manager), '--sdk_root=' + str(private), '--list_installed'],
                [str(manager), '--sdk_root=' + str(private), '--install', 'system-images;android-36;google_apis;x86_64']]
    emit(ctx, 'image-intent', dict(owner=ctx['owner'], argv=commands, automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, commands=[], image_properties=None,
                  emulator_registered_version=None, licenses={}, runtime_verified=False)
    try:
        licenses = private / 'licenses'
        require(not licenses.exists() and not licenses.is_symlink(), 'SDK_LICENSE')
        licenses.mkdir()
        for name in ('android-sdk-license', 'android-sdk-preview-license'):
            path = ctx['sdk'] / 'licenses' / name
            if not path.exists() and name == 'android-sdk-preview-license': continue
            data = raw_file(path, 65536)
            rows = [line for line in data.splitlines() if line]
            require(rows and all(re.fullmatch(rb'[0-9a-f]{40}', line) for line in rows), 'SDK_LICENSE')
            with (licenses / name).open('xb') as output: output.write(data)
            result['licenses'][name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        for index, command in enumerate(commands):
            prepared(ctx)
            record = dict(argv=command, attempted=True, returncode=None); result['commands'].append(record)
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=900, check=False)
            record.update(returncode=completed.returncode, stdout_bytes=len(completed.stdout),
                          stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(), stderr_bytes=len(completed.stderr),
                          stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
            require(completed.returncode == 0 and len(completed.stdout) <= 4 * 1024 ** 2 and
                    len(completed.stderr) <= 4 * 1024 ** 2, 'SDK_COMMAND_UNKNOWN')
            if index == 0:
                rows = [parts for line in completed.stdout.decode('ascii').splitlines()
                        if len(parts := [x.strip() for x in line.split('|')]) == 4 and parts[0] == 'emulator']
                require(rows == [['emulator', kernel.FIXED['version'], 'Android Emulator', 'emulator']], 'SDK_REGISTERED_EMULATOR')
                result['emulator_registered_version'] = rows[0][1]
            prepared(ctx)
        image = private / 'system-images/android-36/google_apis/x86_64'
        data = raw_file(image / 'source.properties', 16384)
        properties = {}
        for line in data.decode('ascii').splitlines():
            if '=' not in line: continue
            key, value = line.split('=', 1)
            if key in ('AndroidVersion.ApiLevel', 'SystemImage.Abi', 'SystemImage.TagId'):
                require(key not in properties, 'SDK_IMAGE_IDENTITY'); properties[key] = value
        require(properties == {'AndroidVersion.ApiLevel': '36', 'SystemImage.Abi': 'x86_64',
                               'SystemImage.TagId': 'google_apis'} and (image / 'system.img').is_file()
                and (image / 'system.img').resolve() == image / 'system.img', 'SDK_IMAGE_IDENTITY')
        result.update(passed=True, image_properties=dict(api='36', abi='x86_64', tag='google_apis',
                      bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_IMAGE_UNAVAILABLE'
    emit(ctx, 'image-result', result)
    return result


def probe(ctx):
    value = prepared(ctx)
    image_ready(ctx)
    selection = read(ctx, 'launch-selection-intent', ('owner', 'launcher', 'automatic_retry'))
    require(selection['launcher'] == value['launcher'] and selection['automatic_retry'] is False, 'SDK_LAUNCH_INTENT')
    version_file = ctx['parent'] / 'emulator-version.txt'
    require(not version_file.exists() and not version_file.is_symlink(), 'SDK_ORIGINAL_RECORD_EXISTS')
    commands = [[str(ctx['launcher']), option] for option in ('-version', '-accel-check')]
    emit(ctx, 'probe-intent', dict(owner=ctx['owner'], argv=commands, automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, commands=[], runtime_verified=False)
    try:
        for command in commands:
            prepared(ctx)
            record = dict(argv=command, attempted=True, returncode=None)
            result['commands'].append(record)
            completed = subprocess.run(command, capture_output=True, timeout=30, check=False)
            record.update(returncode=completed.returncode, stdout_bytes=len(completed.stdout),
                          stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
                          stderr_bytes=len(completed.stderr), stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
            require(completed.returncode == 0 and len(completed.stdout) <= 65536 and
                    len(completed.stderr) <= 65536, 'SDK_COMMAND_UNKNOWN')
            if command[-1] == '-version':
                pattern = rb'(?:INFO[ ]{1,20}\|[ ]*)?(Android emulator version 37\.2\.12(?:\.0)? \(build_id 16428233\))(?: \(CL:[A-Za-z0-9/:._-]{1,40}\))?'
                admitted = [match.group(1) for stream in (completed.stdout, completed.stderr)
                            for line in stream.splitlines() if (match := re.fullmatch(pattern, line))]
                require(len(admitted) == 1, 'SDK_VERSION_UNAVAILABLE')
                with version_file.open('xb') as output: output.write(admitted[0] + b'\n')
                record['version_file_scope'] = VERSION_SCOPE
            prepared(ctx)
        result['passed'] = True
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_COMMAND_UNKNOWN'
    emit(ctx, 'probe-result', result)
    return result


def proc(pid):
    require(type(pid) is int and 1 <= pid <= 4194304, 'SDK_LAUNCH_PID')
    path = Path('/proc') / str(pid)
    try:
        before = path.stat()
        first = (path / 'stat').read_bytes()
        require(len(first) <= 4096 and first.startswith((str(pid) + ' (').encode()), 'SDK_PROC_IDENTITY')
        fields = first.rsplit(b') ', 1)[1].split()
        require(len(fields) >= 20 and fields[19].isdigit() and int(fields[19]) > 0, 'SDK_PROC_IDENTITY')
        state = fields[0].decode('ascii')
        executable = os.readlink(path / 'exe') if state != 'Z' else None
        second = (path / 'stat').read_bytes().rsplit(b') ', 1)[1].split()
        require(second[19] == fields[19] and path.stat().st_uid == before.st_uid, 'SDK_PROC_IDENTITY')
        return dict(pid=pid, uid=before.st_uid, start_ticks=int(fields[19]), state=state, executable=executable)
    except FileNotFoundError:
        require(not path.exists(), 'SDK_PROC_UNKNOWN')
        return None


def sdk_processes(ctx):
    values = list(Path('/proc').iterdir())
    require(len(values) <= 8192, 'SDK_PROC_LIMIT')
    found = []
    for path in values:
        if not path.name.isdigit(): continue
        try:
            if path.stat().st_uid != ctx['owner']['uid']: continue
            executable = os.readlink(path / 'exe')
            if owns_executable(ctx, executable): found.append(int(path.name))
        except FileNotFoundError:
            if path.exists():
                value = proc(int(path.name))
                require(value is None or value['state'] == 'Z', 'SDK_PROC_UNKNOWN')
    return found


def owns_executable(ctx, value):
    if type(value) is not str or len(value) > 4096:
        return False
    path = Path(value)
    prefix = ctx['root'] / 'runtime/emulator'
    return path.is_absolute() and '..' not in path.parts and path != prefix and path.is_relative_to(prefix)


def observe(ctx):
    observed_path = ctx['evidence'] / 'sdk-launch-observed.json'
    require(not observed_path.exists() and not observed_path.is_symlink(), 'SDK_ORIGINAL_RECORD_EXISTS')
    value = prepared(ctx)
    validate_probe(ctx)
    image = image_ready(ctx)
    binding = read(ctx, 'avd-binding', ('owner', 'avd', 'config_sha256', 'image_properties_sha256', 'runtime_verified'))
    avd = 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' + ctx['owner']['GITHUB_RUN_ATTEMPT']
    require(binding['avd'] == avd and binding['runtime_verified'] is False and
            binding['image_properties_sha256'] == image['image_properties']['sha256'] and
            hashlib.sha256(raw_file(ctx['parent'] / 'avd' / (avd + '.avd') / 'config.ini')).hexdigest() == binding['config_sha256'],
            'SDK_AVD_BINDING')
    intent = read(ctx, 'launch-selection-intent', ('owner', 'launcher', 'automatic_retry'))
    require(intent['launcher'] == value['launcher'] and intent['automatic_retry'] is False, 'SDK_LAUNCH_INTENT')
    raw = raw_file(ctx['parent'] / 'emulator.pid', 16)
    require(re.fullmatch(rb'[1-9][0-9]{0,7}\n', raw), 'SDK_LAUNCH_PID')
    identity = proc(int(raw))
    require(identity is not None and identity['uid'] == ctx['owner']['uid'] and
            identity['state'] != 'Z' and owns_executable(ctx, identity['executable'])
            and not identity['executable'].endswith(' (deleted)'),
            'SDK_PROC_IDENTITY')
    prepared(ctx)
    record = dict(owner=ctx['owner'], pid=identity['pid'], uid=identity['uid'], start_ticks=identity['start_ticks'],
                  runtime_verified=False)
    emit(ctx, 'launch-observed', record)
    return record


def adb_absent(ctx):
    adb = ctx['sdk'] / 'platform-tools/adb'
    require(adb.is_file() and not adb.is_symlink() and adb.resolve() == adb, 'SDK_ADB_UNKNOWN')
    command = [str(adb), 'devices']
    value = subprocess.run(command, capture_output=True, timeout=2, check=False)
    require(value.returncode == 0 and not value.stderr and len(value.stdout) <= 4096, 'SDK_ADB_UNKNOWN')
    lines = value.stdout.decode('ascii').splitlines()
    require(lines and lines[0] == 'List of devices attached' and all(not x.strip() for x in lines[1:]), 'SDK_AVD_PRESENT')
    return dict(argv=command, returncode=0, owned_serial_absent=True, all_other_devices_absent=True)


def cleanup(ctx):
    emit(ctx, 'cleanup-intent', dict(owner=ctx['owner'], automatic_retry=False, no_stop_or_kill=True))
    result = dict(owner=ctx['owner'], passed=False, no_stop_or_kill=True, runtime_verified=False)
    try:
        value = read(ctx, 'prepare-result', ('owner', 'passed', 'root_identity', 'launcher', 'kernel_sha256', 'runtime_verified'))
        require(value['runtime_verified'] is False and
                hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-kernel-result.json')).hexdigest() == value['kernel_sha256'],
                'SDK_PREPARE_RESULT')
        if value['passed'] is False and not ctx['root'].exists():
            result.update(passed=True, state='failed_preparation_namespace_absent', SDK_removed_here=False)
        else:
            owned_prepared(ctx)
            previous = None
            if (ctx['evidence'] / 'sdk-launch-observed.json').exists():
                previous = read(ctx, 'launch-observed', ('owner', 'pid', 'uid', 'start_ticks', 'runtime_verified'))
                require(type(previous['pid']) is int and 1 <= previous['pid'] <= 4194304 and
                        previous['uid'] == ctx['owner']['uid'] and type(previous['start_ticks']) is int and
                        previous['start_ticks'] > 0 and previous['runtime_verified'] is False, 'SDK_PROC_IDENTITY')
            deadline = time.monotonic() + 10
            last_clock = deadline - 10
            while True:
                now = time.monotonic()
                require(now >= last_clock and now <= deadline, 'SDK_CLEANUP_DEADLINE')
                last_clock = now
                require(boot() == ctx['owner']['host_boot_id'], 'SDK_BOOT')
                current = proc(previous['pid']) if previous else None
                if current is not None:
                    require(current['uid'] == previous['uid'] and current['start_ticks'] == previous['start_ticks'],
                            'SDK_ORIGINAL_PID_REUSED')
                found = sdk_processes(ctx)
                if not found and (current is None or current['state'] == 'Z'):
                    result['ADB_read'] = adb_absent(ctx)
                    owned_prepared(ctx)
                    final_pid = proc(previous['pid']) if previous else None
                    require(final_pid is None or (final_pid['uid'] == previous['uid'] and
                            final_pid['start_ticks'] == previous['start_ticks'] and final_pid['state'] == 'Z'),
                            'SDK_ORIGINAL_PID_REUSED')
                    require(not sdk_processes(ctx) and boot() == ctx['owner']['host_boot_id'], 'SDK_SDK_PROCESS_PRESENT')
                    root_owner(ctx, value)
                    require(last_clock <= time.monotonic() <= deadline, 'SDK_CLEANUP_DEADLINE')
                    shutil.rmtree(ctx['root'])
                    result.update(passed=True, state='SDK_removed_after_readonly_AVD_and_host_process_absence',
                                  SDK_removed_here=True, owned_SDK_executables_absent=True,
                                  original_host_PID_absent_or_exited=previous is not None)
                    break
                time.sleep(0.1)
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_CLEANUP_UNAVAILABLE'
        result.setdefault('SDK_removed_here', False)
    emit(ctx, 'cleanup-result', result)
    return result


def main(argv=None):
    actions = {'prepare': prepare, 'select-launcher': select_launcher, 'probe-version-and-accel': probe,
               'observe-launch': observe, 'cleanup': cleanup, 'install-image': install_image,
               'image-root': lambda ctx: (image_ready(ctx), str(ctx['root'] / 'runtime'))[1], 'bind-avd': bind_avd}
    args = sys.argv[1:] if argv is None else argv
    require(len(args) == 1 and args[0] in actions, 'SDK_UNKNOWN_ACTION')
    ctx = context()
    value = actions[args[0]](ctx)
    if args[0] in ('select-launcher', 'image-root'):
        print(value); return 0
    print(json.dumps(value, sort_keys=True))
    return 0 if value.get('passed', True) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        code = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_ACTION_UNAVAILABLE'
        print(json.dumps(dict(passed=False, guard_code=code, no_automatic_retry=True)))
        raise SystemExit(1)
