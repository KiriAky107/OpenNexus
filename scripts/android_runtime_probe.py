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
TEST_IME = 'cc.kronecker.opennexus.testime'
TEST_IME_COMPONENT = TEST_IME + '/.CompositionInputMethod'
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


def native_process_lifetime(pid, stat, cmdline, uid, status):
    require(type(pid) is int and 0 < pid < 2**31 and type(stat) is bytes and len(stat) < 4096,
        'Native TLS process identity is invalid')
    prefix = APP.encode() + b'\0'
    require(stat.startswith(str(pid).encode() + b' (') and b') ' in stat
        and type(cmdline) is bytes and len(prefix) <= len(cmdline) < 16384
        and cmdline.startswith(prefix) and not cmdline[len(prefix):].strip(b'\0')
        and type(uid) is bytes and re.fullmatch(rb'[1-9][0-9]{4}\n?', uid),
        'Native TLS process identity is not the owned application')
    require(type(status) is bytes and len(status) < 16384, 'Native TLS process status is invalid')
    tgids = [line.split()[1:] for line in status.splitlines() if line.startswith(b'Tgid:')]
    uids = [line.split()[1:] for line in status.splitlines() if line.startswith(b'Uid:')]
    require(tgids == [[str(pid).encode()]] and uids == [[uid.strip()] * 4],
        'Native TLS process owner or thread group changed')
    fields = stat.rsplit(b') ', 1)[1].split()
    require(len(fields) >= 20 and fields[19].isdigit() and 0 < int(fields[19]) < 2**64,
        'Native TLS process start time is invalid')
    return {'pid': pid, 'start_ticks': int(fields[19]), 'uid': int(uid), 'package': APP}


def validate_image_save_observation(report, rendered, phase):
    require(phase in ('rich-image', 'canvas-image'), 'Unexpected image/save observation phase')
    require(len(report['drafts']) == 1 and not report['saves'] and len(report['images']) == 1,
        'Original image/save rows are missing, duplicated or already have an unknown Save; no input sent')
    draft, image = report['drafts'][0], report['images'][0]
    asset = f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
    require(image['state'] == 'committed' and image['asset_path'] == asset
        and image['hash'] == digest(IMAGE_BYTES) and image['size'] == len(IMAGE_BYTES)
        and image['draft_id'] == draft['id'] and not image.get('error')
        and draft['generation'] == image['generation'] + 1 and draft['saved_generation'] == 0
        and draft['content'] == image['content_after'], 'Original import is not durably acknowledged; no Save sent')
    require(rendered.get('available') is True, 'Actual image/save rendering was not observed; no Save sent')
    state = rendered.get('styles', {}).get('imageSave', {})
    images = state.get('richImages' if phase == 'rich-image' else 'canvasImages', [])
    if phase == 'rich-image':
        # ProseMirror adds a source-less IMG cursor separator after an inline
        # leaf. Classify it from actual observed attributes; never ignore an
        # extra asset, a sourced separator or an unclassified zero-size image.
        separators = [image for image in images if image.get('editorSeparator') is True]
        require(len(separators) <= 1 and all(image.get('hasSource') is False
            and image.get('workspaceAsset') is False and image.get('width') == 0
            and image.get('height') == 0 and not image.get('error') for image in separators),
            'Editor separator contains a source or unexpected image; no Save sent')
        images = [image for image in images if image.get('editorSeparator') is not True]
        require(len(images) == 1 and images[0].get('hasSource') is True and images[0].get('workspaceAsset') is True,
            'Original native workspace image is missing or ambiguous; no Save sent')
    require(len(images) == 1 and images[0].get('complete') is True and images[0].get('width') == 2
        and images[0].get('height') == 2 and images[0].get('visible') is True
        and not images[0].get('error') and state.get('busy') is False,
        'Original 2x2 image has not finished loading/importing; no Save sent')
    saves = state.get('save', [])
    require(len(saves) == 1 and all(saves[0].get(key) is True for key in ('enabled', 'visible', 'hit')),
        'Original Save action is disabled, hidden, covered or ambiguous; no input sent')


def validate_image_save_result(before, after):
    require(len(after['drafts']) == 1 and len(after['saves']) == 1 and after['images'] == before['images'],
        'Save did not preserve the original image receipt or produced no unique original save result')
    draft, save, original = after['drafts'][0], after['saves'][0], before['drafts'][0]
    require(draft['id'] == original['id'] and draft['content'] == original['content']
        and draft['generation'] == original['generation'] == draft['saved_generation']
        and save['draft_id'] == draft['id'] and save['generation'] == draft['generation']
        and save['state'] == 'committed' and not save['error'], 'Original Save is not durably committed; no success claim')


def rich_image_editor_ready(rendered):
    attributes = rendered.get('styles', {}).get('editorAttributes', [])
    return rendered.get('available') is True and rendered.get('styles', {}).get('imageSave', {}).get('richReady') is True \
        and len(attributes) == 1 and attributes[0].get('contentEditable') == 'true' \
        and attributes[0].get('visible') is True and attributes[0].get('readOnly') is False \
        and attributes[0].get('disabled') is False


def validate_tablet_layout(report, portrait):
    require(report.get('available') is True, 'Actual tablet WebView geometry was not observed')
    layout = report.get('styles', {}).get('layout', {})
    expected = 800 if portrait else 1280
    require(abs(layout.get('width', 0) - expected) <= 2 and layout.get('compact') is portrait,
        'Tablet width did not select the actual drawer/column layout')
    require(0 < layout.get('rootScrollWidth', 0) <= layout.get('rootWidth', 0) + 1,
        'Actual tablet root overflows horizontally')
    toolbar, editor, main, files = [layout.get(name) or {} for name in ('toolbar', 'editor', 'main', 'files')]
    require(toolbar.get('visible') is True and editor.get('visible') is True and main.get('visible') is True,
        'Actual tablet editor or expanded formatting panel is missing')
    require(0 <= toolbar.get('left', -1) and toolbar.get('right', expected+10) <= expected + 1
        and 0 < toolbar.get('width', 0) <= 641 and toolbar.get('clientWidth', 0) + 1 >= toolbar.get('scrollWidth', 1)
        and layout.get('floating', {}).get('position') == 'absolute', 'Tablet floating formatting leaves its visible bounds')
    require(files.get('visible') is (not portrait), 'Tablet file drawer/column visibility differs from its actual width')
    if not portrait:
        targets = layout.get('fileTargets', {})
        for group, minimum in (('rows', 1), ('header', 2), ('search', 2)):
            items = targets.get(group)
            require(isinstance(items, list) and minimum <= len(items) <= 50,
                'Actual tablet navigation touch targets were not observed')
            require(all(isinstance(item, dict) and item.get('visible') is True
                and isinstance(item.get('height'), (float, int)) and item['height'] >= 44
                and isinstance(item.get('width'), (float, int)) and item['width'] >= 44 for item in items),
                'Actual tablet navigation targets are smaller than 44 CSS pixels')
    return layout


def validate_canvas_tools(report, *, width=None, expanded=True, menu=False):
    require(report.get('available') is True, 'Actual Canvas WebView geometry was not observed')
    view = report.get('styles', {}).get('canvasTools', {})
    actual = view.get('width', 0)
    require(isinstance(actual, (int, float)) and (320 <= actual <= 640 if width is None else abs(actual-width) <= 2),
        'Actual Canvas viewport does not match the owned display')
    require(0 < view.get('rootScrollWidth', 0) <= view.get('rootWidth', 0) + 1, 'Canvas root overflows horizontally')
    host, trigger, area, popup = [view.get(name) or {} for name in ('host', 'trigger', 'area', 'menu')]
    require(host.get('visible') is True, 'Actual Canvas scene is missing')
    require(trigger.get('visible', False) is (width is None), 'Phone trigger or tablet inline mode is incorrect')
    require(area.get('visible', False) is expanded and popup.get('visible', False) is menu,
        'Canvas outer and nested menu states differ from the original UI action')
    if expanded:
        require((area.get('position') == 'absolute') is (width is None), 'Canvas floating/inline position is incorrect')
    for item in (trigger, area if expanded else {}, popup if menu else {}):
        if not item.get('visible'): continue
        require(0 <= item.get('left', -1) and item.get('right', actual+2) <= actual+1
            and 0 <= item.get('top', -1) and item.get('bottom', view.get('height', 0)+2) <= view.get('height', 0)+1,
            'Visible Canvas controls leave the actual screen')
    targets = view.get('targets')
    require(isinstance(targets, list) and 1 <= len(targets) <= 50 and all(
        item.get('visible') is True and item.get('width', 0) >= 44 and item.get('height', 0) >= 44 for item in targets),
        'Actual Canvas control touch targets are missing or smaller than 44 CSS pixels')
    return view


def validate_binding(bundle, commit):
    proof = json.loads((bundle / 'package-verification.json').read_text('utf-8'))
    source = json.loads((bundle / 'source-provenance.json').read_text('utf-8'))
    apk = bundle / 'OpenNexus-android-debug.apk'
    require(proof.get('passed') is True and proof.get('source_clean') is True
        and proof.get('source_commit') == source.get('source_commit') == commit, 'Fixed APK source mismatch')
    require(proof.get('package') == APP and proof.get('debug_signing') is True, 'Unexpected runtime package')
    require(apk.is_file() and not apk.is_symlink() and digest(apk.read_bytes()) == proof.get('sha256'), 'APK bytes changed')
    if native_tests := proof.get('native_tests'):
        test_apk = bundle / 'OpenNexus-android-tests.apk'
        require(native_tests.get('apk') == test_apk.name and native_tests.get('package') == APP + '.test'
            and native_tests.get('target_package') == APP and native_tests.get('runner') == 'androidx.test.runner.AndroidJUnitRunner'
            and native_tests.get('test_class') == 'cc.kronecker.opennexus.sync.SyncCredentialStoreTest'
            and native_tests.get('expected_tests') in (4, 5)
            and native_tests.get('signer_certificate_sha256') == proof.get('signer_certificate_sha256'), 'Native test binding mismatch')
        require(test_apk.is_file() and not test_apk.is_symlink() and test_apk.stat().st_size == native_tests.get('bytes')
            and digest(test_apk.read_bytes()) == native_tests.get('sha256'), 'Native test APK bytes changed')
        if native_tests['expected_tests'] == 5:
            broker = proof.get('native_secret_broker', {})
            expected = 'Java_cc_kronecker_opennexus_sync_NativeCredentialProbe_roundTrip'
            require(broker.get('debug_test_entry_only') is True and broker.get('WebView_read_command') is False
                and broker.get('native_symbols') == {'arm64-v8a':expected,'x86_64':expected}, 'Native Rust worker binding mismatch')
    if window_tests := proof.get('native_window_insets'):
        require(native_tests and window_tests.get('test_class') == 'cc.kronecker.opennexus.MobileWindowInsetsTest'
            and window_tests.get('expected_tests') == 3
            and window_tests.get('actual_device_tests_passed') is False
            and window_tests.get('actual_keyboard_visibility_verified') is False, 'Window inset test binding mismatch')
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
        self.tls_initializations = []
        self.tls_process_origins = {}
        self.keystore_test_result = None
        self.native_auth_read_result = None

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
        # Shell-v2 without a PTY preserves binary bytes and the remote exit code.
        # exec-out can report success while mixing a failing cat's error into stdout.
        return self.device('shell', '-T', shlex.join(('run-as', APP, 'cat', path)), check=check)

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
        def preserve_read_output(attempt, channel, raw):
            # Retain the actual read error before another observation. APK pull
            # output concerns only this fresh owned installation, never secrets.
            raw = b'' if raw is None else raw
            require(isinstance(raw, bytes), 'Installed-byte read output is not raw bytes')
            target = self.output / f'installed-apk-pull-{attempt}-{channel}.txt'
            require(not target.exists() and not target.is_symlink(), 'Original installed-read output would be replaced')
            prefix = raw[:64 * 1024]
            with target.open('xb') as stream:
                stream.write(prefix); stream.flush(); os.fsync(stream.fileno())
            return {'file': target.name, 'bytes': len(raw), 'sha256': digest(raw),
                'stored_bytes': len(prefix), 'truncated': len(prefix) != len(raw)}
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
                stdout, stderr = result.stdout, result.stderr
            except subprocess.TimeoutExpired as error:
                entry['pull_returncode'] = 'observation_timeout'
                stdout, stderr = error.stdout, error.stderr
            entry['pull_output'] = {channel: preserve_read_output(attempt, channel, raw)
                for channel, raw in (('stdout', stdout), ('stderr', stderr))}
            preserve()
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
        self.native_keystore_tests()
        self.build_driver()

    def native_keystore_tests(self):
        if getattr(self.args,'scope',None) in ('attachments','attachments-remaining','canvas-remaining','editing-remaining','canvas-tools','canvas-tablet','document-limits','document-export','storage-recovery','write-interruption','file-actions','file-actions-remaining','canvas-file-delete','note-search','canvas-graph','canvas-graph-remaining','canvas-motion','canvas-view','canvas-view-remaining'):
            return  # Same immutable APK's five cases already have their own evidence.
        if not (binding := self.binding.get('native_tests')):
            return
        require_hosted_emulator(self.args.serial)
        package = APP + '.test'
        installed = self.shell('pm', 'list', 'packages', package).stdout.decode().splitlines()
        require('package:' + package not in installed, 'Native test data already exists; refusing to replace it')
        self.device('install', '-t', '--no-streaming', self.args.bundle / 'OpenNexus-android-tests.apk')
        result = self.shell('am', 'instrument', '-w', '-r', '-e', 'class', binding['test_class'],
            package + '/' + binding['runner'], timeout=180, check=False)
        output = result.stdout
        (self.output / 'keystore-device-tests.txt').write_bytes(output)
        (self.output / 'keystore-device-tests.stderr.txt').write_bytes(result.stderr)
        count = binding['expected_tests']
        require(count in (4, 5) and result.returncode == 0 and f'OK ({count} tests)'.encode() in output
            and b'FAILURES!!!' not in output and b'INSTRUMENTATION_FAILED' not in output,
            'Actual Android Keystore tests did not all pass; preserve the original instrument output')
        self.keystore_test_result = {'passed':True,'tests':count,'package':package,'test_APK_sha256':binding['sha256'],
            'scope':'Actual owned API36 AndroidKeyStore/AtomicFile tests; native session broker only, not login or remote Sync',
            'Rust_worker_verified':count == 5,
            'hardware_backing_claimed':False,'physical_phone_verified':False}
        (self.output / 'keystore-device-proof.json').write_text(json.dumps(self.keystore_test_result,indent=2)+'\n','utf-8')
        self.native_window_insets_tests()
        self.force_stop()

    def native_window_insets_tests(self):
        if not (binding := self.binding.get('native_window_insets')):
            return  # Older immutable packages have no such suite; never infer a pass.
        require_hosted_emulator(self.args.serial)
        require(binding.get('test_class') == 'cc.kronecker.opennexus.MobileWindowInsetsTest'
            and binding.get('expected_tests') == 3, 'Window inset test binding mismatch')
        result = self.shell('am','instrument','-w','-r','-e','class',binding['test_class'],
            APP+'.test/androidx.test.runner.AndroidJUnitRunner',timeout=90,check=False)
        (self.output/'window-insets-device-tests.txt').write_bytes(result.stdout)
        (self.output/'window-insets-device-tests.stderr.txt').write_bytes(result.stderr)
        require(result.returncode == 0 and b'OK (3 tests)' in result.stdout
            and b'FAILURES!!!' not in result.stdout and b'INSTRUMENTATION_FAILED' not in result.stdout,
            'Window inset tests did not all pass; preserve the original instrument output')
        proof = {'passed':True,'tests':3,'test_class':binding['test_class'],
            'test_APK_sha256':self.binding['native_tests']['sha256'],
            'scope':'Owned API36 native View/WindowInsets synthetic delivery, not actual keyboard visibility',
            'actual_keyboard_visibility_verified':False,'physical_phone_verified':False}
        (self.output/'window-insets-device-proof.json').write_text(json.dumps(proof,indent=2)+'\n','utf-8')

    def build_driver(self):
        root = Path(__file__).resolve().parent / 'android-runtime'
        sdk_tools = self.args.sdk / 'build-tools/36.0.0'
        platform = self.args.sdk / 'platforms/android-36/android.jar'
        key = self.tools / 'ephemeral-test-driver.p12'
        self.command(['keytool', '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
            '-alias', 'ci-driver', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '30', '-dname', 'CN=OpenNexus CI test driver'])
        identities = []
        for name, package, source in (('ime', TEST_IME, root / 'ime'), ('driver', DRIVER, root)):
            installed = self.shell('pm', 'list', 'packages', package).stdout.decode().splitlines()
            require('package:' + package not in installed, 'Owned tool package already exists; refusing to replace it')
            build = self.tools / name
            build.mkdir()
            classes, dex = build / 'classes', build / 'dex'
            classes.mkdir(); dex.mkdir()
            self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
                '-classpath', platform, '-d', classes, *sorted(source.glob('*.java'))])
            if name == 'driver':
                # This pure JVM admission check is not part of either APK.
                # It guards the actual one-tap driver used below on the device.
                checks = build / 'stability-checks'
                checks.mkdir()
                self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-classpath', classes,
                    '-d', checks, root.parent / 'android-runtime-tests/TargetStabilityCheck.java'])
                observed = self.command(['java', '-Xmx64m', '-cp', str(classes) + os.pathsep + str(checks),
                    'cc.kronecker.opennexus.testdriver.TargetStabilityCheck']).stdout
                (self.output / 'driver-target-stability.txt').write_bytes(observed)
                require(observed.startswith(b'TARGET_STABILITY_OK '), 'Actual driver target-stability check failed')
                if getattr(self.args, 'scope', '') == 'product-pair':
                    self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-classpath', classes,
                        '-d', checks, root.parent / 'android-runtime-tests/OriginalCanvasPropertyCheck.java'])
                    observed = self.command(['java', '-Xmx64m', '-cp', str(classes) + os.pathsep + str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalCanvasPropertyCheck']).stdout
                    (self.output / 'driver-original-canvas-property.txt').write_bytes(observed)
                    require(observed.startswith(b'ORIGINAL_CANVAS_PROPERTY_OK '), 'Original Canvas property admission failed')
                if getattr(self.args, 'scope', '') in ('canvas-graph','canvas-graph-remaining'):
                    self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-classpath', classes,
                        '-d', checks, root.parent / 'android-runtime-tests/OriginalCanvasGraphCheck.java'])
                    observed = self.command(['java', '-Xmx64m', '-cp', str(classes) + os.pathsep + str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalCanvasGraphCheck']).stdout
                    (self.output / 'driver-original-canvas-graph.txt').write_bytes(observed)
                    from android_canvas_graph_probe import PATH, SEED_A, SEED_B, TEXT, GROUP, FILE, URL, EDGE, STEPS
                    lines = observed.decode('ascii', 'strict').splitlines()
                    require(len(lines) == 2 and lines[0] == 'ORIGINAL_CANVAS_GRAPH_DATA_OK 69'
                        and lines[1].split() == [digest(x.encode()) for x in (PATH, SEED_A, SEED_B, TEXT, GROUP, FILE, URL, EDGE, *STEPS)],
                        'Original Canvas graph driver fixture data differs')
                if getattr(self.args,'scope','')=='write-interruption':
                    self.command(['javac','-J-Xmx256m','-encoding','UTF-8','-classpath',classes,
                        '-d',checks,root.parent/'android-runtime-tests/OriginalAtomicWriteCheck.java'])
                    observed=self.command(['java','-Xmx64m','-cp',str(classes)+os.pathsep+str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalAtomicWriteCheck']).stdout
                    (self.output/'driver-original-atomic-write.txt').write_bytes(observed)
                    from android_atomic_probe import NOTE,ORIGINAL,UPDATED
                    require(observed.decode('ascii').splitlines()==['ORIGINAL_ATOMIC_WRITE_DATA_OK',digest(NOTE.encode()),digest(ORIGINAL),digest(UPDATED)],
                        'Original atomic write driver bytes differ')
                if getattr(self.args,'scope','')=='canvas-view-remaining':
                    self.command(['javac','-J-Xmx128m','-encoding','UTF-8','-classpath',classes,
                        '-d',checks,root.parent/'android-runtime-tests/OriginalCanvasViewFrameCheck.java'])
                    observed=self.command(['java','-Xmx64m','-cp',str(classes)+os.pathsep+str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalCanvasViewFrameCheck']).stdout
                    (self.output/'driver-original-canvas-view-frame.txt').write_bytes(observed)
                    require(observed.decode('ascii').splitlines()==['ORIGINAL_CANVAS_VIEW_FRAME_OK 12'],
                        'Original remaining-view frame contract differs')
                if getattr(self.args,'scope','')=='canvas-motion':
                    self.command(['javac','-J-Xmx256m','-encoding','UTF-8','-classpath',classes,
                        '-d',checks,root.parent/'android-runtime-tests/OriginalCanvasMotionCheck.java'])
                    observed=self.command(['java','-Xmx64m','-cp',str(classes)+os.pathsep+str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalCanvasMotionCheck']).stdout
                    (self.output/'driver-original-canvas-motion.txt').write_bytes(observed)
                    from android_canvas_motion_probe import PATH,NODE,OTHER,EDGE,STEPS
                    lines=observed.decode('ascii').splitlines()
                    require(len(lines)==2 and lines[0]=='ORIGINAL_CANVAS_MOTION_DATA_OK 39'
                        and lines[1].split()==[digest(value.encode()) for value in (PATH,NODE,OTHER,EDGE,*STEPS)],
                        'Original Canvas motion driver data differs')
                if getattr(self.args, 'scope', '') == 'note-search':
                    self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-classpath', classes,
                        '-d', checks, root.parent / 'android-runtime-tests/OriginalNoteSearchCheck.java'])
                    observed = self.command(['java', '-Xmx64m', '-cp', str(classes) + os.pathsep + str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalNoteSearchCheck']).stdout
                    (self.output / 'driver-original-note-search.txt').write_bytes(observed)
                    from android_note_search_probe import SEED, ORIGINAL, CONTROL
                    lines = observed.decode('ascii', 'strict').splitlines()
                    require(len(lines) == 2 and lines[0].startswith('ORIGINAL_NOTE_SEARCH_DATA_OK ')
                        and lines[1].split() == [digest(x) for x in (SEED, ORIGINAL, CONTROL)],
                        'Original search driver fixture data differs')
                if getattr(self.args, 'scope', '') in ('file-actions','file-actions-remaining','canvas-file-delete'):
                    self.command(['javac', '-J-Xmx256m', '-encoding', 'UTF-8', '-classpath', classes,
                        '-d', checks, root.parent / 'android-runtime-tests/OriginalFileActionsCheck.java'])
                    observed = self.command(['java', '-Xmx64m', '-cp', str(classes) + os.pathsep + str(checks),
                        'cc.kronecker.opennexus.testdriver.OriginalFileActionsCheck']).stdout
                    (self.output / 'driver-original-file-actions.txt').write_bytes(observed)
                    from android_file_actions_probe import BODY as file_body, UPDATED as file_updated, NODE as file_node
                    lines = observed.decode('ascii', 'strict').splitlines()
                    require(len(lines) == 2 and lines[0].startswith('ORIGINAL_FILE_ACTIONS_DATA_OK ')
                        and lines[1].split() == [digest(x) for x in (file_body, file_updated, file_node.encode())],
                        'Original local file driver fixture data differs')
            self.command([sdk_tools / 'd8', '--lib', platform, '--min-api', '24', '--output', dex,
                *sorted(classes.rglob('*.class'))])
            unsigned, aligned, signed = [build / part for part in ('unsigned.apk', 'aligned.apk', name + '.apk')]
            arguments = [sdk_tools / 'aapt2', 'link', '-I', platform, '--manifest', source / 'AndroidManifest.xml',
                '--version-code', '1', '--version-name', 'ci', '-o', unsigned]
            if name == 'ime':
                resources = build / 'resources.zip'
                self.command([sdk_tools / 'aapt2', 'compile', '--dir', root / 'res', '-o', resources])
                arguments.extend(['-R', resources])
            self.command(arguments)
            with zipfile.ZipFile(unsigned, 'a') as archive:
                archive.write(dex / 'classes.dex', 'classes.dex')
            self.command([sdk_tools / 'zipalign', '-f', '4', unsigned, aligned])
            self.command([sdk_tools / 'apksigner', 'sign', '--ks', key, '--ks-pass', 'pass:android',
                '--key-pass', 'pass:android', '--out', signed, aligned])
            verification = self.command([sdk_tools / 'apksigner', 'verify', '--verbose', '--print-certs', signed]).stdout.decode()
            certificate = re.findall(r'Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})', verification)
            require(len(certificate) == 1, 'The original tool signer was not observed')
            identities.append({'package': package, 'sha256': digest(signed.read_bytes()), 'signer_sha256': certificate[0]})
            self.device('install', '--no-streaming', signed)
            observed = self.shell('pm', 'list', 'packages', '-U', package).stdout.decode().splitlines()
            uids = [int(match.group(1)) for line in observed
                if (match := re.fullmatch(re.escape('package:' + package) + r' uid:([0-9]+)', line))]
            require(len(uids) == 1 and uids[0] >= 10000, 'The separate tool UID was not observed')
            identities[-1]['uid'] = uids[0]
        require(identities[0]['signer_sha256'] == identities[1]['signer_sha256'], 'Signature-only tool permission is not shared')
        require(identities[0]['uid'] != identities[1]['uid'], 'Instrumentation and test IME share a UID')
        (self.output / 'test-ime-packages.json').write_text(json.dumps({'packages': identities,
            'separate_UIDs': True, 'permission': TEST_IME + '.permission.CONTROL', 'product_permissions_changed': False,
            'reason': 'Starting instrumentation force-stops only the driver package, not the separate real InputMethodService'}, indent=2)+'\n', 'utf-8')

    def wait_test_ime(self):
        require_hosted_emulator(self.args.serial)
        expected = TEST_IME_COMPONENT
        deadline = time.monotonic() + 15
        observations = []
        while True:
            # Package installation can finish before InputMethodManager handles
            # its package-added event. Only observe registration here; never
            # replay enable/set or modify an unrecognized service's settings.
            result = self.shell('ime', 'list', '-a', '-s', check=False)
            listing = result.stdout.decode('utf-8', 'replace')[:16384]
            registered = result.returncode == 0 and expected in listing.splitlines()
            observations.append({'returncode':result.returncode,'registered':registered,'listing':listing})
            (self.output/'ime-registration.json').write_text(json.dumps({'expected':expected,'observations':observations[-32:]},indent=2)+'\n','utf-8')
            if registered:
                return
            require(time.monotonic() < deadline, 'Owned test IME was not registered before the bounded deadline; no enable/set attempted')
            time.sleep(.25)

    def enable_test_ime(self):
        require_hosted_emulator(self.args.serial)
        original = self.shell('settings', 'get', 'secure', 'default_input_method').stdout.decode().strip()
        require(re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+', original), 'Original hosted-emulator IME cannot be identified')
        require(original != TEST_IME_COMPONENT, 'Test IME already selected before this owned run')
        hardware = self.shell('settings', 'get', 'secure', 'show_ime_with_hard_keyboard').stdout.decode().strip()
        require(hardware in ('0', '1', 'null'), 'Original hosted-emulator hardware-keyboard preference is unknown')
        self.wait_test_ime()
        self.original_ime = original
        self.original_hardware_ime = hardware
        self.shell('ime', 'enable', TEST_IME_COMPONENT)
        self.shell('ime', 'set', TEST_IME_COMPONENT)
        self.shell('settings', 'put', 'secure', 'show_ime_with_hard_keyboard', '1')

    def restore_test_ime(self):
        if not self.original_ime:
            return
        require_hosted_emulator(self.args.serial)
        observed = self.shell('settings', 'get', 'secure', 'default_input_method', check=False).stdout.decode().strip()
        if observed == TEST_IME_COMPONENT:
            self.shell('ime', 'set', self.original_ime)
            self.shell('ime', 'disable', TEST_IME_COMPONENT)
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

    def phase(self, phase, *, continue_process=False, keep_running=False):
        if not continue_process:
            self.force_stop()
        start = time.monotonic()
        if not continue_process:
            launch = self.shell('am', 'start', '-W', '-n', self.activity)
            if getattr(getattr(self,'args',None),'scope',None) == 'performance':
                from android_performance_probe import parse_launch
                (self.output/(phase+'-launch.json')).write_text(json.dumps(parse_launch(launch.stdout))+'\n','utf-8')
        else:
            require_hosted_emulator(self.args.serial)
            self.wait_owned_connection()
            require(re.fullmatch(rb'[1-9][0-9]{0,9}', self.shell('pidof', APP).stdout.strip()),
                'Tablet rotation requires the same single owned app process')
        self.native_tls_startup(phase)
        if getattr(getattr(self,'args',None),'scope','') in ('canvas-view','canvas-view-remaining') and phase in ('view-pan','view-pinch','view-cancel','view-takeover','view-background'):
            from android_canvas_view_input import instrument_original_view
            result=instrument_original_view(self,phase)
        elif phase in ('rich-image', 'canvas-image') and getattr(getattr(self, 'args', None), 'scope', '') in ('attachments-remaining','canvas-remaining','editing-remaining'):
            result = self.image_save_instrumentation(phase)
        elif getattr(getattr(self,'args',None),'scope','')=='canvas-motion' and phase=='motion-background':
            require(re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.]+',self.activity),'Original motion resume activity refused')
            result=self.shell('am','instrument','-w','-e','phase',phase,'-e','motion_activity',self.activity,
                f'{DRIVER}/.RuntimeProbe',timeout=180)
        else:
            result = self.shell('am', 'instrument', '-w', '-e', 'phase', phase, f'{DRIVER}/.RuntimeProbe', timeout=180)
        text = result.stdout.decode('utf-8', 'replace')
        (self.output / f'{phase}.log').write_text(text, 'utf-8')
        self.capture(phase)
        passed = f'OPENNEXUS_RUNTIME_PHASE_OK {phase}' in text and 'OPENNEXUS_RUNTIME_FAILED' not in text
        if not passed:
            try:
                self.read_native_styles(phase+'-failed')
            except Exception as error:
                (self.output/(phase+'-failed-diagnostic-error.json')).write_text(
                    json.dumps({'available':False,'error':type(error).__name__})+'\n','utf-8')
            self.capture_failed_editor_state(phase)
        require(passed,
            f'Actual Android UI phase failed: {phase}; see preserved UI log and screenshot')
        self.phases.append({'phase': phase, 'duration_ms': round((time.monotonic() - start) * 1000), 'passed': True})
        if phase == 'theme-diagnostic-dark' or phase.startswith(('tablet-', 'canvas-tools-')):
            self.read_native_styles(phase)
        if not keep_running:
            self.force_stop()
        if self.root is None and not phase.startswith('theme-'):
            self.discover_private_root()
        if phase in ('rich-image', 'canvas-image') and getattr(getattr(self, 'args', None), 'scope', '') in ('attachments-remaining','canvas-remaining','editing-remaining'):
            after = self.image_save_rows(phase, 'after-save')
            before = json.loads((self.output/(phase+'-stable-before-save-receipts.json')).read_text('utf-8'))
            validate_image_save_result(before, after)

    def image_save_rows(self, phase, label):
        require_hosted_emulator(self.args.serial)
        require(phase in ('rich-image', 'canvas-image') and (label in ('before-save', 'stable-before-save', 'after-save', 'failed') or re.fullmatch(r'observe-[0-7]-(?:before|stable)',label)),
            'Refusing an unrelated image/save metadata observation')
        self.wait_owned_connection()
        if self.root is None:
            self.discover_private_root()
        path = 'runtime-rich-image.md' if phase == 'rich-image' else 'runtime-image.canvas'
        columns = {
            'drafts': ('id', 'path', 'generation', 'saved_generation', 'content'),
            'saves': ('operation_id', 'draft_id', 'generation', 'state', 'error'),
            'images': ('operation_id', 'draft_id', 'generation', 'asset_path', 'hash', 'size', 'state', 'content_after', 'error'),
        }
        report = {'path': path, 'scope': 'Only exact owned synthetic target; read-only copied draft database'}
        for key, table in (('drafts', 'drafts'), ('saves', 'save_intents'), ('images', 'image_intents')):
            rows = self.database_rows('SELECT '+','.join(columns[key])+' FROM '+table+' WHERE path=? ORDER BY rowid LIMIT 3', (path,))
            report[key] = [dict(zip(columns[key], row)) for row in rows]
        (self.output/(phase+'-'+label+'-receipts.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', 'utf-8')
        return report

    def wait_original_image_save_observation(self, phase, process):
        # The original Android picker can return before its one import commits.
        # Observe only that same draft/intent; never re-pick, edit or tap Save.
        deadline = time.monotonic()+20
        identity = initial = original_image = None
        last_generation = last_image_state = -1
        last_error = 'Original image import did not become ready; no Save sent'
        for observation in range(8):
            require(process.poll() is None, 'Original image driver ended before observation')
            before = self.image_save_rows(phase, f'observe-{observation}-before')
            rendered = self.read_native_styles(f'{phase}-save-observation-{observation}')
            stable = self.image_save_rows(phase, f'observe-{observation}-stable')
            for report in (before, stable):
                require(len(report['drafts']) == 1 and not report['saves'] and len(report['images']) <= 1,
                    'Unknown Save, duplicate draft or image; no Save sent')
                draft = report['drafts'][0]
                current = (report.get('path'),draft['id'],draft['path'])
                if identity is None:
                    identity, initial = current, (draft['generation'],draft['content'])
                require(current == identity, 'Original import identity changed during observation; no Save sent')
                require(draft['saved_generation'] == 0 and draft['generation'] >= last_generation,
                    'Original draft was saved or regressed during observation; no Save sent')
                last_generation = draft['generation']
                if not report['images']:
                    require(original_image is None and (draft['generation'],draft['content']) == initial,
                        'Original import disappeared or draft changed; no Save sent')
                    continue
                image = report['images'][0]
                frozen = {key:value for key,value in image.items() if key not in ('state','error')}
                if original_image is None:
                    original_image = frozen
                require(frozen == original_image and image['state'] in ('prepared','committed') and not image.get('error'),
                    'Original image intent changed or failed; no Save sent')
                image_state = int(image['state'] == 'committed')
                require(image_state >= last_image_state, 'Original image intent regressed; no Save sent')
                last_image_state = image_state
                require(image['draft_id'] == draft['id'] and image['hash'] == digest(IMAGE_BYTES)
                    and image['size'] == len(IMAGE_BYTES)
                    and draft['generation'] in (image['generation'],image['generation']+1),
                    'Original image identity, bytes or generation changed; no Save sent')
                if draft['generation'] == image['generation']+1:
                    require(draft['content'] == image['content_after'], 'Original image content changed; no Save sent')
            if before == stable:
                try:
                    validate_image_save_observation(stable, rendered, phase)
                except RuntimeError as error:
                    last_error = str(error)
                else:
                    # Keep the canonical exact before/after comparison consumed
                    # by phase(), alongside every bounded original observation.
                    for label,value in (('before-save',before),('stable-before-save',stable)):
                        (self.output/(phase+'-'+label+'-receipts.json')).write_text(json.dumps(value,indent=2)+'\n','utf-8')
                    return stable
            require(time.monotonic() < deadline, last_error)
            time.sleep(.2)
        raise RuntimeError(last_error)

    def image_save_instrumentation(self, phase):
        require_hosted_emulator(self.args.serial)
        require(phase in ('rich-image', 'canvas-image'), 'Refusing unrelated interactive image phase')
        self.wait_owned_connection()
        command = ('am', 'instrument', '-w', '-e', 'phase', phase, '-e', 'image_checkpoint', 'yes', f'{DRIVER}/.RuntimeProbe')
        process = subprocess.Popen([str(self.adb), '-s', self.args.serial, 'shell', shlex.join(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = error = b''
        finished = False
        try:
            stages = ('editor', 'save') if phase == 'rich-image' else ('save',)
            for stage in stages:
                deadline = time.monotonic()+95
                while True:
                    if process.poll() is not None:
                        output, error = process.communicate(timeout=5)
                        finished = True
                        return subprocess.CompletedProcess(command, process.returncode, output, error)
                    ready = self.shell('run-as', DRIVER, 'cat', 'no_backup/owned-'+phase+'-'+stage+'.ready', check=False)
                    if ready.returncode == 0 and ready.stdout == b'ready\n':
                        break
                    require(time.monotonic() < deadline, 'Original image checkpoint unavailable; no input replay')
                    time.sleep(.2)
                if stage == 'editor':
                    # A named AX editor can appear before Milkdown finishes
                    # hydration. Observe actual readiness before one focus/tap;
                    # never repeatedly focus or send content to a stale node.
                    deadline = time.monotonic()+20
                    for observation in range(15):
                        rendered = self.read_native_styles(phase+'-editor-'+str(observation))
                        if rich_image_editor_ready(rendered):
                            break
                        require(time.monotonic() < deadline, 'Original rich editor stayed unready; no text sent')
                        time.sleep(.2)
                    require(rich_image_editor_ready(rendered), 'Original rich editor never became ready; no text sent')
                else:
                    self.wait_original_image_save_observation(phase, process)
                require(process.poll() is None, 'Original image driver ended before input approval')
                signal = 'no_backup/owned-'+phase+'-'+stage+'.proceed'
                pending = signal+'.pending'
                # The driver must never observe the final name while stdin is
                # still being written. Publish the complete owned marker exactly
                # once; an existing approval remains untouched and is refused.
                # The owned Android runner rejects hard links. Its ordinary
                # same-directory, nonreplacing rename needs no permission change.
                publication = f'umask 077; test ! -e {signal} && test ! -e {pending} && cat > {pending} && test "$(wc -c < {pending})" -eq 8 && mv -n {pending} {signal} && test ! -e {pending}'
                self.device('shell', '-T', 'run-as '+DRIVER+' sh -c '+shlex.quote(publication),input=b'proceed\n')
            output, error = process.communicate(timeout=40)
            finished = True
            return subprocess.CompletedProcess(command, process.returncode, output, error)
        finally:
            if not finished:
                if process.poll() is None:
                    process.terminate()
                try:
                    output, error = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); output, error = process.communicate(timeout=10)
                (self.output/(phase+'-instrumentation-unfinished.log')).write_bytes(output)
                self.capture(phase+'-unfinished')
                self.capture_failed_editor_state(phase)

    def discover_private_root(self):
        paths = self.shell('run-as', APP, 'find', '.', '-name', 'mobile-drafts.sqlite3', '-type', 'f').stdout.decode().splitlines()
        require(len(paths) == 1 and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)*mobile-drafts\.sqlite3', paths[0])
            and '..' not in paths[0].split('/'), 'Native draft database origin is ambiguous')
        self.root = paths[0].removesuffix('mobile-drafts.sqlite3')

    def native_process_identity(self, pid):
        require_hosted_emulator(self.args.serial)
        require(type(pid) is int and 0 < pid < 2**31, 'Native TLS process ID is invalid')
        prefix = '/proc/' + str(pid)
        return native_process_lifetime(pid,
            self.shell('run-as', APP, 'cat', prefix + '/stat').stdout,
            self.shell('run-as', APP, 'cat', prefix + '/cmdline').stdout,
            self.shell('run-as', APP, 'id', '-u').stdout,
            self.shell('run-as', APP, 'cat', prefix + '/status').stdout)

    def native_tls_startup(self, phase):
        if not self.binding.get('native_tls'):
            return  # Existing immutable APKs did not contain this native bridge.
        require_hosted_emulator(self.args.serial)
        pid = self.shell('pidof', APP).stdout.decode().strip()
        require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'Native TLS startup requires the exact owned app process')
        identity = self.native_process_identity(int(pid))
        key = (identity['pid'], identity['start_ticks'], identity['uid'])
        origins = getattr(self, 'tls_process_origins', None)
        if origins is None:
            origins = self.tls_process_origins = {}
        ready = re.compile(rb'[VDIWEAF]/OpenNexusSync\(\s*' + pid.encode() + rb'\): SYNC_TLS_READY')
        deadline = time.monotonic() + 5
        while True:
            logs = self.shell('logcat', '--pid=' + pid, '-d', '-v', 'brief', 'OpenNexusSync:I', '*:S').stdout
            require(len(logs) <= 16384, 'Unexpected native TLS startup log size')
            (self.output / (phase + '-native-tls.log')).write_bytes(logs)
            require(b'SYNC_TLS_UNAVAILABLE' not in logs, 'Native platform TLS initialization failed; offline data preserved')
            require(self.native_process_identity(int(pid)) == identity,
                'Native TLS process changed during original observation')
            seen = [line.rstrip() for line in logs.splitlines() if line.rstrip().endswith(b': SYNC_TLS_READY')]
            require(all(ready.fullmatch(line) for line in seen), 'Native TLS ready event belongs to another process')
            if seen and key not in origins:
                origins[key] = {'identity': identity, 'phase': phase, 'log_sha256': digest(logs)}
            if seen or key in origins:
                origin = origins[key]
                original = (self.output / (origin['phase'] + '-native-tls.log')).read_bytes()
                require(origin['identity'] == identity and digest(original) == origin['log_sha256']
                    and any(ready.fullmatch(line.rstrip()) for line in original.splitlines())
                    and b'SYNC_TLS_UNAVAILABLE' not in original, 'Original Native TLS proof changed')
                record = {'phase':phase,'owned_pid':int(pid),'native_context_initialized':True,'network_tested':False,
                    'identity':identity,'source':'fresh_log' if seen else 'retained_original_log',
                    'origin_phase':origin['phase'],'origin_log_sha256':origin['log_sha256']}
                (self.output / (phase + '-native-tls.json')).write_text(json.dumps(record, indent=2) + '\n', 'utf-8')
                self.tls_initializations.append(record)
                return
            require(time.monotonic() < deadline, 'Native TLS startup had no verified initialization result')
            time.sleep(.2)
    def capture_failed_editor_state(self, phase):
        report = {'scope': 'Owned hosted fixture after failed phase and force-stop; no UI action or write replay',
            'originals': {}, 'phase': phase}
        try:
            require_hosted_emulator(self.args.serial)
            self.wait_owned_connection()
            self.force_stop()
            if self.root is None:
                self.discover_private_root()
            # These are exclusively the probe's own synthetic targets. Do not
            # enumerate arbitrary files or read native credential/config stores.
            owned_names = ('runtime-note.md', 'runtime-rich.md', 'runtime.canvas',
                    'runtime-back.md', 'runtime-image.md', 'runtime-rich-image.md', 'runtime-image.canvas')
            if getattr(getattr(self,'args',None),'scope',None) == 'performance':
                owned_names = ('runtime-long.md',)
            if getattr(getattr(self,'args',None),'scope',None) == 'storage-recovery':
                owned_names = ('full-area/original.md',)
            if getattr(getattr(self,'args',None),'scope',None) in ('canvas-graph','canvas-graph-remaining'):
                from android_canvas_graph_probe import PATH
                owned_names = (PATH,)
            if getattr(getattr(self,'args',None),'scope',None) == 'canvas-motion':
                from android_canvas_motion_probe import PATH
                owned_names = (PATH,)
            if getattr(getattr(self,'args',None),'scope',None) in ('canvas-view','canvas-view-remaining'):
                from android_canvas_view_probe import PATH
                owned_names = (PATH,)
            if getattr(getattr(self,'args',None),'scope',None) in ('document-limits','document-export'):
                from android_document_limits_probe import SEED_PATH, CASES
                owned_names = (SEED_PATH, *(path for _, path, _, _ in CASES))
            if getattr(getattr(self,'args',None),'scope',None) == 'note-search':
                from android_note_search_probe import PATHS
                owned_names = PATHS
            if getattr(getattr(self,'args',None),'scope',None) in ('file-actions','file-actions-remaining','canvas-file-delete'):
                owned_names = ('fileops-original.md', 'fileops-original.canvas', 'fileops/资料😀.md', 'fileops/资料😀.canvas')
            for name in owned_names:
                try:
                    value = self.original(name)
                    report['originals'][name] = {'size': len(value), 'sha256': digest(value)}
                except Exception as error:
                    report['originals'][name] = {'unavailable': type(error).__name__}
            report['draft_rows'] = self.draft_rows()
            if phase in ('rich-image', 'canvas-image'):
                report['image_save_metadata'] = self.image_save_rows(phase, 'failed')
        except Exception as error:
            report['observation_error'] = type(error).__name__
        (self.output / f'{phase}-failed-native-state.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')

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
            observed = self.command(['node', '--max-old-space-size=128', script, 'ws://127.0.0.1:' + port + socket.path], timeout=20)
            report.update(available=True, styles=json.loads(observed.stdout))
        except Exception as error:
            report.update(available=False, failure=type(error).__name__, reason=str(error)[:400])
        finally:
            if port and re.fullmatch(r'[0-9]{4,5}', port):
                report['own_forward_removed'] = self.device('forward', '--remove', 'tcp:' + port, check=False).returncode == 0
            (self.output/(phase+'-computed-styles.json')).write_text(json.dumps(report,indent=2)+'\n','utf-8')
        return report

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

    def database_rows(self, query, parameters=()):
        snapshot = Path(tempfile.mkdtemp(prefix='draft-snapshot-', dir=self.tools))
        database = snapshot / 'mobile-drafts.sqlite3'
        for suffix in ('', '-wal', '-shm'):
            value = self.private(self.root + 'mobile-drafts.sqlite3' + suffix, check=False)
            if value.returncode == 0:
                Path(str(database) + suffix).write_bytes(value.stdout)
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'Draft database integrity check failed')
            return db.execute(query, parameters).fetchall()

    def draft_rows(self):
        return self.database_rows('SELECT path,content,generation,saved_generation FROM drafts ORDER BY path')

    def check_empty_native_auth_index(self):
        snapshot=Path(tempfile.mkdtemp(prefix='auth-read-snapshot-',dir=self.tools))
        database=snapshot/'mobile-sync-auth.sqlite3'
        for suffix in ('','-wal','-shm'):
            result=self.private(self.root+'mobile-sync-auth.sqlite3'+suffix,check=False)
            if result.returncode==0:Path(str(database)+suffix).write_bytes(result.stdout)
        require(database.is_file(),'Native authentication index was not created by the actual command gateway')
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'Native authentication metadata integrity failed')
            counts={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                for name in ('auth_scopes','auth_intents','auth_profile_choices','auth_selection')}
            require(all(count==0 for count in counts.values()),'Reading connection settings created an authentication operation or selection')
        proof={'passed':True,'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,
            'apk_sha256':self.binding['sha256'],'actual_native_current_and_profile_UI_read':True,
            'original_metadata_counts':counts,'database_integrity':'ok','login_or_HTTPS_verified':False,
            'selected_credential_decryption_verified':False,'network_auth_mutation_requested':False,
            'scope':'Actual application main-window Auth IPC, Activity foreground lease and initialized native store; empty metadata reads only'}
        (self.output/'native-auth-read-proof.json').write_text(json.dumps(proof,indent=2)+'\n','utf-8')
        self.native_auth_read_result=proof

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

    def verify_original_images(self, include_markdown=True, include_rich=True):
        asset = f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
        require(self.original(asset) == IMAGE_BYTES, 'System-selected image was re-encoded or replaced')
        reference = ('![](' + asset + ')').encode()
        originals={}
        if include_markdown:
            markdown = self.original('runtime-image.md')
            require(markdown.count(reference) == 1 and markdown.replace(reference, b'') == IMAGE_BODY.encode(), 'Image insertion changed the original Markdown or duplicated its reference')
            originals['runtime-image.md']=markdown
        if include_rich:
            rich = self.original('runtime-rich-image.md')
            require(rich.count(reference) == 1 and rich.replace(reference,b'') == (RICH_IMAGE_TEXT + '\n').encode(), 'Rich image changed original text or duplicated its reference')
            originals['runtime-rich-image.md']=rich
        canvas = self.original('runtime-image.canvas')
        nodes = json.loads(canvas)['nodes']
        require(len(nodes) == 1 and nodes[0]['type'] == 'file' and nodes[0]['file'] == asset, 'Canvas image node differs from the chosen original')
        rows = self.database_rows('SELECT operation_id,path,asset_path,hash,size,state FROM image_intents ORDER BY path')
        expected={'runtime-image.canvas'} | ({'runtime-rich-image.md'} if include_rich else set()) | ({'runtime-image.md'} if include_markdown else set())
        require(len(rows) == len(expected) and len({row[0] for row in rows}) == len(expected)
            and {row[1] for row in rows} == expected
            and all(row[2:] == (asset, digest(IMAGE_BYTES), len(IMAGE_BYTES), 'committed') for row in rows),
            'Exactly one committed original image receipt per target is required')
        receipts = [dict(zip(('operation_id', 'path', 'asset_path', 'sha256', 'size', 'state'), row)) for row in rows]
        (self.output / 'image-import-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n', 'utf-8')
        return {**originals, 'runtime-image.canvas': canvas, asset: IMAGE_BYTES}

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

    def tablet_display(self, width, height):
        require_hosted_emulator(self.args.serial)
        self.wait_owned_connection()
        require((width, height) in ((1600, 2400), (2560, 1600)), 'Unexpected tablet display mode')
        if not getattr(self, 'tablet_display_owned', False):
            size = self.shell('wm', 'size').stdout.decode()
            density = self.shell('wm', 'density').stdout.decode()
            require(re.fullmatch(r'Physical size: [1-9][0-9]*x[1-9][0-9]*\s*', size)
                and re.fullmatch(r'Physical density: [1-9][0-9]*\s*', density), 'Refusing to replace pre-existing display overrides')
            (self.output / 'tablet-original-display.json').write_text(json.dumps({'size':size, 'density':density}), 'utf-8')
            self.tablet_display_owned = True  # Also restore a partially accepted setting.
        self.shell('wm', 'size', f'{width}x{height}')
        self.shell('wm', 'density', '320')

    def restore_tablet_display(self):
        if not getattr(self, 'tablet_display_owned', False):
            return
        require_hosted_emulator(self.args.serial)
        self.wait_owned_connection()
        self.shell('wm', 'size', 'reset')
        self.shell('wm', 'density', 'reset')
        require(b'Override' not in self.shell('wm', 'size').stdout + self.shell('wm', 'density').stdout,
            'Owned tablet display overrides were not restored')
        self.tablet_display_owned = False

    def run_tablet(self):
        self.enable_test_ime()
        self.phase('tablet-seed')  # Source input once; no duplicate rich-input acceptance claim.
        original = self.original('runtime-rich.md')
        require(original == ('**' + RICH_TEXT + '**\n').encode(), 'Actual source-entered tablet seed differs from original Markdown')
        rows = self.draft_rows()
        layouts = {}
        try:
            self.tablet_display(1600, 2400)
            self.phase('tablet-portrait', keep_running=True)
            portrait_pid = self.shell('pidof', APP).stdout.strip()
            layouts['portrait'] = validate_tablet_layout(json.loads((self.output/'tablet-portrait-computed-styles.json').read_text('utf-8')), True)
            self.tablet_display(2560, 1600)
            self.phase('tablet-landscape', continue_process=True, keep_running=True)
            require(self.shell('pidof', APP).stdout.strip() == portrait_pid, 'Tablet rotation restarted the process')
            layouts['landscape'] = validate_tablet_layout(json.loads((self.output/'tablet-landscape-computed-styles.json').read_text('utf-8')), False)
            self.force_stop()
            require(self.original('runtime-rich.md') == original and self.draft_rows() == rows,
                'Read-only tablet layout/rotation changed the original file or draft')
        finally:
            self.restore_tablet_display()
        proof = {'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,'compiled_from':self.args.build_origin,
            'apk_sha256':self.binding['sha256'],'signer_certificate_sha256':self.binding['signer_certificate_sha256'],
            'passed':True,'scope':'Owned API36 emulator display widths800/1280 and same-process configuration change; not a physical tablet',
            'layouts':layouts,'original_sha256':digest(original),'original_bytes':len(original),'draft_unchanged':True,
            'synthetic_seed_written_once':True,'read_only_after_seed':True,'same_process_pid':int(portrait_pid),
            'owned_display_restored':True,'physical_tablet_verified':False,'physical_phone_verified':False,'published':False}
        (self.output/'tablet-layout-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n','utf-8')
        print(json.dumps(proof,ensure_ascii=False,indent=2))

    def run_attachments(self):
        self.enable_test_ime()
        self.stage_original_image()
        remaining=self.args.scope in ('attachments-remaining','canvas-remaining')
        canvas_only=self.args.scope=='canvas-remaining'
        phases=(() if remaining else ('markdown-image','markdown-image-reopen'))+(() if canvas_only else ('rich-image','rich-image-reopen'))+('canvas-image','canvas-image-reopen')
        for phase in phases:
            self.phase(phase)
        originals=self.verify_original_images(include_markdown=not remaining,include_rich=not canvas_only)
        self.phase('export')
        exported=self.verify_export(originals)
        for name,value in originals.items():
            path=self.output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
        proof={'passed':True,'scope':self.args.scope,'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,
            'compiled_from':self.args.build_origin,'apk_sha256':self.binding['sha256'],
            'signer_certificate_sha256':self.binding['signer_certificate_sha256'],
            'phases':self.phases,'actual_original_image_import_and_cold_reopen':True,
            'native_committed_image_receipts':1 if canvas_only else 2 if remaining else 3,'actual_system_saved_content':exported,
            'previous_markdown_component_not_retested':remaining,'previous_rich_component_not_retested':canvas_only,
            'originals':{name:{'size':len(value),'sha256':digest(value)} for name,value in originals.items()},
            'full_editor_lifecycle_or_back_draft_export_exclusion_retested':False,
            'keystore_tests_repeated':False,'physical_phone_verified':False,'Android_HTTPS_or_sync_verified':False}
        (self.output/'attachments-proof.json').write_text(json.dumps(proof,indent=2)+'\n','utf-8')

    def run_canvas_tools(self):
        self.enable_test_ime()
        self.phase('canvas-tools-seed')
        original = self.original('runtime.canvas')
        nodes = json.loads(original)['nodes']
        require(len(nodes) == 1 and nodes[0]['text'] == NODE, 'Original Canvas layout fixture differs from actual input')
        rows = self.draft_rows(); views = {}
        self.phase('canvas-tools-phone-open', keep_running=True)
        pid = self.shell('pidof', APP).stdout.strip()
        for phase, expanded, menu in [('canvas-tools-phone-open', True, False), ('canvas-tools-phone-menu', True, True),
                ('canvas-tools-phone-back-menu', True, False), ('canvas-tools-phone-back-panel', False, False),
                ('canvas-tools-dark', True, False), ('canvas-tools-light', True, False)]:
            if phase != 'canvas-tools-phone-open': self.phase(phase, continue_process=True, keep_running=True)
            views[phase] = validate_canvas_tools(json.loads((self.output/(phase+'-computed-styles.json')).read_text('utf-8')),
                expanded=expanded, menu=menu)
        require(views['canvas-tools-dark']['colors'] != views['canvas-tools-light']['colors'],
            'Actual Canvas theme colors did not change')
        try:
            for width, size in [(800, (1600,2400)), (1280, (2560,1600))]:
                self.tablet_display(*size)
                phase = 'canvas-tools-tablet-'+str(width)
                self.phase(phase, continue_process=True, keep_running=True)
                require(self.shell('pidof', APP).stdout.strip() == pid, 'Owned Canvas display change restarted the app')
                report = self.output/(phase+'-computed-styles.json')
                views[str(width)] = validate_canvas_tools(json.loads(report.read_text('utf-8')), width=width)
        finally:
            self.restore_tablet_display()
        self.force_stop()
        require(self.original('runtime.canvas') == original and self.draft_rows() == rows,
            'Read-only Canvas tools, Back, theme or display observation changed original content or drafts')
        proof = {'passed':True,'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,
            'compiled_from':self.args.build_origin,'apk_sha256':self.binding['sha256'],'layouts':views,
            'original_canvas_sha256':digest(original),'original_canvas_bytes':len(original),'original_draft_rows_unchanged':True,
            'synthetic_seed_saved_once':True,'read_only_after_seed':True,'owned_display_restored':True,
            'physical_phone_or_tablet_verified':False,'sync_or_desktop_pair_retested':False,'keystore_tests_repeated':False}
        (self.output/'canvas-tools-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n','utf-8')

    def run_canvas_tablet(self):
        # The first density configuration may recreate an Android activity.
        # Open the saved original explicitly at 800px; only the later width
        # change is the same-process, already-open-editor continuity check.
        self.enable_test_ime()
        self.phase('canvas-tools-seed')
        original = self.original('runtime.canvas')
        nodes = json.loads(original)['nodes']
        require(len(nodes) == 1 and nodes[0]['text'] == NODE, 'Original tablet Canvas fixture differs from actual input')
        rows = self.draft_rows(); views = {}
        try:
            self.tablet_display(1600, 2400)
            self.phase('canvas-tools-tablet-open-800', keep_running=True)
            pid = self.shell('pidof', APP).stdout.strip()
            require(re.fullmatch(rb'[1-9][0-9]{0,9}', pid), 'Tablet Canvas requires one original app PID')
            report = self.output/'canvas-tools-tablet-open-800-computed-styles.json'
            views['800'] = validate_canvas_tools(json.loads(report.read_text('utf-8')), width=800)
            self.tablet_display(2560, 1600)
            self.phase('canvas-tools-tablet-1280', continue_process=True, keep_running=True)
            require(self.shell('pidof', APP).stdout.strip() == pid, 'Owned Canvas tablet width change restarted the app')
            report = self.output/'canvas-tools-tablet-1280-computed-styles.json'
            views['1280'] = validate_canvas_tools(json.loads(report.read_text('utf-8')), width=1280)
        finally:
            self.restore_tablet_display()
        self.force_stop()
        require(self.original('runtime.canvas') == original and self.draft_rows() == rows,
            'Read-only tablet Canvas observation changed original content or drafts')
        proof = {'passed':True,'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,
            'compiled_from':self.args.build_origin,'apk_sha256':self.binding['sha256'],'layouts':views,
            'original_canvas_sha256':digest(original),'original_canvas_bytes':len(original),
            'original_draft_rows_unchanged':True,'synthetic_seed_saved_once':True,'read_only_after_seed':True,
            'initial_800px_cold_open_explicit':True,'same_PID_800_to_1280':int(pid),'owned_display_restored':True,
            'phone_Back_menu_theme_cases_replayed':False,'physical_phone_or_tablet_verified':False,
            'sync_or_desktop_pair_retested':False,'keystore_tests_repeated':False}
        (self.output/'canvas-tablet-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n','utf-8')

    def run(self):
        self.boot()
        if self.args.scope in ('canvas-view','canvas-view-remaining'):
            from android_canvas_view_probe import run_canvas_view
            run_canvas_view(self)
            return
        if self.args.scope=='canvas-motion':
            from android_canvas_motion_probe import run_canvas_motion
            run_canvas_motion(self)
            return
        if self.args.scope in ('document-limits','document-export'):
            from android_document_limits_probe import run_document_limits
            run_document_limits(self)
            return
        if self.args.scope=='write-interruption':
            from android_atomic_probe import run_atomic
            run_atomic(self)
            return
        if self.args.scope in ('canvas-graph','canvas-graph-remaining'):
            from android_canvas_graph_probe import run_canvas_graph
            run_canvas_graph(self)
            return
        if self.args.scope == 'note-search':
            from android_note_search_probe import run_note_search
            run_note_search(self)
            return
        if self.args.scope in ('file-actions','file-actions-remaining','canvas-file-delete'):
            from android_file_actions_probe import run_file_actions
            run_file_actions(self)
            return
        if self.args.scope == 'storage-recovery':
            from android_storage_probe import run_storage_recovery
            run_storage_recovery(self)
            return
        if self.args.scope == 'performance':
            from android_performance_probe import run_performance
            run_performance(self)
            return
        if self.args.scope == 'canvas-tablet':
            self.run_canvas_tablet()
            return
        if self.args.scope == 'canvas-tools':
            self.run_canvas_tools()
            return
        if self.args.scope == 'editing-remaining':
            self.enable_test_ime()
            self.phase('ime-seed')  # One fresh UI fixture, not old theme/Markdown/recovery acceptance.
            require(self.original('runtime-note.md') == DRAFT.encode(), 'New owned IME fixture differs from its original bytes')
            self.finish_editing(rich=None)
            return
        if self.args.scope in ('attachments','attachments-remaining','canvas-remaining'):
            self.run_attachments()
            return
        if self.args.scope == 'tablet':
            self.run_tablet()
            return
        if self.args.scope == 'all':
            for theme in ('dark', 'light', 'sepia', 'system'):
                self.phase('theme-' + theme)
        else:
            self.phase('theme-diagnostic-dark')
            self.phase('theme-diagnostic-reset')
        if self.binding.get('native_auth_gateway',{}).get('present') is True:
            self.phase('auth-read')
            self.check_empty_native_auth_index()
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
        self.finish_editing(rich=rich)

    def finish_editing(self, *, rich):
        # Also used by the narrow remaining scope; no replay of passed initial components.
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
        originals = {'runtime-note.md': (DRAFT + IME_TEXT).encode(), 'runtime.canvas': after, **images}
        if rich is not None:
            originals['runtime-rich.md'] = rich
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
            'native_tls_initializations': self.tls_initializations,
            'native_keystore_tests': self.keystore_test_result,
            'native_auth_gateway_read': self.native_auth_read_result,
            'previous_components_not_retested': self.args.scope == 'editing-remaining',
            'remaining_scope_seed': 'One new note through the real UI, solely to bind actual Chinese composition' if self.args.scope == 'editing-remaining' else None,
            'native_theme_contrast_verified': False,
            'theme_style_diagnostic': 'theme-diagnostic-dark-computed-styles.json' if self.args.scope == 'lifecycle' else None,
            'theme_ui': 'Actual shared desktop theme card choices and cold reloads; rendered card screenshots/window observations retained for appearance review' if self.args.scope == 'all' else 'Not repeated in lifecycle scope; theme acceptance requires independent same-APK evidence',
            'system_destination': export, 'draft_database_integrity': 'ok',
            'input_method': 'Real Android InputMethodService/InputConnection for source/rich body text, pinyin, Chinese composing text and candidate confirmation; accessibility only for ordinary path/property fields. Test-only IME, not Gboard/Sogou or physical-device proof.',
            'system_back': 'Actual Android Back key; durable draft recovery and own-task background; not predictive gesture animation proof',
            'physical_phone_verified': False, 'Chinese_IME_verified': True, 'commercial_Chinese_IME_verified': False, 'sync_verified': False,
            'disk_full_verified': False, 'shared_rich_writing_verified': rich is not None, 'image_picker_verified': True,
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
    parser.add_argument('--scope', choices=('all', 'lifecycle', 'tablet','attachments','attachments-remaining','canvas-remaining','editing-remaining','canvas-tools','canvas-tablet','performance','document-limits','document-export','storage-recovery','write-interruption','file-actions','file-actions-remaining','canvas-file-delete','note-search','canvas-graph','canvas-graph-remaining','canvas-motion','canvas-view','canvas-view-remaining'), default='all')
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
