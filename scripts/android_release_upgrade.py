"""One real signed release update on an exclusive hosted API36 emulator.

Only owned Markdown/Canvas files and their stopped Native ledgers are inspected.
This scope never signs, clears data, uninstalls, logs in, or claims phone coverage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from types import SimpleNamespace

from android_release_observation import APP, StoppedReleaseObserver, exclusive
from android_release_snapshot import inspect_stopped_snapshot, same_originals
from android_runtime_probe import BODY, DRAFT, NODE, DRIVER, Probe, require, require_hosted_emulator
from android_signed_runtime_binding import bind_pair
from android_signed_runtime_inputs import strict_json
from release_plan import atomic_json, digest

FILES = ['release-owned-note.md', 'release-owned-original.canvas']


def installed_identity(raw, listing, expected):
    require(type(raw) is str and type(listing) is str, 'RELEASE_UPGRADE_INSTALLED_METADATA')
    codes = re.findall(r'^\s*versionCode=([0-9]+)\b.*$', raw, re.M)
    names = re.findall(r'^\s*versionName=(\S+)\s*$', raw, re.M)
    uids = re.findall(r'^package:cc\.kronecker\.opennexus uid:([0-9]+)$', listing, re.M)
    require(codes == [str(expected['version_code'])] and names == [expected['version']]
            and len(uids) == 1 and 10000 <= int(uids[0]) < 100000,
            'RELEASE_UPGRADE_INSTALLED_SOURCE_VERSION_OR_UID')
    return dict(package=APP, version=names[0], version_code=int(codes[0]), uid=int(uids[0]))


def original_fixture(projection):
    notes = projection['owned_files']
    require(set(notes) == set(FILES), 'RELEASE_UPGRADE_ORIGINAL_FIXTURE_FILES')
    md, canvas = [notes[name] for name in FILES]
    require(projection['raw_sha256']['vault/' + FILES[0]] == hashlib.sha256(BODY.encode()).hexdigest(),
            'RELEASE_UPGRADE_ORIGINAL_MARKDOWN_BYTES')
    require(len(md['drafts']) == 1 and md['drafts'][0]['content'] == DRAFT
            and md['drafts'][0]['generation'] > md['drafts'][0]['saved_generation'],
            'RELEASE_UPGRADE_ORIGINAL_UNCONFIRMED_DRAFT')
    ids = []
    for item in (md, canvas):
        require(len(item['saves']) == 1 and item['saves'][0]['state'] == 'committed'
                and not item['saves'][0]['error'], 'RELEASE_UPGRADE_ORIGINAL_SINGLE_GUI_SAVE')
        ids.append(item['saves'][0]['operation_id'])
    require(len(set(ids)) == 2, 'RELEASE_UPGRADE_ORIGINAL_DISTINCT_SAVE_IDS')
    return ids


class ReleaseUpgrade:
    command = Probe.command
    device = Probe.device
    shell = Probe.shell
    build_driver = Probe.build_driver
    wait_test_ime = Probe.wait_test_ime
    enable_test_ime = Probe.enable_test_ime
    restore_test_ime = Probe.restore_test_ime
    capture = Probe.capture

    def __init__(self, inputs, sdk, java, output, serial='emulator-5562'):
        require_hosted_emulator(serial)
        self.inputs, self.sdk, self.java, self.output = Path(inputs), Path(sdk), Path(java), Path(output)
        require(not self.output.exists(), 'RELEASE_UPGRADE_ORIGINAL_OUTPUT_EXISTS')
        self.output.mkdir(parents=True)
        self.tools = self.output.parent / 'release-driver-build'
        self.tools.mkdir()
        self.args = SimpleNamespace(serial=serial, sdk=self.sdk, scope='release-upgrade')
        self.adb = self.sdk / 'platform-tools/adb'
        self.original_ime = self.original_hardware_ime = None
        self.phases, self.identities = [], {}
        self.binding = bind_pair(self.inputs, self.sdk, self.java, self.output / 'binary-binding')
        self.manifest = strict_json((self.inputs / 'runtime-inputs.json').read_bytes())
        self.observer = StoppedReleaseObserver(self.device, serial, self.output / 'stopped-originals', FILES)
        self.identity = self.root = None

    def original_command(self, label, args, *, timeout=120):
        exclusive(self.output / (label + '-intent.json'), json.dumps(dict(arguments=[str(v) for v in args],
            automatic_retry=False, package=APP)).encode())
        try:
            result = self.device(*args, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            exclusive(self.output / (label + '.stdout'), error.stdout or b'')
            exclusive(self.output / (label + '.stderr'), error.stderr or b'')
            atomic_json(self.output / (label + '-result.json'), dict(returncode=None, unknown_result=True,
                error_type='TimeoutExpired', automatic_retry=False))
            raise
        exclusive(self.output / (label + '.stdout'), result.stdout)
        exclusive(self.output / (label + '.stderr'), result.stderr)
        atomic_json(self.output / (label + '-result.json'), dict(returncode=result.returncode))
        require(result.returncode == 0, 'RELEASE_UPGRADE_ORIGINAL_COMMAND_FAILED ' + label)
        return result

    def stop(self):
        require(self.observer.observe_identity() == self.identity, 'RELEASE_UPGRADE_AVD_CHANGED')
        self.shell('am', 'force-stop', '--user', '0', APP)

    def installed(self, role):
        item = self.manifest[role]
        paths = self.shell('pm', 'path', APP).stdout.decode('utf-8').splitlines()
        require(len(paths) == 1 and re.fullmatch(r'package:/data/app/[A-Za-z0-9_=~.-]+/cc\.kronecker\.opennexus-[A-Za-z0-9_=~.-]+/base\.apk', paths[0]),
                'RELEASE_UPGRADE_INSTALLED_APK_PATH')
        path = paths[0].removeprefix('package:')
        hashed = self.shell('sha256sum', path).stdout.decode('ascii').split()
        require(hashed == [item['apk']['sha256'], path], 'RELEASE_UPGRADE_INSTALLED_APK_BYTES')
        raw = self.shell('dumpsys', 'package', APP).stdout
        listing = self.shell('pm', 'list', 'packages', '-U', APP).stdout
        exclusive(self.output / (role + '-installed-package.txt'), raw)
        exclusive(self.output / (role + '-installed-owner.txt'), listing)
        identity = installed_identity(raw.decode('utf-8'), listing.decode('ascii'),
                                      self.binding['binaries'][role]['metadata'])
        identity.update(apk_sha256=item['apk']['sha256'], source_commit=item['source_commit'],
                        certificate_sha256=self.manifest['certificate_sha256'], nondebuggable_installed_binary=True)
        self.identities[role] = identity
        return identity

    def phase(self, phase, label):
        require(phase in ('markdown', 'canvas', 'draft', 'release-upgrade-read'), 'RELEASE_UPGRADE_PHASE')
        self.stop()
        launch = self.original_command(label + '-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', self.activity))))
        require(b'Error:' not in launch.stdout, 'RELEASE_UPGRADE_LAUNCH_FAILED')
        pid = self.shell('pidof', APP).stdout.decode('ascii').strip()
        require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'RELEASE_UPGRADE_OWN_PROCESS_MISSING')
        result = self.original_command(label + '-instrument', ('shell', '-T', shlex.join(
            ('am', 'instrument', '-w', '-e', 'phase', phase, DRIVER + '/.ReleaseRuntimeProbe'))), timeout=180)
        self.capture(label)
        require(('OPENNEXUS_RUNTIME_PHASE_OK ' + phase).encode() in result.stdout
                and b'OPENNEXUS_RUNTIME_FAILED' not in result.stdout and b'INSTRUMENTATION_FAILED' not in result.stdout,
                'RELEASE_UPGRADE_GUI_PHASE_FAILED ' + label)
        self.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True))
        self.stop()

    def snapshot(self, label):
        if self.root is None:
            self.root = self.observer.discover_data_root()
        self.observer.copy_stopped(self.root, label)
        projection = inspect_stopped_snapshot(self.output / 'stopped-originals' / label,
                                             self.output / ('working-' + label), FILES)
        atomic_json(self.output / (label + '-projection.json'), projection)
        return projection

    def run(self):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            result = self.device('get-state', check=False, timeout=10)
            if result.stdout.strip() == b'device' and self.shell('getprop', 'sys.boot_completed', check=False).stdout.strip() == b'1':
                break
            time.sleep(2)
        else:
            raise RuntimeError('RELEASE_UPGRADE_OWN_AVD_BOOT_TIMEOUT')
        self.identity = self.observer.observe_identity()
        require(self.shell('pm', 'list', 'packages', APP).stdout.decode().splitlines() == [],
                'RELEASE_UPGRADE_FRESH_PROFILE_REQUIRED_NO_CLEAR_DATA')
        for setting in ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale'):
            self.shell('settings', 'put', 'global', setting, '0')
        self.shell('input', 'keyevent', 'KEYCODE_WAKEUP')
        self.shell('wm', 'dismiss-keyguard')
        self.original_command('install-predecessor', ('install', '--no-streaming', self.inputs / 'predecessor.apk'))
        before_identity = self.installed('predecessor')
        activities = self.shell('cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN',
                                '-c', 'android.intent.category.LAUNCHER', APP).stdout.decode().strip().splitlines()
        require(activities and re.fullmatch(re.escape(APP) + r'/[A-Za-z0-9_.$]+', activities[-1]),
                'RELEASE_UPGRADE_OWN_LAUNCH_ACTIVITY')
        self.activity = activities[-1]
        self.build_driver()
        self.enable_test_ime()
        try:
            self.phase('markdown', 'predecessor-markdown')
            self.phase('canvas', 'predecessor-canvas')
            self.phase('draft', 'predecessor-draft')
            self.observer.start_root_observation()
            before = self.snapshot('before-update')
            ids = original_fixture(before)
            canvas = (self.output / 'stopped-originals/before-update/vault' / FILES[1]).read_text('utf-8')
            require(NODE in canvas, 'RELEASE_UPGRADE_ORIGINAL_CANVAS_TEXT')
            self.original_command('update-successor', ('install', '--no-streaming', '-r', self.inputs / 'successor.apk'))
            after_identity = self.installed('successor')
            require(after_identity['uid'] == before_identity['uid'], 'RELEASE_UPGRADE_UID_CHANGED')
            self.stop()
            unlaunched = self.snapshot('after-update-before-launch')
            same_originals(before, unlaunched)
            self.phase('release-upgrade-read', 'successor-first-read')
            first = self.snapshot('after-first-read')
            same_originals(before, first)
            self.phase('release-upgrade-read', 'successor-cold-read')
            cold = self.snapshot('after-cold-read')
            same_originals(before, cold)
            require(self.phases[-1]['pid'] != self.phases[-2]['pid'], 'RELEASE_UPGRADE_COLD_PROCESS_NOT_NEW')
            result = dict(schema=1, passed=True, kind='android-real-signed-release-upgrade', package=APP, abi='x86_64',
                tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']),
                manifest_sha256=digest(self.inputs / 'runtime-inputs.json'), installed_binaries=self.identities,
                phases=self.phases, original_GUI_save_ids=ids, original_data_and_drafts_preserved=True,
                package_manager_update_without_clear_data=True, device_kind='owned-hosted-emulator',
                normal_phone_root_or_permissions_claimed=False, physical_phone_verified=False,
                media_or_export_or_network_or_credentials_verified=False, published=False)
            atomic_json(self.output / 'upgrade-proof.json', result)
            return result
        finally:
            self.restore_test_ime()
            self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'sdk', 'java', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    probe = ReleaseUpgrade(args.inputs, args.sdk, args.java, args.output)
    try:
        result = probe.run()
    except Exception as error:
        atomic_json(args.output / 'original-failure.json', dict(passed=False, error_type=type(error).__name__, message=str(error)[:2000]))
        probe.capture('original-failure')
        raise
    print(json.dumps({k: result[k] for k in ('kind', 'passed', 'abi', 'physical_phone_verified', 'published')}))


if __name__ == '__main__': main()
