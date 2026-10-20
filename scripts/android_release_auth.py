"""Normal production HTTPS auth on the unchanged intended-signed successor.

Private provider input/index copies stay outside artifacts. There is no raw UI
capture, debug product run-as, direct IPC, credential export or mutation retry.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import time
from types import SimpleNamespace

from android_auth_fixture import owned_root, read_json, require_hosted
from android_auth_runtime import AuthProbe, active_intent, expect_requests, provider_account
from android_release_auth_observation import StoppedAuthObserver, project_metadata
from android_release_observation import APP, exclusive
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from android_signed_runtime_binding import bind_pair
from android_signed_runtime_inputs import strict_json
from release_plan import atomic_json, digest

PHASES = ('login-a', 'current', 'refresh', 'logout')


def validate_normal_observations(provider, native, sealed):
    require(len(provider) == len(native) == len(sealed) == 4, 'RELEASE_AUTH_NORMAL_PHASE_COUNT')
    for value, counts in zip(provider, ((1, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1))):
        expect_requests(value, *counts)
        require(provider_account(value, 1)['devices'] == [], 'RELEASE_AUTH_FOREIGN_IDENTITY_USED')
    first = provider_account(provider[0], 0)
    require(len(first['devices']) == 1 and first['session_counts'] == [dict(device_id=first['devices'][0]['id'], sessions=1)],
            'RELEASE_AUTH_SINGLE_ORIGINAL_SESSION')
    device = first['devices'][0]['id']
    for value in provider[1:]:
        require(provider_account(value, 0)['devices'] == first['devices'], 'RELEASE_AUTH_DEVICE_CHANGED')
    require(provider_account(provider[2], 0)['session_counts'] == first['session_counts']
            and provider_account(provider[3], 0)['session_counts'] == [dict(device_id=device, sessions=0)],
            'RELEASE_AUTH_SESSION_ROTATION_OR_LOGOUT')
    login = active_intent(native[0], 'login', device)
    require(len(native[0]['intents']) == 1 and len(native[2]['intents']) == 2 and len(native[3]['intents']) == 3
            and native[2]['intents'][0] == native[0]['intents'][0]
            and native[3]['intents'][:2] == native[2]['intents'], 'RELEASE_AUTH_ORIGINAL_INTENTS_PRESERVED')
    require(native[1] == native[0], 'RELEASE_AUTH_COLD_NATIVE_ORIGINAL_CHANGED')
    refreshed = active_intent(native[2], 'refresh', device)
    selected = native[3]['selection']
    require(selected is not None, 'RELEASE_AUTH_LOGOUT_SELECTION_MISSING')
    rows = [v for v in native[3]['intents'] if v['operation_id'] == selected['operation_id']]
    require(len(rows) == 1 and rows[0]['kind'] == 'logout' and rows[0]['phase'] == 'disconnected'
            and rows[0]['server_logout_confirmed'] == 1 and rows[0]['device_id'] == device,
            'RELEASE_AUTH_ORIGINAL_LOGOUT_UNCONFIRMED')
    ids = [login['operation_id'], refreshed['operation_id'], rows[0]['operation_id']]
    require(len(set(ids)) == 3 and refreshed['previous_operation'] == ids[0]
            and rows[0]['previous_operation'] == ids[1], 'RELEASE_AUTH_ORIGINAL_ID_CHAIN')
    require(bool(sealed[0]) and sealed[0] == sealed[1] and set(sealed[2]) == set(sealed[0])
            and sealed[2] != sealed[0] and not sealed[3], 'RELEASE_AUTH_CIPHER_PERSIST_ROTATE_ERASE')
    return dict(original_login_refresh_logout_ids=ids, device=device,
                original_cold_session_and_post_cold_refresh_verified=True,
                ciphertext_persisted_rotated_and_erased_without_export=True)


class ReleaseAuth(ReleaseUpgrade):
    observe = AuthProbe.observe
    stage_owned_input = AuthProbe.stage_owned_input

    def __init__(self, inputs, sdk, java, output, serial='emulator-5562'):
        require_hosted(); require_hosted_emulator(serial)
        self.inputs, self.sdk, self.java, self.output = Path(inputs), Path(sdk), Path(java), Path(output)
        require(not self.output.exists(), 'RELEASE_AUTH_ORIGINAL_OUTPUT_EXISTS')
        self.output.mkdir(parents=True)
        self.tools = self.output.parent / 'release-auth-driver-build'; self.tools.mkdir()
        require(not self.tools.resolve().is_relative_to(self.output.resolve()), 'RELEASE_AUTH_PRIVATE_TOOLS_NOT_ARTIFACT')
        self.args = SimpleNamespace(serial=serial, sdk=self.sdk, scope='release-auth')
        self.adb = self.sdk / 'platform-tools/adb'
        self.original_ime = self.original_hardware_ime = None
        self.owner = owned_root()
        require(read_json(self.owner / 'owner-state.json')['result'] == 'ready', 'RELEASE_AUTH_ORIGIN_NOT_READY')
        self.phases, self.identities, self.observations = [], {}, []
        self.binding = bind_pair(self.inputs, self.sdk, self.java, self.output / 'binary-binding')
        self.manifest = strict_json((self.inputs / 'runtime-inputs.json').read_bytes())
        self.observer = StoppedAuthObserver(self.device, serial, self.tools / 'private-auth-indexes')
        self.identity = self.root = None
        atomic_json(self.output / 'auth-scope-intent.json', dict(install_only='successor', phases=list(PHASES),
            private_auth_indexes_and_input_outside_artifacts=True, ciphertext_bytes_never_read=True,
            raw_UI_and_credentials_never_captured=True, mutation_retry=False))

    def capture(self, label):
        # Intentionally no screenshot/AX/raw logcat, including all failure paths.
        require(re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label), 'RELEASE_AUTH_PUBLIC_FAILURE_LABEL')
        atomic_json(self.output / (label + '-capture-omitted.json'), dict(raw_UI_capture=False, credentials_captured=False))

    def phase(self, phase, label):
        require(phase in PHASES and re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label), 'RELEASE_AUTH_EXACT_PHASE')
        self.stop()
        self.original_command(label + '-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', self.activity))))
        pid = self.shell('pidof', APP).stdout.decode('ascii').strip()
        require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'RELEASE_AUTH_OWN_PROCESS_MISSING')
        try:
            result = self.original_command(label + '-instrument', ('shell', '-T', shlex.join(
                ('am', 'instrument', '-w', '-e', 'phase', phase, DRIVER + '/.ReleaseAuthRuntimeProbe'))), timeout=180)
            require(('OPENNEXUS_RELEASE_AUTH_PHASE_OK ' + phase).encode() in result.stdout
                    and b'_PHASE_FAILED' not in result.stdout and b'INSTRUMENTATION_FAILED' not in result.stdout,
                    'RELEASE_AUTH_ORIGINAL_GUI_FAILED')
            self.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True))
        finally:
            self.stop()

    def snapshot(self, label):
        if self.root is None: self.root = self.observer.discover_auth_root()
        manifest = self.observer.copy_stopped(self.root, label)
        value = project_metadata(self.tools / 'private-auth-indexes' / label, self.tools / ('working-' + label))
        atomic_json(self.output / (label + '-native.json'), value)
        atomic_json(self.output / (label + '-native-origin.json'), dict(identity=manifest['identity'],
            raw_index_hashes_only=manifest['files'], raw_index_uploaded=False,
            only_new_private_working_copy_opened_by_SQLite=True, stopped_checks=self.observer.stopped_checks))
        return value

    def failure_observations(self):
        if self.identity is None: return
        self.stop()
        for label, read in (('provider', lambda: self.observe('failure')), ('native', lambda: self.snapshot('failure')),
                            ('ciphertext-fingerprints', self.observer.ciphertext_fingerprints)):
            try:
                value = read()
                if label == 'ciphertext-fingerprints': atomic_json(self.output / ('failure-' + label + '.json'), value)
            except Exception as error:
                atomic_json(self.output / ('failure-' + label + '-unavailable.json'), dict(error_type=type(error).__name__, mutation_retried=False))

    def run(self):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if self.device('get-state', check=False, timeout=10).stdout.strip() == b'device' and self.shell('getprop', 'sys.boot_completed', check=False).stdout.strip() == b'1': break
            time.sleep(2)
        else: raise RuntimeError('RELEASE_AUTH_OWN_BOOT_TIMEOUT')
        self.identity = self.observer.observe_identity()
        require(self.shell('pm', 'list', 'packages', APP).stdout.decode().splitlines() == [], 'RELEASE_AUTH_FRESH_PROFILE_REQUIRED_NO_CLEAR')
        for setting in ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale'):
            self.shell('settings', 'put', 'global', setting, '0')
        self.shell('input', 'keyevent', 'KEYCODE_WAKEUP'); self.shell('wm', 'dismiss-keyguard')
        self.original_command('install-successor', ('install', '--no-streaming', self.inputs / 'successor.apk'))
        self.installed('successor')
        activities = self.shell('cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', APP).stdout.decode().strip().splitlines()
        require(activities and re.fullmatch(re.escape(APP) + r'/[A-Za-z0-9_.$]+', activities[-1]), 'RELEASE_AUTH_OWN_ACTIVITY')
        self.activity = activities[-1]
        self.build_driver()
        self.stop(); self.observer.start_root_observation()
        require(not self.observer.ciphertext_fingerprints(), 'RELEASE_AUTH_FRESH_CIPHER_PROFILE_REQUIRED')
        configuration = read_json(self.owner / 'device-private.json')
        exclusive(self.tools / 'private-input-intent.json', json.dumps(dict(driver=DRIVER, stdin_only=True, original_once=True)).encode())
        self.stage_owned_input(configuration)
        baseline = self.observe('initial'); expect_requests(baseline, 0, 0, 0)
        require(all(not v['devices'] and not v['session_counts'] for v in baseline['users']), 'RELEASE_AUTH_FRESH_PROVIDER_REQUIRED')
        provider, native, sealed = [], [], []
        for phase, label in zip(PHASES, ('login', 'cold-current', 'refresh', 'logout')):
            self.phase(phase, label)
            provider.append(self.observe(label)); native.append(self.snapshot(label)); sealed.append(self.observer.ciphertext_fingerprints())
            atomic_json(self.output / (label + '-ciphertext-fingerprints.json'), sealed[-1])
        checks = validate_normal_observations(provider, native, sealed)
        require(self.phases[0]['pid'] != self.phases[1]['pid'], 'RELEASE_AUTH_NEW_COLD_PROCESS_REQUIRED')
        result = dict(schema=1, passed=True, kind='android-signed-release-normal-auth', package=APP, abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']), compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs / 'runtime-inputs.json'), installed_binary=self.identities['successor'],
            phases=self.phases, original_checks=checks, actual_default_trusted_HTTPS_and_persisted_session_use=True,
            private_auth_index_or_ciphertext_or_UI_or_credentials_exported=False, only_successor_installed=True,
            device_kind='owned-hosted-emulator', physical_phone_verified=False, background_or_certificate_recovery_verified=False,
            bidirectional_sync_or_worker_verified=False, published=False)
        atomic_json(self.output / 'auth-runtime-proof.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'sdk', 'java', 'output'): parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); probe = None
    try:
        probe = ReleaseAuth(args.inputs, args.sdk, args.java, args.output)
        result = probe.run()
        print(json.dumps({k:result[k] for k in ('kind', 'passed', 'abi', 'physical_phone_verified', 'published')}))
        return 0
    except Exception as error:
        if probe is not None:
            # Never expose exception messages, which may contain provider data.
            atomic_json(probe.output / 'original-failure.json', dict(passed=False, error_type=type(error).__name__, mutation_retried=False))
            try: probe.failure_observations()
            except Exception as observation_error:
                atomic_json(probe.output / 'failure-observation-unavailable.json', dict(error_type=type(observation_error).__name__, mutation_retried=False))
        print('RELEASE_AUTH_FAILED_' + type(error).__name__)
        return 1
    finally:
        if probe is not None and probe.identity is not None:
            try: probe.stop()
            except Exception as cleanup_error:
                atomic_json(probe.output / 'stop-unconfirmed.json', dict(error_type=type(cleanup_error).__name__))
                return 1


if __name__ == '__main__': raise SystemExit(main())
