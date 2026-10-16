"""Run the exact CI APK on an exclusive hosted emulator, preserving real UI and file evidence.

This is intentionally unavailable on developer machines and physical devices. A test-only
Android InputConnection drives actual editor text and composition, not a commercial IME.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from urllib.parse import urlsplit

APP = 'cc.kronecker.opennexus.dev'
DRIVER = 'cc.kronecker.opennexus.testdriver'
BODY = '# 原始 Markdown 😀\n\n离线正文 / original content\n'
DRAFT = BODY + '未保存的恢复草稿 / recovered draft\n'
NODE = '中文画布节点 😀 / original canvas text'
IME_TEXT = '中文组合输入 😀'
BACK_DRAFT = '# 系统返回键草稿 😀\nBack keeps the native draft before leaving.\n'
IMAGE_BODY = '# Original picker image\n'
RICH_TEXT = '共享富文本 😀 / native rich writing'
RICH_IMAGE_TEXT = 'Original rich picker image'
# Owned deterministic 2x2 RGBA PNG. No user gallery data or storage permission.
IMAGE_BYTES = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAG0lEQVR4nGN45JD0X2l5238G8/TL/78f8/gPAFY9Ckass/NvAAAAAElFTkSuQmCC')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def validate_binding(bundle, commit):
    proof = json.loads((bundle / 'package-verification.json').read_text('utf-8'))
    source = json.loads((bundle / 'source-provenance.json').read_text('utf-8'))
    apk = bundle / 'OpenNexus-android-debug.apk'
    require(proof.get('passed') is True and proof.get('source_clean') is True
        and proof.get('source_commit') == source.get('source_commit') == commit, 'Fixed APK source mismatch')
    require(proof.get('package') == APP and proof.get('debug_signing') is True, 'Unexpected runtime package')
    require(apk.is_file() and not apk.is_symlink() and digest(apk.read_bytes()) == proof.get('sha256'), 'APK bytes changed')
    return apk, proof


def validate_build_job(run, jobs, commit):
    require(run.get('head_sha') == commit and run.get('path') == '.github/workflows/android.yml', 'APK build run source mismatch')
    builds = [job for job in jobs if job.get('name') == 'debug-apk']
    require(len(builds) == 1 and builds[0].get('status') == 'completed'
        and builds[0].get('conclusion') == 'success', 'APK build job has no successful terminal result')
    return {'run_id': run['id'], 'job_id': builds[0]['id'], 'head_sha': commit, 'head_branch': run['head_branch']}


def build_job_origin(run_id, commit):
    require(re.fullmatch(r'[1-9][0-9]{0,17}', run_id), 'Invalid APK build run id')
    repository = os.environ['GITHUB_REPOSITORY']
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'Invalid CI repository')
    def read(path):
        connection = http.client.HTTPSConnection('api.github.com', timeout=30)
        try:
            connection.request('GET', path, headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                'Accept': 'application/vnd.github+json', 'User-Agent': 'OpenNexus-fixed-runtime-origin'})
            response = connection.getresponse()
            require(response.status == 200, 'Cannot verify the selected APK build run')
            data = response.read(4 * 1024 * 1024 + 1)
            require(len(data) <= 4 * 1024 * 1024, 'Oversized CI build metadata')
            return json.loads(data)
        finally:
            connection.close()
    base = f'/repos/{repository}/actions/runs/{run_id}'
    return validate_build_job(read(base), read(base + '/jobs?per_page=100')['jobs'], commit)


def require_hosted_emulator(serial):
    require(sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'Runtime probe requires an isolated GitHub-hosted Linux runner')
    require(serial == 'emulator-5562', 'Refusing an unowned emulator or physical device')


def stop_owned_emulator(adb, serial, avd):
    require_hosted_emulator(serial)
    def command(*args):
        return subprocess.run([str(adb), '-s', serial, *args], capture_output=True, timeout=20)
    inventory = subprocess.run([str(adb), 'devices'], capture_output=True, timeout=20)
    require(inventory.returncode == 0, 'Device inventory is unknown; no process was stopped')
    states = [parts[1] for line in inventory.stdout.decode('utf-8', 'replace').splitlines()
        if len(parts := line.split()) >= 2 and parts[0] == serial]
    if not states:
        # Absence from ADB is not a claim that an unresponsive process has exited.
        return {'state': 'not_connected', 'stop_requested': False}
    require(states == ['device'], 'Emulator state is unknown; no guessed process was stopped')
    observed = command('emu', 'avd', 'name')
    require(observed.returncode == 0, 'Emulator identity is unknown; no guessed process was stopped')
    names = observed.stdout.decode('utf-8', 'replace').splitlines()
    require(names and names[0].strip() == avd, 'AVD ownership mismatch; no process was stopped')
    stopped = command('emu', 'kill')
    require(stopped.returncode == 0, 'Owned emulator stop request failed')
    return {'state': 'stop_requested', 'stop_requested': True, 'avd': avd}


class Probe:
    def __init__(self, args, apk, binding):
        self.args, self.apk, self.binding = args, apk, binding
        self.output = args.output.resolve()
        require(not self.output.exists(), 'Runtime evidence directory already exists')
        self.output.mkdir(parents=True)
        self.tools = self.output.parent / 'driver-build'
        self.tools.mkdir()
        self.adb = args.sdk / 'platform-tools/adb'
        self.root = None
        self.phases = []
        self.original_ime = None
        self.original_hardware_ime = None

    def command(self, command, *, timeout=90, input=None, check=True):
        result = subprocess.run([str(value) for value in command], input=input, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {command[:5]}\n{result.stderr.decode("utf-8", "replace")}')
        return result

    def device(self, *arguments, **kwargs):
        return self.command([self.adb, '-s', self.args.serial, *arguments], **kwargs)

    def shell(self, *arguments, **kwargs):
        return self.device('shell', shlex.join(arguments), **kwargs)

    def private(self, path, *, check=True):
        require(path and '..' not in path.split('/') and not path.startswith('/'), 'Unsafe private test path')
        return self.device('exec-out', 'run-as', APP, 'cat', path, check=check)

    def force_stop(self):
        self.shell('am', 'force-stop', APP)

    def wait_owned_connection(self):
        require_hosted_emulator(self.args.serial)
        expected = f'opennexus-ci-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                state = self.device('get-state', check=False, timeout=5)
                if state.returncode == 0 and state.stdout.strip() == b'device':
                    identity = self.device('emu', 'avd', 'name', check=False, timeout=5)
                    if identity.returncode == 0:
                        names = identity.stdout.decode('utf-8', 'replace').splitlines()
                        require(names and names[0].strip() == expected, 'Reconnected AVD ownership mismatch')
                        return
            except subprocess.TimeoutExpired:
                pass  # Observation expiry never restarts or reinstalls the existing device/app.
            time.sleep(1)
        raise RuntimeError('Original owned emulator did not reconnect; no restart was attempted')

    def verify_installed(self, installed_path):
        expected = self.binding['sha256']
        observations = {'expected_sha256': expected, 'expected_size': self.apk.stat().st_size, 'attempts': []}
        def preserve():
            (self.output / 'installed-apk.json').write_text(json.dumps(observations, indent=2) + '\n', 'utf-8')
        def device_hash():
            try:
                result = self.shell('sha256sum', installed_path, check=False, timeout=10)
            except subprocess.TimeoutExpired:
                return None
            fields = result.stdout.decode('utf-8', 'replace').split()
            return fields[0] if result.returncode == 0 and fields and re.fullmatch(r'[0-9a-f]{64}', fields[0]) else None
        preserve()
        for attempt in range(1, 4):
            self.wait_owned_connection()
            entry = {'attempt': attempt, 'verified': False}
            observations['attempts'].append(entry); preserve()
            try:
                path = self.shell('pm', 'path', APP, check=False, timeout=10)
            except subprocess.TimeoutExpired:
                entry['package_observation'] = 'timeout'; preserve(); continue
            if path.returncode != 0:
                entry['package_observation'] = 'unavailable'; preserve(); continue
            current_path = path.stdout.decode().strip().removeprefix('package:')
            require(current_path == installed_path, 'Installed package location changed during observation')
            entry['device_sha256_before'] = device_hash(); preserve()
            if entry['device_sha256_before'] is None:
                continue  # Re-observe the same identity after a transient ADB loss; no device write.
            require(entry['device_sha256_before'] == expected, 'Installed device APK hash differs from the fixed build')
            copy = self.tools / f'installed-observation-{attempt}.apk'
            require(not copy.exists(), 'An earlier installed-byte observation would be replaced')
            try:
                result = self.device('pull', installed_path, copy, check=False, timeout=60)
                entry['pull_returncode'] = result.returncode
            except subprocess.TimeoutExpired:
                entry['pull_returncode'] = 'observation_timeout'
            if copy.is_file() and not copy.is_symlink():
                entry['read_size'] = copy.stat().st_size
                with copy.open('rb') as file:
                    entry['read_sha256'] = hashlib.file_digest(file, 'sha256').hexdigest()
            entry['device_sha256_after'] = device_hash()
            entry['verified'] = (entry['pull_returncode'] == 0 and entry.get('read_size') == observations['expected_size']
                and entry.get('read_sha256') == entry['device_sha256_after'] == expected)
            preserve()
            if entry['verified']:
                return
            if entry['device_sha256_after'] is not None:
                require(entry['device_sha256_after'] == expected, 'Device APK changed during the original read')
        raise RuntimeError('Installed APK bytes could not be confirmed; preserved all original read observations')

    def boot(self):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                if self.device('get-state', check=False, timeout=10).stdout.strip() == b'device':
                    if self.shell('getprop', 'sys.boot_completed', check=False, timeout=10).stdout.strip() == b'1':
                        break
            except subprocess.TimeoutExpired:
                pass  # An observation timeout does not restart the still-owned emulator.
            time.sleep(2)
        else:
            raise RuntimeError('Owned emulator did not boot')
        require(self.shell('getprop', 'ro.kernel.qemu').stdout.strip() == b'1', 'Target is not an emulator')
        require(self.shell('getprop', 'ro.build.version.sdk').stdout.strip() == b'36', 'Unexpected emulator API')
        require(self.device('emu', 'avd', 'name').stdout.decode().splitlines()[0]
            == f'opennexus-ci-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}', 'AVD ownership mismatch')
        installed = self.shell('pm', 'list', 'packages', APP).stdout.decode().splitlines()
        require(f'package:{APP}' not in installed, 'Target data already exists; refusing to clear or replace it')
        for setting in ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale'):
            self.shell('settings', 'put', 'global', setting, '0')
        self.shell('input', 'keyevent', 'KEYCODE_WAKEUP')
        self.shell('wm', 'dismiss-keyguard')
        self.device('install', '--no-streaming', self.apk)
        installed_path = self.shell('pm', 'path', APP).stdout.decode().strip().removeprefix('package:')
        require(installed_path.startswith('/data/app/') and '\n' not in installed_path, 'Unexpected installed APK location')
        self.verify_installed(installed_path)
        self.activity = self.shell('cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN',
            '-c', 'android.intent.category.LAUNCHER', APP).stdout.decode().strip().splitlines()[-1]
        require(self.activity.startswith(APP + '/') and re.fullmatch(r'[a-zA-Z0-9_./]+', self.activity), 'Unbound target activity')
        self.build_driver()

    def build_driver(self):
        root = Path(__file__).resolve().parent / 'android-runtime'
        sdk_tools = self.args.sdk / 'build-tools/36.0.0'
        platform = self.args.sdk / 'platforms/android-36/android.jar'
        classes, dex = self.tools / 'classes', self.tools / 'dex'
        classes.mkdir(); dex.mkdir()
        self.command(['javac', '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-classpath', platform,
            '-d', classes, *sorted(root.glob('*.java'))])
        self.command([sdk_tools / 'd8', '--lib', platform, '--min-api', '24', '--output', dex,
            *sorted(classes.rglob('*.class'))])
        unsigned, aligned, signed = [self.tools / name for name in ('unsigned.apk', 'aligned.apk', 'driver.apk')]
        resources = self.tools / 'resources.zip'
        self.command([sdk_tools / 'aapt2', 'compile', '--dir', root / 'res', '-o', resources])
        self.command([sdk_tools / 'aapt2', 'link', '-I', platform, '--manifest', root / 'AndroidManifest.xml',
            '-R', resources, '--version-code', '1', '--version-name', 'ci', '-o', unsigned])
        with zipfile.ZipFile(unsigned, 'a') as archive:
            archive.write(dex / 'classes.dex', 'classes.dex')
        self.command([sdk_tools / 'zipalign', '-f', '4', unsigned, aligned])
        key = self.tools / 'ephemeral-test-driver.p12'
        self.command(['keytool', '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
            '-alias', 'ci-driver', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '30', '-dname', 'CN=OpenNexus CI test driver'])
        self.command([sdk_tools / 'apksigner', 'sign', '--ks', key, '--ks-pass', 'pass:android',
            '--key-pass', 'pass:android', '--out', signed, aligned])
        self.device('install', '--no-streaming', signed)

    def enable_test_ime(self):
        require_hosted_emulator(self.args.serial)
        original = self.shell('settings', 'get', 'secure', 'default_input_method').stdout.decode().strip()
        require(re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+', original), 'Original hosted-emulator IME cannot be identified')
        require(original != DRIVER + '/.CompositionIme', 'Test IME already selected before this owned run')
        hardware = self.shell('settings', 'get', 'secure', 'show_ime_with_hard_keyboard').stdout.decode().strip()
        require(hardware in ('0', '1', 'null'), 'Original hosted-emulator hardware-keyboard preference is unknown')
        self.original_ime = original
        self.original_hardware_ime = hardware
        self.shell('ime', 'enable', DRIVER + '/.CompositionIme')
        self.shell('ime', 'set', DRIVER + '/.CompositionIme')
        self.shell('settings', 'put', 'secure', 'show_ime_with_hard_keyboard', '1')

    def restore_test_ime(self):
        if not self.original_ime:
            return
        require_hosted_emulator(self.args.serial)
        observed = self.shell('settings', 'get', 'secure', 'default_input_method', check=False).stdout.decode().strip()
        if observed == DRIVER + '/.CompositionIme':
            self.shell('ime', 'set', self.original_ime)
            self.shell('ime', 'disable', DRIVER + '/.CompositionIme')
            if self.original_hardware_ime == 'null':
                self.shell('settings', 'delete', 'secure', 'show_ime_with_hard_keyboard')
            elif self.original_hardware_ime in ('0', '1'):
                self.shell('settings', 'put', 'secure', 'show_ime_with_hard_keyboard', self.original_hardware_ime)
        self.original_ime = None
        self.original_hardware_ime = None

    def capture(self, phase):
        image = self.device('exec-out', 'screencap', '-p', check=False).stdout
        if image.startswith(b'\x89PNG\r\n\x1a\n'):
            (self.output / f'{phase}.png').write_bytes(image)
        self.shell('uiautomator', 'dump', '/sdcard/opennexus-ci-window.xml', check=False, timeout=25)
        tree = self.device('exec-out', 'cat', '/sdcard/opennexus-ci-window.xml', check=False).stdout
        if tree.startswith(b'<?xml'):
            (self.output / f'{phase}.xml').write_bytes(tree)
        if phase.startswith('theme-'):
            (self.output / f'{phase}-window.txt').write_bytes(self.shell('dumpsys', 'window', 'windows', check=False).stdout)
            selected = {'theme-dark': 'Dark', 'theme-light': 'Light', 'theme-sepia': 'Sepia',
                'theme-system': 'System', 'theme-diagnostic-dark': 'Dark', 'theme-diagnostic-reset': 'System'}.get(phase)
            if selected is None:
                return
            filename = f'theme-cards-{selected}.png'
            popup = self.device('exec-out', 'run-as', DRIVER, 'cat', 'files/' + filename, check=False).stdout
            if popup.startswith(b'\x89PNG\r\n\x1a\n'):
                (self.output / filename).write_bytes(popup)

    def phase(self, phase):
        self.force_stop()
        start = time.monotonic()
        self.shell('am', 'start', '-W', '-n', self.activity)
        result = self.shell('am', 'instrument', '-w', '-e', 'phase', phase, f'{DRIVER}/.RuntimeProbe', timeout=180)
        text = result.stdout.decode('utf-8', 'replace')
        (self.output / f'{phase}.log').write_text(text, 'utf-8')
        self.capture(phase)
        require(f'OPENNEXUS_RUNTIME_PHASE_OK {phase}' in text and 'OPENNEXUS_RUNTIME_FAILED' not in text,
            f'Actual Android UI phase failed: {phase}; see preserved UI log and screenshot')
        self.phases.append({'phase': phase, 'duration_ms': round((time.monotonic() - start) * 1000), 'passed': True})
        if phase == 'theme-diagnostic-dark':
            self.read_native_styles(phase)
        self.force_stop()
        if self.root is None and not phase.startswith('theme-'):
            paths = self.shell('run-as', APP, 'find', '.', '-name', 'mobile-drafts.sqlite3', '-type', 'f').stdout.decode().splitlines()
            require(len(paths) == 1, 'Native draft database origin is ambiguous')
            self.root = paths[0].removesuffix('mobile-drafts.sqlite3')

    def read_native_styles(self, phase):
        require_hosted_emulator(self.args.serial)
        self.wait_owned_connection()
        port = None
        report = {'scope': 'read-only owned debug WebView, no DOM/credential/file mutation'}
        try:
            process = self.shell('pidof', APP).stdout.decode().strip()
            require(re.fullmatch(r'[1-9][0-9]{0,9}', process), 'Owned app process is ambiguous')
            port = self.device('forward', 'tcp:0', 'localabstract:webview_devtools_remote_' + process).stdout.decode().strip()
            require(re.fullmatch(r'[0-9]{4,5}', port) and 1024 <= int(port) <= 65535, 'Unexpected owned forwarding port')
            connection = http.client.HTTPConnection('127.0.0.1', int(port), timeout=10)
            try:
                connection.request('GET', '/json/list')
                response = connection.getresponse(); data = response.read(1024*1024+1)
                require(response.status == 200 and len(data) <= 1024*1024, 'Cannot inspect owned WebView metadata')
                pages = [item for item in json.loads(data) if item.get('type') == 'page' and item.get('title') == 'OpenNexus']
            finally:
                connection.close()
            require(len(pages) == 1, 'Owned WebView page is ambiguous')
            socket = urlsplit(pages[0]['webSocketDebuggerUrl'])
            require(socket.scheme == 'ws' and socket.hostname in ('127.0.0.1', 'localhost') and socket.port == int(port)
                and re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+', socket.path) and not socket.query and not socket.fragment,
                'Refusing an unrelated page endpoint')
            script = Path(__file__).resolve().parent/'android-runtime/read_webview_styles.mjs'
            observed = self.command(['node', script, 'ws://127.0.0.1:' + port + socket.path], timeout=20)
            report.update(available=True, styles=json.loads(observed.stdout))
        except Exception as error:
            report.update(available=False, failure=type(error).__name__, reason=str(error)[:400])
        finally:
            if port and re.fullmatch(r'[0-9]{4,5}', port):
                report['own_forward_removed'] = self.device('forward', '--remove', 'tcp:' + port, check=False).returncode == 0
            (self.output/(phase+'-computed-styles.json')).write_text(json.dumps(report,indent=2)+'\n','utf-8')

    def original(self, name):
        require(name and not name.startswith('/') and '\\' not in name and ':' not in name
            and all(part not in ('', '.', '..') for part in name.split('/')), 'Unsafe original observation name')
        value = self.private(self.root + 'vault/' + name).stdout
        ordinal = getattr(self, 'original_observations', 0) + 1
        directory = self.output / 'original-observations' / f'{ordinal:03d}'
        directory.mkdir(parents=True, exist_ok=False)
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        self.original_observations = ordinal
        return value

    def database_rows(self, query):
        snapshot = Path(tempfile.mkdtemp(prefix='draft-snapshot-', dir=self.tools))
        database = snapshot / 'mobile-drafts.sqlite3'
        for suffix in ('', '-wal', '-shm'):
            value = self.private(self.root + 'mobile-drafts.sqlite3' + suffix, check=False)
            if value.returncode == 0:
                Path(str(database) + suffix).write_bytes(value.stdout)
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'Draft database integrity check failed')
            return db.execute(query).fetchall()

    def draft_rows(self):
        return self.database_rows('SELECT path,content,generation,saved_generation FROM drafts ORDER BY path')

    def stage_original_image(self):
        require_hosted_emulator(self.args.serial)
        destination = '/sdcard/Download/opennexus-ci-original.png'
        require(self.shell('test', '!', '-e', destination, check=False).returncode == 0, 'Owned picker original already exists; refusing to replace it')
        source = self.tools / 'original-picker-image.png'
        require(not source.exists(), 'Picker source already exists')
        source.write_bytes(IMAGE_BYTES)
        self.device('push', source, destination)
        device_hash = self.shell('sha256sum', destination).stdout.decode().split()[0]
        require(device_hash == digest(IMAGE_BYTES), 'Staged system picker original hash mismatch')
        require(self.device('exec-out', 'cat', destination).stdout == IMAGE_BYTES, 'System picker source bytes changed')
        (self.output / 'picker-original.png').write_bytes(IMAGE_BYTES)
        (self.output / 'picker-original.json').write_text(json.dumps({'origin': 'synthetic owned RGBA fixture in this exclusive hosted emulator',
            'path': destination, 'sha256': digest(IMAGE_BYTES), 'size': len(IMAGE_BYTES), 'permissions_added': []}, indent=2) + '\n', 'utf-8')

    def verify_original_images(self):
        asset = f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
        require(self.original(asset) == IMAGE_BYTES, 'System-selected image was re-encoded or replaced')
        markdown = self.original('runtime-image.md')
        reference = ('![](' + asset + ')').encode()
        require(markdown.count(reference) == 1 and markdown.replace(reference, b'') == IMAGE_BODY.encode(), 'Image insertion changed the original Markdown or duplicated its reference')
        rich = self.original('runtime-rich-image.md')
        require(rich.count(reference) == 1 and rich.replace(reference,b'') == (RICH_IMAGE_TEXT + '\n').encode(), 'Rich image changed original text or duplicated its reference')
        canvas = self.original('runtime-image.canvas')
        nodes = json.loads(canvas)['nodes']
        require(len(nodes) == 1 and nodes[0]['type'] == 'file' and nodes[0]['file'] == asset, 'Canvas image node differs from the chosen original')
        rows = self.database_rows('SELECT operation_id,path,asset_path,hash,size,state FROM image_intents ORDER BY path')
        require(len(rows) == 3 and len({row[0] for row in rows}) == 3
            and {row[1] for row in rows} == {'runtime-image.md', 'runtime-rich-image.md', 'runtime-image.canvas'}
            and all(row[2:] == (asset, digest(IMAGE_BYTES), len(IMAGE_BYTES), 'committed') for row in rows),
            'Exactly one committed original image receipt per target is required')
        receipts = [dict(zip(('operation_id', 'path', 'asset_path', 'sha256', 'size', 'state'), row)) for row in rows]
        (self.output / 'image-import-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n', 'utf-8')
        return {'runtime-image.md': markdown, 'runtime-rich-image.md': rich, 'runtime-image.canvas': canvas, asset: IMAGE_BYTES}

    def verify_export(self, originals):
        paths = self.shell('run-as', APP, 'find', self.root + 'exports', '-name', '*.delivery.json', '-type', 'f').stdout.decode().splitlines()
        require(len(paths) == 1, 'Exactly one real SAF delivery is required')
        delivery = json.loads(self.private(paths[0]).stdout)
        require(delivery.get('state') == 'saved' and delivery.get('verified_at'), 'SAF destination bytes were not verified')
        ticket = delivery['ticket']
        require(re.fullmatch(r'[0-9a-f-]{36}', ticket), 'Unexpected export ticket')
        content = self.private(self.root + f'exports/{ticket}.zip').stdout
        require(digest(content) == delivery['sha256'] and len(content) == delivery['size'], 'Original export bytes mismatch')
        filename = f'OpenNexus-content-{ticket}.zip'
        destinations = self.shell('find', '/sdcard/Download', '/sdcard/Documents', '-maxdepth', '2', '-name', filename, '-type', 'f', check=False).stdout.decode().splitlines()
        require(len(destinations) == 1 and destinations[0].startswith(('/sdcard/Download/', '/sdcard/Documents/')), 'Owned SAF destination was not independently located')
        actual = self.device('exec-out', 'cat', destinations[0]).stdout
        require(actual == content, 'System-picked file differs from native frozen export')
        with zipfile.ZipFile(io.BytesIO(actual)) as archive:
            for name, expected in originals.items():
                require(archive.read('vault/' + name) == expected, 'Exported content changed: ' + name)
            require(not any(name.endswith(('.sqlite3', '.db')) for name in archive.namelist()), 'Private database leaked into content export')
            require('vault/runtime-back.md' not in archive.namelist(), 'Unconfirmed Back draft leaked into saved content export')
        (self.output / 'system-saved-content.zip').write_bytes(actual)
        return {key: delivery[key] for key in ('ticket', 'delivery_id', 'state', 'sha256', 'size', 'verified_at')}

    def run(self):
        self.boot()
        if self.args.scope == 'all':
            for theme in ('dark', 'light', 'sepia', 'system'):
                self.phase('theme-' + theme)
        else:
            self.phase('theme-diagnostic-dark')
            self.phase('theme-diagnostic-reset')
        self.enable_test_ime()
        self.phase('markdown')
        require(self.original('runtime-note.md') == BODY.encode(), 'Saved Markdown bytes differ from actual UI text')
        self.phase('rich-markdown')
        rich = self.original('runtime-rich.md')
        require(rich == ('**' + RICH_TEXT + '**\n').encode(), 'Shared rich toolbar did not save the actual selected bold text')
        self.phase('rich-reopen')
        require(self.original('runtime-rich.md') == rich, 'Cold reopen changed the actual rich Markdown bytes')
        self.phase('draft')
        require(self.original('runtime-note.md') == BODY.encode(), 'Unconfirmed draft replaced saved Markdown')
        rows = self.draft_rows()
        require(any(path == 'runtime-note.md' and content == DRAFT and generation > saved for path, content, generation, saved in rows), 'Confirmed local draft missing after force-stop')
        self.phase('recover')
        require(self.original('runtime-note.md') == DRAFT.encode(), 'Recovered and saved Markdown bytes mismatch')
        self.phase('ime-compose')
        require(self.original('runtime-note.md') == (DRAFT + IME_TEXT).encode(), 'Confirmed Chinese IME input changed original Markdown bytes')
        self.phase('ime-recover')
        require(self.original('runtime-note.md') == (DRAFT + IME_TEXT).encode(), 'Reopening changed confirmed Chinese IME input')
        self.phase('back-draft')
        require(any(path == 'runtime-back.md' and content == BACK_DRAFT and generation > saved
            for path, content, generation, saved in self.draft_rows()), 'System Back left before the original draft was durable')
        require(self.private(self.root + 'vault/runtime-back.md', check=False).returncode != 0, 'System Back implicitly saved an unconfirmed note')
        self.phase('back-recover')
        require(any(path == 'runtime-back.md' and content == BACK_DRAFT and generation > saved
            for path, content, generation, saved in self.draft_rows()), 'Backgrounding changed the original Back draft')
        self.phase('canvas')
        before = self.original('runtime.canvas')
        document = json.loads(before)
        require(len(document['nodes']) == 1 and document['nodes'][0]['text'] == NODE, 'Actual Canvas node text mismatch')
        self.phase('canvas-cancel')
        require(self.original('runtime.canvas') == before, 'Cancelled real touch changed saved Canvas')
        self.phase('canvas-drag')
        after = self.original('runtime.canvas')
        moved = json.loads(after)
        require(len(moved['nodes']) == 1 and moved['nodes'][0]['id'] == document['nodes'][0]['id']
            and moved['nodes'][0]['text'] == NODE, 'Drag changed node identity or original text')
        require((moved['nodes'][0]['x'], moved['nodes'][0]['y']) != (document['nodes'][0]['x'], document['nodes'][0]['y']), 'Real touch did not move the Canvas node')
        self.stage_original_image()
        self.phase('markdown-image')
        self.phase('markdown-image-reopen')
        self.phase('rich-image')
        self.phase('rich-image-reopen')
        self.phase('canvas-image')
        self.phase('canvas-image-reopen')
        images = self.verify_original_images()
        self.phase('export')
        originals = {'runtime-rich.md': rich, 'runtime-note.md': (DRAFT + IME_TEXT).encode(), 'runtime.canvas': after, **images}
        export = self.verify_export(originals)
        for name, data in originals.items():
            path = self.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        proof = {'schema': 1, 'passed': True, 'at': datetime.now(timezone.utc).isoformat(),
            'source_commit': self.args.commit, 'runtime_tools_commit': self.args.tools_commit,
            'compiled_from': self.args.build_origin, 'package': APP, 'apk_sha256': self.binding['sha256'],
            'signer_certificate_sha256': self.binding['signer_certificate_sha256'],
            'device': {'kind': 'github-hosted-android-emulator', 'api': 36, 'abi': 'x86_64', 'serial': self.args.serial},
            'phases': self.phases, 'files_sha256': {name: digest(data) for name, data in originals.items()},
            'scope': self.args.scope, 'theme_choices_verified': self.args.scope == 'all',
            'native_theme_contrast_verified': False,
            'theme_style_diagnostic': 'theme-diagnostic-dark-computed-styles.json' if self.args.scope == 'lifecycle' else None,
            'theme_ui': 'Actual shared desktop theme card choices and cold reloads; rendered card screenshots/window observations retained for appearance review' if self.args.scope == 'all' else 'Not repeated in lifecycle scope; theme acceptance requires independent same-APK evidence',
            'system_destination': export, 'draft_database_integrity': 'ok',
            'input_method': 'Real Android InputMethodService/InputConnection for source/rich body text, pinyin, Chinese composing text and candidate confirmation; accessibility only for ordinary path/property fields. Test-only IME, not Gboard/Sogou or physical-device proof.',
            'system_back': 'Actual Android Back key; durable draft recovery and own-task background; not predictive gesture animation proof',
            'physical_phone_verified': False, 'Chinese_IME_verified': True, 'commercial_Chinese_IME_verified': False, 'sync_verified': False,
            'disk_full_verified': False, 'shared_rich_writing_verified': True, 'image_picker_verified': True,
            'image_picker_scope': 'Real system document picker selects an owned synthetic PNG; original bytes, three native receipts for source/rich Markdown and Canvas, cold reopen and exported bytes verified. Not camera or user-gallery acceptance.',
            'published': False}
        (self.output / 'runtime-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        print(json.dumps(proof, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--commit')
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--serial', default='emulator-5562')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stop-owned', action='store_true')
    parser.add_argument('--source-checkout', type=Path)
    parser.add_argument('--tools-commit')
    parser.add_argument('--build-run-id')
    parser.add_argument('--scope', choices=('all', 'lifecycle'), default='all')
    args = parser.parse_args()
    require_hosted_emulator(args.serial)
    if args.stop_owned:
        expected = f'opennexus-ci-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}'
        print(json.dumps(stop_owned_emulator(args.sdk / 'platform-tools/adb', args.serial, expected)))
        return
    require(args.bundle and args.commit and args.output, 'Runtime needs a fixed bundle, commit and exclusive evidence output')
    require(re.fullmatch(r'[0-9a-f]{40}', args.commit), 'A full immutable APK source commit is required')
    apk, binding = validate_binding(args.bundle, args.commit)
    from release_plan import build_source
    tools_root = Path(__file__).resolve().parents[1]
    args.tools_commit = args.tools_commit or args.commit
    build_source(tools_root, args.tools_commit)
    compiled_checkout = args.source_checkout.resolve() if args.source_checkout else tools_root
    require(compiled_checkout.is_relative_to(Path(os.environ['GITHUB_WORKSPACE']).resolve()), 'Compiled checkout leaves the owned CI workspace')
    require(json.loads((args.bundle / 'source-provenance.json').read_text('utf-8'))
        == build_source(compiled_checkout, args.commit), 'Runtime checkout differs from compiled APK inputs')
    args.build_origin = build_job_origin(args.build_run_id or os.environ['GITHUB_RUN_ID'], args.commit)
    probe = Probe(args, apk, binding)
    try:
        probe.run()
    finally:
        try:
            probe.restore_test_ime()
        finally:
            log = probe.device('logcat', '-d', '-t', '1500', check=False).stdout
            (probe.output / 'logcat.txt').write_bytes(log)
            probe.shell('am', 'force-stop', APP, check=False)


if __name__ == '__main__':
    main()
