"""Closed hosted timeout SDK preparation, launch binding and read-only cleanup."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import android_release_sdk_archive as kernel
import android_release_sdk_layout as layout
import android_release_sdk_ini as ini

require = kernel.require
PREFIX = 'sdk-'
SERIAL = 'emulator-5562'
VERSION_SCOPE = 'One admitted original fixed SDK version prefix; other streams length/hash only'
REGISTRY_CLASS = 'cc.kronecker.opennexus.sdkinternal.OwnedEmulatorRegistry'
REGISTRY_MATCH = b'SDK_REGISTRY_SCHEMA_MATCH\n'
REGISTRY_PROPERTIES = (b'Pkg.UserSrc=false\nPkg.Revision=37.2.12\nPkg.Path=emulator\n'
                       b'Pkg.Desc=Android Emulator\nPkg.BuildId=16428233')
REGISTRY_KEYS = ('owner', 'passed', 'properties', 'package_xml', 'created_here', 'tools', 'commands',
                 'build_name', 'build_removed', 'SDKmanager_registered', 'runtime_verified')
SAFE = kernel.SAFE_CODES | layout.SAFE | frozenset('''SDK_CONTEXT SDK_BOOT SDK_JSON SDK_FILE_ALIAS
SDK_ORIGINAL_RECORD_EXISTS SDK_PREPARE_RESULT SDK_ROOT_OWNER SDK_LAUNCHER_CHANGED
SDK_LAUNCH_INTENT SDK_LAUNCH_PID SDK_PROC_UNKNOWN SDK_PROC_IDENTITY SDK_PROC_LIMIT
SDK_ADB_UNKNOWN SDK_AVD_PRESENT SDK_CLEANUP_DEADLINE SDK_CLEANUP_CLOCK
SDK_SDK_PROCESS_PRESENT SDK_CLEANUP_OWNER SDK_ORIGINAL_PID_REUSED SDK_UNKNOWN_ACTION
SDK_PROBE_IDENTITY SDK_COMMAND_UNKNOWN SDK_VERSION_UNAVAILABLE SDK_GLOBAL_TOOLS
SDK_LICENSE SDK_REGISTERED_EMULATOR SDK_IMAGE_IDENTITY SDK_AVD_BINDING
SDK_PROC_PRIVILEGED_READ SDK_PROC_OWNER_CHANGED SDK_REGISTRY_TOOLS SDK_REGISTRY_SOURCE
SDK_REGISTRY_CHANGED SDK_REGISTRY_COMMAND SDK_REGISTRY_BUILD_CLEANUP SDK_REGISTRY_NOT_READY
SDK_AVD_CONTEXT SDK_AVD_COMMAND SDK_AVD_ALREADY_EXISTS SDK_LAYOUT_BINDING SDK_LAYOUT_UNAVAILABLE'''.split())


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
    layout_ready(ctx)
    emit(ctx, 'launch-selection-intent', dict(owner=ctx['owner'], launcher=value['launcher'], automatic_retry=False))
    prepared(ctx)
    layout_ready(ctx)
    return str(ctx['launcher'])


def layout_owner(ctx):
    require(context() == ctx and boot() == ctx['owner']['host_boot_id'], 'SDK_LAYOUT_OWNER')
    prepared(ctx); image_ready(ctx)


def layout_lineage(ctx):
    return {key: hashlib.sha256(raw_file(ctx['evidence'] / ('sdk-' + key + '.json'))).hexdigest()
            for key in ('prepare-result', 'registry-result', 'image-result')}


def layout_records(ctx):
    result = layout._read(ctx, 'result')[0]
    return dict(intent=hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-layout-intent.json')).hexdigest(),
                inventory=result['inventory_sha256'],
                result=hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-layout-result.json')).hexdigest())


def prepare_layout(ctx):
    """One fixed dependency copy after the original image install and before any launch."""
    layout_owner(ctx)
    require(not list(ctx['evidence'].glob('sdk-runtime-dependencies-*.json')),
            'SDK_ORIGINAL_RECORD_EXISTS')
    lineage = layout_lineage(ctx)
    def guard():
        layout_owner(ctx)
        require(layout_lineage(ctx) == lineage and not any((ctx['parent'] / name).exists() or
                (ctx['parent'] / name).is_symlink() for name in ('emulator.pid', 'emulator.log', 'emulator-version.txt'))
                and not any((ctx['evidence'] / ('sdk-' + name + '.json')).exists() for name in
                ('launch-selection-intent', 'probe-intent', 'avd-create-intent', 'avd-binding', 'launch-observed')),
                'SDK_LAYOUT_BINDING')
    guard()
    emit(ctx, 'runtime-dependencies-intent', dict(owner=ctx['owner'], packages=list(layout.PACKAGES),
         lineage=lineage, automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, lineage=lineage, layout_records=None, runtime_verified=False)
    try:
        guard()
        copied = layout.prepare(ctx, guard)
        require(copied.get('passed') is True, copied.get('guard_code', 'SDK_LAYOUT_UNAVAILABLE'))
        layout.ready(ctx, guard)
        result['layout_records'] = layout_records(ctx)
        guard(); result['passed'] = True
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_LAYOUT_UNAVAILABLE'
        result['error_kind'] = {kernel.ArchiveError: 'sequence_guard', PermissionError: 'permission_denied',
            FileExistsError: 'file_exists', FileNotFoundError: 'file_missing', OSError: 'io'}.get(type(error), 'unclassified')
    emit(ctx, 'runtime-dependencies-result', result)
    return result


def layout_ready(ctx):
    layout_owner(ctx)
    intent = read(ctx, 'runtime-dependencies-intent', ('owner', 'packages', 'lineage', 'automatic_retry'))
    result = read(ctx, 'runtime-dependencies-result', ('owner', 'passed', 'lineage', 'layout_records', 'runtime_verified'))
    require(intent['packages'] == list(layout.PACKAGES) and intent['automatic_retry'] is False and
            intent['lineage'] == result['lineage'] == layout_lineage(ctx) and result['passed'] is True and
            result['runtime_verified'] is False and type(result['layout_records']) is dict and
            set(result['layout_records']) == {'intent', 'inventory', 'result'} and
            all(type(v) is str and re.fullmatch(r'[0-9a-f]{64}', v) for v in result['layout_records'].values()),
            'SDK_LAYOUT_BINDING')
    original = result['layout_records']
    def guard():
        layout_owner(ctx)
        require(layout_lineage(ctx) == result['lineage'] and layout_records(ctx) == original,
                'SDK_LAYOUT_BINDING')
    layout.ready(ctx, guard)
    guard()
    return result


def image_root(ctx):
    image_ready(ctx); layout_ready(ctx)
    return str(ctx['root'] / 'runtime')


def emulator_catalogue(raw):
    """Project only the installed fixed package from two closed listing layouts."""
    plain = type(raw) is bytes and len(raw) <= 4 * 1024 ** 2 and all(
        byte in (9, 10, 13) or 32 <= byte <= 126 for byte in raw)
    sections = available = candidates = matches = 0
    kinds = set()
    installed = False
    if plain:
        for line in raw.decode('ascii').splitlines():
            line = line.strip()
            if line == 'Installed packages:':
                sections += 1; installed = True; continue
            if line in ('Available packages:', 'Available Updates:'):
                available += 1; installed = False; continue
            if not installed or not line: continue
            if '|' in line:
                parts = [part.strip() for part in line.split('|')]
                if parts[0] != 'emulator': continue
                candidates += 1
                if (len(parts) == 4 and parts[:3] == ['emulator', kernel.FIXED['version'], 'Android Emulator']
                        and parts[3] in ('emulator', 'emulator/')):
                    matches += 1; kinds.add('legacy_table')
            elif line.split()[0] == 'emulator':
                candidates += 1
                if re.fullmatch(r'emulator[ \t]+37\.2\.12[ \t]+Android Emulator', line):
                    matches += 1; kinds.add('android_cli_table')
    passed = plain and sections == 1 and available == 0 and candidates == matches == 1
    return dict(plain_ascii=plain, installed_sections=min(sections, 2), available_sections=min(available, 2),
                emulator_candidate_rows=min(candidates, 2), matching_rows=min(matches, 2),
                catalogue_kind=next(iter(kinds)) if len(kinds) == 1 else 'none', installed_exactly_once=passed)


def registry_tools(ctx):
    reference = os.environ.get('JAVA_HOME', '')
    home = Path(reference)
    require(home.is_absolute() and '..' not in home.parts and home.is_dir(),
            'SDK_REGISTRY_TOOLS')
    # setup-java exports a tool-cache reference. Bind it and its physical JDK;
    # only read-only tool discovery resolves references, never output paths.
    def identity(value):
        return [value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_mtime_ns]
    reference_identity = identity(home.lstat())
    resolved = home.resolve(strict=True)
    require(resolved.is_dir() and not resolved.is_symlink() and resolved.resolve() == resolved,
            'SDK_REGISTRY_TOOLS')
    target_identity = identity(resolved.stat())
    suffix = '.exe' if os.name == 'nt' else ''
    paths = dict(java=resolved / ('bin/java' + suffix), javac=resolved / ('bin/javac' + suffix),
                 classpath=ctx['sdk'] / 'cmdline-tools/latest/lib/avdmanager-classpath.jar',
                 source=Path(__file__).resolve().parent / 'android-sdk-tools/OwnedEmulatorRegistry.java')
    tools = {name: dict(path=str(path), **fingerprint(path)) for name, path in paths.items()}
    require(os.environ.get('JAVA_HOME', '') == reference and identity(home.lstat()) == reference_identity
            and home.resolve(strict=True) == resolved and identity(resolved.stat()) == target_identity,
            'SDK_REGISTRY_CHANGED')
    tools['java_home'] = dict(reference=reference, reference_identity=reference_identity,
                              resolved=str(resolved), target_identity=target_identity)
    return tools


def registry_snapshot(ctx, tools, expected_xml):
    prepared(ctx)
    require(boot() == ctx['owner']['host_boot_id'] and registry_tools(ctx) == tools, 'SDK_REGISTRY_CHANGED')
    directory = ctx['root'] / 'runtime/emulator'
    require(raw_file(directory / 'source.properties', 104) == REGISTRY_PROPERTIES, 'SDK_REGISTRY_SOURCE')
    path = directory / 'package.xml'
    if expected_xml is None:
        require(not path.exists() and not path.is_symlink(), 'SDK_REGISTRY_CHANGED')
    else:
        require(raw_file(path) == expected_xml, 'SDK_REGISTRY_CHANGED')


def register_owned(ctx):
    prepared(ctx)
    require(not list(ctx['evidence'].glob('sdk-registry-*.json')), 'SDK_ORIGINAL_RECORD_EXISTS')
    properties = dict(bytes=len(REGISTRY_PROPERTIES), sha256=hashlib.sha256(REGISTRY_PROPERTIES).hexdigest())
    emit(ctx, 'registry-intent', dict(owner=ctx['owner'], properties=properties,
         package_relative='runtime/emulator/package.xml', automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, properties=properties, package_xml=None,
                  created_here=False, tools={}, commands=[], build_name=None, build_removed=True,
                  SDKmanager_registered=False, runtime_verified=False)
    build = None
    try:
        directory = ctx['root'] / 'runtime/emulator'
        require(raw_file(directory / 'source.properties', 104) == REGISTRY_PROPERTIES, 'SDK_REGISTRY_SOURCE')
        target = directory / 'package.xml'
        xml = raw_file(target) if target.exists() or target.is_symlink() else None
        if xml is not None: result['package_xml'] = dict(bytes=len(xml), sha256=hashlib.sha256(xml).hexdigest())
        tools = registry_tools(ctx); result['tools'] = tools
        registry_snapshot(ctx, tools, xml)
        parent = ctx['parent']; parent_stat = parent.stat()
        parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        build = Path(tempfile.mkdtemp(prefix='sdk-registry-build-', dir=parent))
        build_stat = build.stat(); build_identity = (build_stat.st_dev, build_stat.st_ino)
        result.update(build_name=build.name, build_removed=False)
        require(build.parent == parent and build.resolve() == build and not build.is_symlink(), 'SDK_REGISTRY_BUILD_CLEANUP')

        def invoke(argv, data=None):
            registry_snapshot(ctx, tools, xml)
            number = len(result['commands']) + 1
            input_meta = dict(stdin_bytes=len(data or b''), stdin_sha256=hashlib.sha256(data or b'').hexdigest())
            emit(ctx, 'registry-command-' + str(number) + '-intent',
                 dict(owner=ctx['owner'], argv=argv, automatic_retry=False, **input_meta))
            registry_snapshot(ctx, tools, xml)
            record = dict(argv=argv, attempted=True, returncode=None, **input_meta)
            result['commands'].append(record)
            options = dict(capture_output=True, timeout=40, check=False)
            if data is None: options['stdin'] = subprocess.DEVNULL
            else: options['input'] = data
            value = subprocess.run(argv, **options)
            record.update(returncode=value.returncode, stdout_bytes=len(value.stdout),
                          stdout_sha256=hashlib.sha256(value.stdout).hexdigest(), stderr_bytes=len(value.stderr),
                          stderr_sha256=hashlib.sha256(value.stderr).hexdigest())
            require(value.returncode == 0 and len(value.stdout) <= 65536 and len(value.stderr) <= 65536,
                    'SDK_REGISTRY_COMMAND')
            registry_snapshot(ctx, tools, xml)
            return value

        compile_argv = [tools['javac']['path'], '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath',
                        tools['classpath']['path'], '-d', str(build), tools['source']['path']]
        invoke(compile_argv)
        class_file = build.joinpath(*REGISTRY_CLASS.split('.')).with_suffix('.class')
        require(bool(raw_file(class_file, 1024 ** 2)), 'SDK_REGISTRY_COMMAND')
        launch = [tools['java']['path'], '-Xmx64m', '-cp', str(build) + os.pathsep + tools['classpath']['path'], REGISTRY_CLASS]
        if xml is None:
            derived = invoke(launch + ['derive'], REGISTRY_PROPERTIES)
            require(derived.stderr == b'' and 0 < len(derived.stdout) <= 65536, 'SDK_REGISTRY_COMMAND')
            data = derived.stdout
            emit(ctx, 'registry-write-intent', dict(owner=ctx['owner'], package_relative='runtime/emulator/package.xml',
                 original_absent=True, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), automatic_retry=False))
            registry_snapshot(ctx, tools, None)
            with target.open('xb') as output:
                result['created_here'] = True
                output.write(data); output.flush(); os.fsync(output.fileno())
            xml = data
        result['package_xml'] = dict(bytes=len(xml), sha256=hashlib.sha256(xml).hexdigest())
        registry_snapshot(ctx, tools, xml)
        validated = invoke(launch + ['validate'], raw_file(target))
        require(validated.stdout == REGISTRY_MATCH and validated.stderr == b'', 'SDK_REGISTRY_COMMAND')
        registry_snapshot(ctx, tools, xml)
        result['passed'] = True
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_REGISTRY_UNAVAILABLE'
        result['error_kind'] = type(error).__name__ if type(error) in (
            kernel.ArchiveError, subprocess.TimeoutExpired, PermissionError, FileNotFoundError, FileExistsError) else 'unclassified'
    finally:
        if build is not None:
            try:
                require(parent.resolve() == parent and not parent.is_symlink() and
                        (parent.stat().st_dev, parent.stat().st_ino) == parent_identity and
                        build.parent == parent and build.resolve() == build and not build.is_symlink() and
                        (build.stat().st_dev, build.stat().st_ino) == build_identity, 'SDK_REGISTRY_BUILD_CLEANUP')
                shutil.rmtree(build)
                require(not build.exists() and not build.is_symlink(), 'SDK_REGISTRY_BUILD_CLEANUP')
                result['build_removed'] = True
            except Exception:
                result['passed'] = False
                result.setdefault('guard_code', 'SDK_REGISTRY_BUILD_CLEANUP')
                result['build_cleanup_unknown'] = True
    emit(ctx, 'registry-result', result)
    return result


def registry_ready(ctx):
    value = read(ctx, 'registry-result', REGISTRY_KEYS)
    require(value['passed'] is True and value['build_removed'] is True and
            value['SDKmanager_registered'] is False and value['runtime_verified'] is False and
            type(value['created_here']) is bool, 'SDK_REGISTRY_NOT_READY')
    xml = raw_file(ctx['root'] / 'runtime/emulator/package.xml')
    require(value['properties'] == dict(bytes=len(REGISTRY_PROPERTIES), sha256=hashlib.sha256(REGISTRY_PROPERTIES).hexdigest())
            and value['package_xml'] == dict(bytes=len(xml), sha256=hashlib.sha256(xml).hexdigest()), 'SDK_REGISTRY_CHANGED')
    registry_snapshot(ctx, value['tools'], xml)
    require(type(value['build_name']) is str and re.fullmatch(r'sdk-registry-build-[a-z0-9_]{8}', value['build_name']),
            'SDK_REGISTRY_NOT_READY')
    build = ctx['parent'] / value['build_name']
    require(not build.exists() and not build.is_symlink(), 'SDK_REGISTRY_BUILD_CLEANUP')
    intent = read(ctx, 'registry-intent', ('owner', 'properties', 'package_relative', 'automatic_retry'))
    require(intent['properties'] == value['properties'] and intent['package_relative'] == 'runtime/emulator/package.xml'
            and intent['automatic_retry'] is False, 'SDK_REGISTRY_NOT_READY')
    tools = value['tools']
    launch = [tools['java']['path'], '-Xmx64m', '-cp', str(build) + os.pathsep + tools['classpath']['path'], REGISTRY_CLASS]
    plans = [([tools['javac']['path'], '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath', tools['classpath']['path'],
               '-d', str(build), tools['source']['path']], b'')]
    if value['created_here']:
        plans.append((launch + ['derive'], REGISTRY_PROPERTIES))
        write = read(ctx, 'registry-write-intent', ('owner', 'package_relative', 'original_absent', 'bytes', 'sha256', 'automatic_retry'))
        require(write['package_relative'] == 'runtime/emulator/package.xml' and write['original_absent'] is True and
                write['automatic_retry'] is False and dict(bytes=write['bytes'], sha256=write['sha256']) == value['package_xml'],
                'SDK_REGISTRY_NOT_READY')
    else:
        require(not (ctx['evidence'] / 'sdk-registry-write-intent.json').exists(), 'SDK_REGISTRY_NOT_READY')
    plans.append((launch + ['validate'], xml))
    require(type(value['commands']) is list and len(value['commands']) == len(plans), 'SDK_REGISTRY_NOT_READY')
    for index, (record, (argv, data)) in enumerate(zip(value['commands'], plans), 1):
        expected_input = dict(stdin_bytes=len(data), stdin_sha256=hashlib.sha256(data).hexdigest())
        require(type(record) is dict and set(record) == {'argv', 'attempted', 'returncode', 'stdin_bytes', 'stdin_sha256',
                'stdout_bytes', 'stdout_sha256', 'stderr_bytes', 'stderr_sha256'} and record['argv'] == argv and
                record['attempted'] is True and type(record['returncode']) is int and record['returncode'] == 0 and
                type(record['stdin_bytes']) is int and
                all(record[k] == v for k, v in expected_input.items()), 'SDK_REGISTRY_NOT_READY')
        original = read(ctx, 'registry-command-' + str(index) + '-intent',
                        ('owner', 'argv', 'automatic_retry', 'stdin_bytes', 'stdin_sha256'))
        require(original['argv'] == argv and original['automatic_retry'] is False and
                type(original['stdin_bytes']) is int and
                all(original[k] == v for k, v in expected_input.items()), 'SDK_REGISTRY_NOT_READY')
        for channel in ('stdout', 'stderr'):
            require(type(record[channel + '_bytes']) is int and 0 <= record[channel + '_bytes'] <= 65536 and
                    re.fullmatch(r'[0-9a-f]{64}', record[channel + '_sha256']), 'SDK_REGISTRY_NOT_READY')
        if argv[-1] in ('derive', 'validate'):
            output = xml if argv[-1] == 'derive' else REGISTRY_MATCH
            require(record['stdout_bytes'] == len(output) and record['stdout_sha256'] == hashlib.sha256(output).hexdigest()
                    and record['stderr_bytes'] == 0 and record['stderr_sha256'] == hashlib.sha256(b'').hexdigest(),
                    'SDK_REGISTRY_NOT_READY')
    return value


def image_ready(ctx):
    prepared(ctx)
    value = read(ctx, 'image-result', ('owner', 'passed', 'commands', 'image_properties',
                                      'emulator_registered_version', 'emulator_catalogue', 'registry_sha256', 'licenses', 'runtime_verified'))
    require(value['passed'] is True and value['runtime_verified'] is False and
            value['emulator_registered_version'] == kernel.FIXED['version'], 'SDK_IMAGE_IDENTITY')
    registry_ready(ctx)
    require(value['registry_sha256'] == hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-registry-result.json')).hexdigest(),
            'SDK_REGISTRY_NOT_READY')
    catalogue = value['emulator_catalogue']
    require(type(catalogue) is dict and set(catalogue) == {'plain_ascii', 'installed_sections', 'available_sections',
            'emulator_candidate_rows', 'matching_rows', 'catalogue_kind', 'installed_exactly_once'} and
            catalogue['plain_ascii'] is True and catalogue['installed_exactly_once'] is True and
            all(type(catalogue[key]) is int and catalogue[key] == expected for key, expected in (
                ('installed_sections', 1), ('available_sections', 0), ('emulator_candidate_rows', 1), ('matching_rows', 1))) and
            catalogue['catalogue_kind'] in ('legacy_table', 'android_cli_table'), 'SDK_IMAGE_IDENTITY')
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
    try: record_ini_prelaunch(ctx, value, data)
    except Exception: pass
    return value


def create_avd(ctx):
    """One original vendor CLI call with an explicit owned SDK lookup context."""
    image_ready(ctx); validate_probe(ctx); layout_ready(ctx)
    tools = registry_ready(ctx)['tools']
    name = 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' + ctx['owner']['GITHUB_RUN_ATTEMPT']
    avd = ctx['parent'] / 'avd' / (name + '.avd')
    locator = avd.with_suffix('.ini')
    private = ctx['root'] / 'runtime'
    descriptor = private / 'cmdline-tools/avd-root-context'
    lineage = {key: hashlib.sha256(raw_file(ctx['evidence'] / ('sdk-' + key + '.json'))).hexdigest()
               for key in ('registry-result', 'image-result', 'probe-result', 'runtime-dependencies-result')}
    argv = [tools['java']['path'], '-Xmx128m', '-Dcom.android.sdkmanager.toolsdir=' + str(descriptor),
            '-cp', tools['classpath']['path'], 'com.android.sdklib.tool.AvdManagerCli',
            'create', 'avd', '--name', name, '--package', 'system-images;android-36;google_apis;x86_64',
            '--device', 'pixel_7']
    require(not list(ctx['evidence'].glob('sdk-avd-create-*.json')), 'SDK_ORIGINAL_RECORD_EXISTS')
    emit(ctx, 'avd-create-intent', dict(owner=ctx['owner'], argv=argv, lineage=lineage,
         descriptor_relative='runtime/cmdline-tools/avd-root-context', automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, command=None, binding=None, locator_sha256=None,
                  lineage=lineage, runtime_verified=False, no_automatic_retry=True)
    descriptor_identity = None
    try:
        def guard(*, missing):
            image_ready(ctx); validate_probe(ctx); layout_ready(ctx)
            require(registry_tools(ctx) == tools and boot() == ctx['owner']['host_boot_id'], 'SDK_AVD_CONTEXT')
            for key, digest in lineage.items():
                require(hashlib.sha256(raw_file(ctx['evidence'] / ('sdk-' + key + '.json'))).hexdigest() == digest,
                        'SDK_AVD_CONTEXT')
            if descriptor_identity is not None:
                require(descriptor.is_dir() and not descriptor.is_symlink() and descriptor.resolve() == descriptor and
                        (descriptor.stat().st_dev, descriptor.stat().st_ino) == descriptor_identity, 'SDK_AVD_CONTEXT')
            if missing:
                require(all(not path.exists() and not path.is_symlink() for path in (avd, locator)), 'SDK_AVD_ALREADY_EXISTS')
        guard(missing=True)
        parent = descriptor.parent
        if parent.exists():
            require(parent.is_dir() and not parent.is_symlink() and parent.resolve() == parent, 'SDK_AVD_CONTEXT')
        else: parent.mkdir()
        require(not descriptor.exists() and not descriptor.is_symlink(), 'SDK_AVD_CONTEXT')
        descriptor.mkdir()
        descriptor_identity = (descriptor.stat().st_dev, descriptor.stat().st_ino)
        guard(missing=True)
        # This directory is a lookup anchor, not a copied/registered cmdline-tools package.
        # Vendor init() resolves its grandparent as SDK root; the global classpath stays read-only.
        emit(ctx, 'avd-create-command-intent', dict(owner=ctx['owner'], argv=argv,
             stdin_bytes=3, stdin_sha256=hashlib.sha256(b'no\n').hexdigest(), automatic_retry=False))
        guard(missing=True)
        emit(ctx, 'avd-create-command-attempt', dict(owner=ctx['owner'], argv=argv, attempted=True))
        guard(missing=True)
        record = dict(argv=argv, attempted=True, returncode=None)
        result['command'] = record
        env = dict(os.environ, ANDROID_HOME=str(private), ANDROID_SDK_ROOT=str(private))
        completed = subprocess.run(argv, input=b'no\n', env=env, capture_output=True, timeout=120, check=False)
        record.update(returncode=completed.returncode, stdout_bytes=len(completed.stdout),
                      stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(), stderr_bytes=len(completed.stderr),
                      stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
        require(type(completed.returncode) is int and completed.returncode == 0 and
                len(completed.stdout) <= 4 * 1024 ** 2 and len(completed.stderr) <= 4 * 1024 ** 2, 'SDK_AVD_COMMAND')
        guard(missing=False)
        original = raw_file(locator)
        paths = [line.split('=', 1)[1] for line in original.decode('ascii').splitlines() if line.startswith('path=')]
        require(paths == [str(avd)] and avd.is_dir() and not avd.is_symlink() and avd.resolve() == avd, 'SDK_AVD_CONTEXT')
        result['binding'] = bind_avd(ctx)
        result['locator_sha256'] = hashlib.sha256(original).hexdigest()
        guard(missing=False)
        require(raw_file(locator) == original, 'SDK_AVD_CONTEXT')
        result['passed'] = True
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_AVD_COMMAND'
        result['error_kind'] = {kernel.ArchiveError: 'sequence_guard', subprocess.TimeoutExpired: 'timeout',
            PermissionError: 'permission_denied', FileExistsError: 'file_exists', FileNotFoundError: 'file_missing',
            OSError: 'io'}.get(type(error), 'unclassified')
    emit(ctx, 'avd-create-result', result)
    return result


def install_image(ctx):
    prepared(ctx)
    private = ctx['root'] / 'runtime'
    manager = ctx['sdk'] / 'cmdline-tools/latest/bin/sdkmanager'
    commands = [[str(manager), '--sdk_root=' + str(private), '--list_installed'],
                [str(manager), '--sdk_root=' + str(private), '--install', 'system-images;android-36;google_apis;x86_64']]
    emit(ctx, 'image-intent', dict(owner=ctx['owner'], argv=commands, automatic_retry=False))
    result = dict(owner=ctx['owner'], passed=False, commands=[], image_properties=None,
                  emulator_registered_version=None, emulator_catalogue=None, registry_sha256=None, licenses={}, runtime_verified=False)
    try:
        registered = register_owned(ctx)
        require(registered['passed'], 'SDK_REGISTRY_NOT_READY')
        registry_ready(ctx)
        result['registry_sha256'] = hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-registry-result.json')).hexdigest()
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
            registry_ready(ctx)
            record = dict(argv=command, attempted=True, returncode=None); result['commands'].append(record)
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=900, check=False)
            record.update(returncode=completed.returncode, stdout_bytes=len(completed.stdout),
                          stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(), stderr_bytes=len(completed.stderr),
                          stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
            require(completed.returncode == 0 and len(completed.stdout) <= 4 * 1024 ** 2 and
                    len(completed.stderr) <= 4 * 1024 ** 2, 'SDK_COMMAND_UNKNOWN')
            if index == 0:
                result['emulator_catalogue'] = emulator_catalogue(completed.stdout)
                require(result['emulator_catalogue']['installed_exactly_once'], 'SDK_REGISTERED_EMULATOR')
                result['emulator_registered_version'] = kernel.FIXED['version']
            prepared(ctx)
            registry_ready(ctx)
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
    layout_ready(ctx)
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
            layout_ready(ctx)
            record = dict(argv=command, attempted=True, returncode=None)
            result['commands'].append(record)
            completed = subprocess.run(command, capture_output=True, timeout=30, check=False)
            record.update(returncode=completed.returncode, stdout_bytes=len(completed.stdout),
                          stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
                          stderr_bytes=len(completed.stderr), stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
            require(completed.returncode == 0 and len(completed.stdout) <= 65536 and
                    len(completed.stderr) <= 65536, 'SDK_COMMAND_UNKNOWN')
            layout_ready(ctx)
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


def sdk_stat(path):
    require(path.parent == Path('/proc') and path.name.isascii() and path.name.isdigit() and
            str(int(path.name)) == path.name and 1 <= int(path.name) <= 4194304, 'SDK_PROC_IDENTITY')
    before = path.stat()
    raw = (path / 'stat').read_bytes()
    require(len(raw) <= 4096 and raw.startswith((path.name + ' (').encode()) and b') ' in raw, 'SDK_PROC_IDENTITY')
    fields = raw.rsplit(b') ', 1)[1].split()
    require(len(fields) >= 20 and fields[0] in tuple(bytes([v]) for v in b'RSDZTtWXxKWPIN') and
            fields[19].isdigit() and int(fields[19]) > 0, 'SDK_PROC_IDENTITY')
    after = path.stat()
    require((before.st_uid, before.st_dev, before.st_ino) == (after.st_uid, after.st_dev, after.st_ino),
            'SDK_PROC_OWNER_CHANGED')
    return before.st_uid, int(fields[19]), fields[0]


def sdk_permission_readlink(ctx, path, identity):
    """One read of a denied executable link; no permission, process or file writes."""
    require(identity[0] == ctx['owner']['uid'] and sdk_stat(path)[:2] == identity[:2] and
            boot() == ctx['owner']['host_boot_id'], 'SDK_PROC_OWNER_CHANGED')
    command = ['/usr/bin/sudo', '-n', '--', '/usr/bin/readlink', '--', '/proc/' + path.name + '/exe']
    started = time.monotonic()
    value = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=1, check=False)
    require(type(value.returncode) is int and value.returncode == 0 and value.stderr == b'' and
            type(value.stdout) is bytes and 2 <= len(value.stdout) <= 4096 and value.stdout.endswith(b'\n') and
            all(32 <= byte <= 126 for byte in value.stdout[:-1]), 'SDK_PROC_PRIVILEGED_READ')
    text = value.stdout[:-1].decode('ascii')
    posix = PurePosixPath(text)
    require(posix.is_absolute() and not text.startswith('//') and '..' not in posix.parts and str(posix) == text,
            'SDK_PROC_PRIVILEGED_READ')
    require(sdk_stat(path)[:2] == identity[:2] and boot() == ctx['owner']['host_boot_id'], 'SDK_PROC_OWNER_CHANGED')
    require(started <= time.monotonic() <= started + 1, 'SDK_PROC_PRIVILEGED_READ')
    return text


def sdk_processes(ctx):
    values = list(Path('/proc').iterdir())
    require(len(values) <= 8192, 'SDK_PROC_LIMIT')
    found = []
    for path in values:
        if not path.name.isdigit(): continue
        try:
            if path.stat().st_uid != ctx['owner']['uid']: continue
            identity = sdk_stat(path)
            require(identity[0] == ctx['owner']['uid'], 'SDK_PROC_OWNER_CHANGED')
            if identity[2] == b'Z': continue
            try:
                executable = os.readlink(path / 'exe')
            except PermissionError:
                executable = sdk_permission_readlink(ctx, path, identity)
            require(sdk_stat(path)[:2] == identity[:2], 'SDK_PROC_OWNER_CHANGED')
            if owns_executable(ctx, executable) or owns_dependency_executable(ctx, executable): found.append(int(path.name))
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


def owns_dependency_executable(ctx, value):
    if type(value) is not str or len(value) > 4096: return False
    path = Path(value)
    return path.is_absolute() and '..' not in path.parts and any(path != ctx['root'] / 'runtime' / package and
           path.is_relative_to(ctx['root'] / 'runtime' / package) for package in layout.PACKAGES)


def checked_ini_projection(value):
    keys = {'kind', 'source_bytes', 'source_sha256', 'available', 'canonical_sha256', 'key_count',
            'image_literal_matches', 'abi_x86_64', 'tag_google_apis', 'unavailable_reason', 'runtime_verified'}
    require(type(value) is dict and set(value) == keys and value['kind'] == ini.KIND and
            type(value['source_bytes']) is int and 0 <= value['source_bytes'] <= ini.MAX_BYTES and
            type(value['source_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', value['source_sha256']) and
            type(value['available']) is bool and value['runtime_verified'] is False, 'SDK_AVD_BINDING')
    if value['available']:
        require(type(value['canonical_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', value['canonical_sha256']) and
                type(value['key_count']) is int and 1 <= value['key_count'] <= ini.MAX_KEYS and
                all(type(value[k]) is bool for k in ('image_literal_matches', 'abi_x86_64', 'tag_google_apis')) and
                value['unavailable_reason'] is None, 'SDK_AVD_BINDING')
    else:
        require(all(value[k] is None for k in ('canonical_sha256', 'key_count', 'image_literal_matches',
                'abi_x86_64', 'tag_google_apis')) and value['unavailable_reason'] in
                ('non_ascii', 'control_byte', 'line_limit', 'assignment', 'key', 'value_remainder',
                 'duplicate', 'key_limit', 'empty'), 'SDK_AVD_BINDING')
    return value


def record_ini_prelaunch(ctx, binding, data):
    """Optional diagnostic metadata from the original bind read; never a second config read."""
    projection = checked_ini_projection(ini.project(ctx, data))
    require(binding['owner'] == ctx['owner'] and binding['runtime_verified'] is False and
            projection['source_sha256'] == binding['config_sha256'], 'SDK_AVD_BINDING')
    emit(ctx, 'avd-config-semantics', dict(owner=ctx['owner'], stage='bind-original-config',
         avd=binding['avd'], config_sha256=binding['config_sha256'],
         image_properties_sha256=binding['image_properties_sha256'], projection=projection, runtime_verified=False))
    fingerprints = checked_ini_fingerprints(ini.fingerprints(ctx, data), projection)
    emit(ctx, 'avd-config-fingerprints', dict(owner=ctx['owner'], stage='bind-original-config',
         avd=binding['avd'], config_sha256=binding['config_sha256'],
         image_properties_sha256=binding['image_properties_sha256'], fingerprints=fingerprints, runtime_verified=False))


def expected_ini_projection(ctx, binding):
    """One read of the original owned sidecar; absent/unknown metadata stays unavailable."""
    try:
        original = read(ctx, 'avd-config-semantics', ('owner', 'stage', 'avd', 'config_sha256',
                        'image_properties_sha256', 'projection', 'runtime_verified'))
        require(type(original['owner']['uid']) is int and original['stage'] == 'bind-original-config' and
                original['avd'] == binding['avd'] == 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' +
                ctx['owner']['GITHUB_RUN_ATTEMPT'] and binding['runtime_verified'] is False and
                original['runtime_verified'] is False and original['config_sha256'] == binding['config_sha256'] and
                original['image_properties_sha256'] == binding['image_properties_sha256'] and
                type(original['image_properties_sha256']) is str and
                re.fullmatch(r'[0-9a-f]{64}', original['image_properties_sha256']), 'SDK_AVD_BINDING')
        projection = checked_ini_projection(original['projection'])
        require(projection['source_sha256'] == binding['config_sha256'], 'SDK_AVD_BINDING')
        return projection
    except Exception:
        return None


def checked_ini_fingerprints(value, projection):
    require(ini.fingerprints_valid(value) and all(type(value[k]) is type(projection[k]) and value[k] == projection[k]
            for k in ('source_bytes', 'source_sha256', 'canonical_sha256', 'key_count')),
            'SDK_AVD_BINDING')
    require(not value['available'] or projection['available'] is True, 'SDK_AVD_BINDING')
    return value


def expected_ini_fingerprints(ctx, binding, projection):
    if projection is None: return None
    try:
        original = read(ctx, 'avd-config-fingerprints', ('owner', 'stage', 'avd', 'config_sha256',
                        'image_properties_sha256', 'fingerprints', 'runtime_verified'))
        require(type(original['owner']['uid']) is int and original['stage'] == 'bind-original-config' and
                original['avd'] == binding['avd'] == 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' +
                ctx['owner']['GITHUB_RUN_ATTEMPT'] and original['runtime_verified'] is False and
                original['config_sha256'] == binding['config_sha256'] == projection['source_sha256'] and
                original['image_properties_sha256'] == binding['image_properties_sha256'], 'SDK_AVD_BINDING')
        return checked_ini_fingerprints(original['fingerprints'], projection)
    except Exception:
        return None


def avd_config_facts(ctx, data):
    """Only fixed booleans and hashes from the existing read, never config text or paths."""
    require(type(data) is bytes and len(data) <= 65536, 'SDK_AVD_BINDING')
    values = {key: [] for key in ('image.sysdir.1', 'abi.type', 'tag.id')}
    try:
        for line in data.decode('ascii').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                if key in values: values[key].append(value)
        ascii_only = True
    except UnicodeDecodeError:
        ascii_only = False; values = {key: [] for key in values}
    image = 'system-images/android-36/google_apis/x86_64'
    admitted = (image, image + '/', str(ctx['root'] / 'runtime').replace('\\', '/') + '/' + image,
                str(ctx['root'] / 'runtime').replace('\\', '/') + '/' + image + '/')
    return dict(config_bytes=len(data), observed_config_sha256=hashlib.sha256(data).hexdigest(),
                config_ascii=ascii_only, selected_keys_unique=all(len(v) == 1 for v in values.values()),
                image_sysdir_literal_matches=len(values['image.sysdir.1']) == 1 and values['image.sysdir.1'][0] in admitted,
                abi_x86_64=values['abi.type'] == ['x86_64'], tag_google_apis=values['tag.id'] == ['google_apis'],
                ini_projection=ini.project(ctx, data), ini_fingerprints=ini.fingerprints(ctx, data))


def record_avd_binding_failure(ctx, binding, image, avd, observed):
    expected = binding['config_sha256']
    require(type(expected) is str and re.fullmatch(r'[0-9a-f]{64}', expected), 'SDK_AVD_BINDING')
    keys = {'config_bytes', 'observed_config_sha256', 'config_ascii', 'selected_keys_unique',
            'image_sysdir_literal_matches', 'abi_x86_64', 'tag_google_apis', 'ini_projection', 'ini_fingerprints'}
    if observed is not None:
        require(type(observed) is dict and set(observed) == keys and type(observed['config_bytes']) is int and
                0 <= observed['config_bytes'] <= 65536 and type(observed['observed_config_sha256']) is str and
                re.fullmatch(r'[0-9a-f]{64}', observed['observed_config_sha256']) and
                all(type(observed[k]) is bool for k in keys - {'config_bytes', 'observed_config_sha256', 'ini_projection', 'ini_fingerprints'}),
                'SDK_AVD_BINDING')
        projection = checked_ini_projection(observed['ini_projection'])
        require(projection['source_bytes'] == observed['config_bytes'] and
                projection['source_sha256'] == observed['observed_config_sha256'], 'SDK_AVD_BINDING')
        checked_ini_fingerprints(observed['ini_fingerprints'], projection)
    facts = dict(binding_name_matches=binding['avd'] == avd, binding_runtime_false=binding['runtime_verified'] is False,
                 binding_image_matches=binding['image_properties_sha256'] == image['image_properties']['sha256'],
                 expected_config_sha256=expected, config_read=observed is not None,
                 config_hash_matches=None if observed is None else observed['observed_config_sha256'] == expected)
    facts.update({key: None for key in keys} if observed is None else observed)
    expected_projection = expected_ini_projection(ctx, binding)
    facts.update(expected_ini_projection=expected_projection, ini_origin_record_valid=expected_projection is not None,
                 ini_full_map_equal=None)
    if expected_projection is not None and expected_projection['available'] and observed is not None and observed['ini_projection']['available']:
        current = observed['ini_projection']
        facts['ini_full_map_equal'] = (expected_projection['canonical_sha256'] == current['canonical_sha256'] and
                                      expected_projection['key_count'] == current['key_count'])
    expected_fingerprints = expected_ini_fingerprints(ctx, binding, expected_projection)
    facts['ini_fingerprint_origin_valid'] = expected_fingerprints is not None
    facts['ini_key_delta'] = ini.fingerprint_delta(expected_fingerprints,
                                None if observed is None else observed['ini_fingerprints'])
    emit(ctx, 'avd-binding-failure', dict(owner=ctx['owner'], stage='observe-launch-binding',
         guard_code='SDK_AVD_BINDING', facts=facts, whole_passed=False, runtime_verified=False))


def observe(ctx):
    observed_path = ctx['evidence'] / 'sdk-launch-observed.json'
    require(not observed_path.exists() and not observed_path.is_symlink(), 'SDK_ORIGINAL_RECORD_EXISTS')
    value = prepared(ctx)
    validate_probe(ctx)
    image = image_ready(ctx)
    binding = read(ctx, 'avd-binding', ('owner', 'avd', 'config_sha256', 'image_properties_sha256', 'runtime_verified'))
    avd = 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' + ctx['owner']['GITHUB_RUN_ATTEMPT']
    observed = None
    def original_config_matches():
        nonlocal observed
        data = raw_file(ctx['parent'] / 'avd' / (avd + '.avd') / 'config.ini')
        observed = avd_config_facts(ctx, data)
        return hashlib.sha256(data).hexdigest() == binding['config_sha256']
    try:
        require(binding['avd'] == avd and binding['runtime_verified'] is False and
                binding['image_properties_sha256'] == image['image_properties']['sha256'] and original_config_matches(),
                'SDK_AVD_BINDING')
    except Exception as error:
        if type(error) is kernel.ArchiveError and str(error) == 'SDK_AVD_BINDING':
            try: record_avd_binding_failure(ctx, binding, image, avd, observed)
            except Exception: pass
        raise
    layout_ready(ctx)
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
    require(boot() == ctx['owner']['host_boot_id'], 'SDK_BOOT')
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
    stage = 'preparation-record'
    try:
        value = read(ctx, 'prepare-result', ('owner', 'passed', 'root_identity', 'launcher', 'kernel_sha256', 'runtime_verified'))
        stage = 'kernel-record'
        require(value['runtime_verified'] is False and
                hashlib.sha256(raw_file(ctx['evidence'] / 'sdk-kernel-result.json')).hexdigest() == value['kernel_sha256'],
                'SDK_PREPARE_RESULT')
        if value['passed'] is False and not ctx['root'].exists():
            result.update(passed=True, state='failed_preparation_namespace_absent', SDK_removed_here=False)
        else:
            stage = 'root-owner'
            owned_prepared(ctx)
            previous = None
            stage = 'original-pid-record'
            if (ctx['evidence'] / 'sdk-launch-observed.json').exists():
                previous = read(ctx, 'launch-observed', ('owner', 'pid', 'uid', 'start_ticks', 'runtime_verified'))
                require(type(previous['pid']) is int and 1 <= previous['pid'] <= 4194304 and
                        previous['uid'] == ctx['owner']['uid'] and type(previous['start_ticks']) is int and
                        previous['start_ticks'] > 0 and previous['runtime_verified'] is False, 'SDK_PROC_IDENTITY')
            deadline = time.monotonic() + 10
            last_clock = deadline - 10
            while True:
                stage = 'wait-clock'
                now = time.monotonic()
                require(now >= last_clock and now <= deadline, 'SDK_CLEANUP_DEADLINE')
                last_clock = now
                stage = 'host-boot'
                require(boot() == ctx['owner']['host_boot_id'], 'SDK_BOOT')
                stage = 'original-pid'
                current = proc(previous['pid']) if previous else None
                if current is not None:
                    require(current['uid'] == previous['uid'] and current['start_ticks'] == previous['start_ticks'],
                            'SDK_ORIGINAL_PID_REUSED')
                stage = 'sdk-processes'
                found = sdk_processes(ctx)
                if not found and (current is None or current['state'] == 'Z'):
                    stage = 'adb-absence'
                    result['ADB_read'] = adb_absent(ctx)
                    stage = 'final-root-owner'
                    owned_prepared(ctx)
                    stage = 'final-original-pid'
                    final_pid = proc(previous['pid']) if previous else None
                    require(final_pid is None or (final_pid['uid'] == previous['uid'] and
                            final_pid['start_ticks'] == previous['start_ticks'] and final_pid['state'] == 'Z'),
                            'SDK_ORIGINAL_PID_REUSED')
                    stage = 'final-processes-and-boot'
                    require(not sdk_processes(ctx) and boot() == ctx['owner']['host_boot_id'], 'SDK_SDK_PROCESS_PRESENT')
                    stage = 'final-owner'
                    root_owner(ctx, value)
                    stage = 'final-clock'
                    require(last_clock <= time.monotonic() <= deadline, 'SDK_CLEANUP_DEADLINE')
                    stage = 'remove-owned-runtime'
                    shutil.rmtree(ctx['root'])
                    result.update(passed=True, state='SDK_removed_after_readonly_AVD_and_host_process_absence',
                                  SDK_removed_here=True, owned_SDK_executables_absent=True,
                                  original_host_PID_absent_or_exited=previous is not None)
                    break
                time.sleep(0.1)
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in SAFE else 'SDK_CLEANUP_UNAVAILABLE'
        result['failure_stage'] = stage
        result['error_kind'] = {PermissionError: 'permission_denied', FileNotFoundError: 'file_missing',
            FileExistsError: 'file_exists', subprocess.TimeoutExpired: 'timeout', OSError: 'io',
            json.JSONDecodeError: 'schema', UnicodeDecodeError: 'schema', kernel.ArchiveError: 'sequence_guard'}.get(
                type(error), 'unclassified')
        result.setdefault('SDK_removed_here', False)
    emit(ctx, 'cleanup-result', result)
    return result


def main(argv=None):
    actions = {'prepare': prepare, 'select-launcher': select_launcher, 'probe-version-and-accel': probe,
               'observe-launch': observe, 'cleanup': cleanup, 'install-image': install_image,
               'image-root': image_root, 'bind-avd': bind_avd,
               'create-avd': create_avd, 'prepare-layout': prepare_layout}
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
