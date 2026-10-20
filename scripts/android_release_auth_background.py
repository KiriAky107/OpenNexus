"""One same-process Home transition before the original held login is released."""
import json
import os
import re
import shlex
import subprocess
import time

from android_auth_fixture import read_json
from android_auth_runtime import AuthProbe, active_intent, expect_requests, provider_account
from android_release_auth import ReleaseAuth, main
from android_release_observation import APP, exclusive
from android_runtime_probe import DRIVER, require
from release_plan import atomic_json, digest

PHASES = ('login-a', 'login-b-held', 'current-unusable')


def validate_background(provider, native, cipher, hold, release, marker, delivered, transition):
    require(len(provider) == 4 and len(native) == len(cipher) == 3, 'RELEASE_BACKGROUND_OBSERVATION_COUNT')
    operation = hold['operation_id']
    require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', operation)
            and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', release['operation_id'])
            and operation != release['operation_id'] and hold['mode'] == 'hold_login' and release['mode'] == 'release'
            and hold['result_revision'] == hold['expected_revision'] + 1
            and release['expected_revision'] == hold['result_revision']
            and release['result_revision'] == release['expected_revision'] + 1, 'RELEASE_BACKGROUND_ORIGINAL_CONTROL_CHAIN')
    require(marker == dict(operation_id=operation, mode='hold_login', revision=hold['result_revision'], action='login',
                           service_status=200, service_response_complete=True, raw_body_recorded=False)
            and delivered == dict(operation_id=operation, original_response_send_attempted=True,
                                  ASGI_send_returned=True, client_receipt_known=False), 'RELEASE_BACKGROUND_ORIGINAL_RESPONSE')
    expect_requests(provider[0], 1, 0, 0)
    require(not provider_account(provider[0], 1)['devices'], 'RELEASE_BACKGROUND_FRESH_SECOND_ACCOUNT')
    for value in provider[1:]: expect_requests(value, 2, 0, 0)
    for index in (0, 1):
        account = provider_account(provider[1], index)
        require(len(account['devices']) == 1 and account['session_counts'] == [dict(device_id=account['devices'][0]['id'], sessions=1)]
                and all(provider_account(v, index) == account for v in provider[2:]), 'RELEASE_BACKGROUND_ONE_DEVICE_SESSION')
    require(provider_account(provider[0], 0) == provider_account(provider[1], 0)
            and provider[1]['requests'] == provider[2]['requests'] == provider[3]['requests']
            and provider[1]['requests']['events'][-1] == dict(action='login', service_status=200, delivery_status=None, fault_operation=operation),
            'RELEASE_BACKGROUND_REQUEST_REPLAY_OR_PRIOR_DEVICE_CHANGE')
    first = active_intent(native[0], 'login', provider_account(provider[0], 0)['devices'][0]['id'])
    require(len(native[0]['intents']) == 1 and len(native[1]['intents']) == 2
            and native[1]['intents'][0] == first and native[1]['selection'] is not None,
            'RELEASE_BACKGROUND_ORIGINAL_INTENTS')
    pending = native[1]['intents'][1]
    require(pending['operation_id'] == native[1]['selection']['operation_id'] and pending['operation_id'] != first['operation_id']
            and pending['kind'] == 'login' and pending['phase'] in ('login_pending', 'unknown')
            and pending['device_id'] is None and pending['account'] == provider_account(provider[1], 1)['username'],
            'RELEASE_BACKGROUND_LATE_LOGIN_ACTIVATED')
    expected = json.loads(json.dumps(native[1])); expected['intents'][1]['phase'] = 'unknown'
    require(native[2] == expected and bool(cipher[0]) and cipher[0] == cipher[1] == cipher[2],
            'RELEASE_BACKGROUND_ORIGINAL_ID_OR_CIPHER_CHANGED')
    require(type(transition['pid']) is int and transition['pid'] > 0
            and transition['original_control_id'] == operation
            and all(transition[k] is True for k in ('same_original_app_process', 'actual_foreground_before_Home',
                'actual_background_after_Home', 'same_UI_instrumentation_alive_until_after_original_release'))
            and transition['private_auth_observed_only_after_stop'] is True, 'RELEASE_BACKGROUND_NOT_ACTUAL_SAME_PROCESS_HOME')
    return dict(original_login_id=first['operation_id'], original_held_login_id=pending['operation_id'],
                hold_control_id=operation, release_control_id=release['operation_id'], original_B_record_cold_unknown_only=True,
                old_A_ciphertext_unchanged_without_activation=True, same_process_Home_before_original_response_release=True)


class ReleaseAuthBackground(ReleaseAuth):
    allowed_phases = PHASES
    driver_entry = 'ReleaseAuthBackgroundRuntimeProbe'
    driver_namespace = 'RELEASE_AUTH_BACKGROUND'
    control = AuthProbe.control

    def finish_held_driver(self):
        # Publishing the destination with `cat > finish` exposes a zero-length
        # file to the driver's existence check before cat writes its bytes.
        # Complete and validate one fresh staging file, then link it atomically
        # without replacing any existing original signal.
        temporary = 'no_backup/owned-auth-held.finish.tmp'
        final = 'no_backup/owned-auth-held.finish'
        script = ('umask 077; test ! -e ' + temporary + ' && test ! -e ' + final +
                  ' && cat > ' + temporary + ' && test "$(stat -c %s ' + temporary + ')" = 7' +
                  ' && test "$(cat ' + temporary + ')" = finish && ln ' + temporary + ' ' + final + ' && rm ' + temporary)
        args = ('shell', '-T', shlex.join(('run-as', DRIVER, 'sh', '-c', script)))
        atomic_json(self.output / 'held-finish-intent.json', dict(arguments=list(args), input_bytes=7,
            original_once=True, automatic_retry=False, complete_before_atomic_publish=True))
        try:
            result = self.device(*args, input=b'finish\n', timeout=15, check=False)
        except subprocess.TimeoutExpired:
            atomic_json(self.output / 'held-finish-result.json', dict(returncode=None, unknown_result=True, automatic_retry=False))
            raise
        atomic_json(self.output / 'held-finish-result.json', dict(returncode=result.returncode, unknown_result=False, automatic_retry=False))
        require(result.returncode == 0, 'RELEASE_BACKGROUND_ORIGINAL_FINISH_PUBLISH_FAILED')
        observed = self.shell('run-as', DRIVER, 'cat', final, check=False)
        require(observed.returncode == 0 and observed.stdout == b'finish\n', 'RELEASE_BACKGROUND_ORIGINAL_FINISH_READBACK')
        atomic_json(self.output / 'held-finish-readback.json', dict(expected_static_bytes_observed=True, bytes=7,
            raw_product_or_credential_file_read=False))

    def app_is_resumed(self):
        # Only reduce the private raw activity dump to a boolean; never persist it.
        raw = self.shell('dumpsys', 'activity', 'activities').stdout
        require(len(raw) <= 4 * 1024**2, 'RELEASE_BACKGROUND_ACTIVITY_BOUND')
        return any(APP + '/' in line and ('topResumedActivity' in line or 'mResumedActivity' in line)
                   for line in raw.decode('utf-8', 'strict').splitlines())

    def held_background(self, held_id):
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', held_id), 'RELEASE_BACKGROUND_CONTROL_ID')
        self.stop()
        self.original_command('held-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', self.activity))))
        args = ('shell', '-T', shlex.join(('am', 'instrument', '-w', '-e', 'phase', 'login-b-held', DRIVER + '/.' + self.driver_entry)))
        atomic_json(self.output / 'held-instrument-intent.json', dict(arguments=list(args), automatic_retry=False))
        process = subprocess.Popen([str(self.adb), '-s', self.args.serial, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        complete = False; stdout = stderr = b''
        try:
            deadline = time.monotonic() + 35
            while True:
                require(process.poll() is None, 'RELEASE_BACKGROUND_DRIVER_ENDED_EARLY')
                ready = self.shell('run-as', DRIVER, 'cat', 'no_backup/owned-auth-held.ready', check=False)
                if ready.returncode == 0 and ready.stdout == b'ready\n' and (self.owner / 'fault-events' / (held_id + '.json')).is_file(): break
                require(time.monotonic() < deadline, 'RELEASE_BACKGROUND_ORIGINAL_HELD_NOT_READY'); time.sleep(.2)
            held = self.observe('held-before-home'); expect_requests(held, 2, 0, 0)
            require(self.observer.observe_identity() == self.identity, 'RELEASE_BACKGROUND_OWNER_CHANGED')
            pid = self.shell('pidof', APP).stdout.strip()
            require(re.fullmatch(rb'[1-9][0-9]{0,9}', pid) and self.app_is_resumed(), 'RELEASE_BACKGROUND_NOT_FOREGROUND')
            self.original_command('held-home', ('shell', '-T', 'input keyevent KEYCODE_HOME'))
            time.sleep(1)
            require(self.shell('pidof', APP).stdout.strip() == pid and not self.app_is_resumed()
                    and process.poll() is None and self.observer.observe_identity() == self.identity,
                    'RELEASE_BACKGROUND_HOME_PROCESS_OR_DRIVER_CHANGED')
            release_id = self.control('release'); deadline = time.monotonic() + 10
            released_path = self.owner / 'fault-events' / (held_id + '.released.json')
            while not released_path.is_file():
                require(process.poll() is None and time.monotonic() < deadline, 'RELEASE_BACKGROUND_RELEASE_NOT_OBSERVED'); time.sleep(.1)
            # Let the original callback reach the already-backgrounded WebView while this same instrumentation is alive.
            time.sleep(1)
            require(process.poll() is None and self.shell('pidof', APP).stdout.strip() == pid and not self.app_is_resumed(),
                    'RELEASE_BACKGROUND_ENDED_BEFORE_ORIGINAL_DELIVERY')
            transition = dict(pid=int(pid), same_original_app_process=True, actual_foreground_before_Home=True,
                actual_background_after_Home=True, same_UI_instrumentation_alive_until_after_original_release=True,
                private_auth_observed_only_after_stop=True, original_control_id=held_id)
            atomic_json(self.output / 'held-background-transition.json', transition)
            atomic_json(self.output / 'original-held-marker.json', read_json(self.owner / 'fault-events' / (held_id + '.json')))
            atomic_json(self.output / 'original-held-released-marker.json', read_json(released_path))
            # Preserve the already-observed original release even if this last
            # test-driver acknowledgement fails. It must not trigger a replay.
            self.finish_held_driver()
            stdout, stderr = process.communicate(timeout=30); complete = True
            require(process.returncode == 0 and b'OPENNEXUS_RELEASE_AUTH_BACKGROUND_PHASE_OK login-b-held' in stdout
                    and b'_PHASE_FAILED' not in stdout and b'INSTRUMENTATION_FAILED' not in stdout, 'RELEASE_BACKGROUND_ORIGINAL_GUI_FAILED')
            self.phases.append(dict(phase='login-b-held', label='held-background', pid=int(pid), passed=True))
            return held, release_id, transition
        finally:
            if not complete:
                if process.poll() is None: process.terminate()
                try: stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); stdout, stderr = process.communicate(timeout=10)
            # Static driver diagnostics only; no raw UI or provider payload enters these pipes.
            if len(stdout) <= 1024**2 and len(stderr) <= 1024**2:
                exclusive(self.output / 'held-instrument.stdout', stdout); exclusive(self.output / 'held-instrument.stderr', stderr)
            atomic_json(self.output / 'held-instrument-result.json', dict(returncode=process.returncode, unknown_result=not complete, automatic_retry=False))
            self.stop()

    def run(self):
        self.prepare(); self.phase('login-a', 'bootstrap-login')
        provider = [self.observe('bootstrap-login')]; native = [self.snapshot('bootstrap-login')]
        cipher = [self.observer.ciphertext_fingerprints()]
        atomic_json(self.output / 'bootstrap-login-ciphertext-fingerprints.json', cipher[-1])
        held_id = self.control('hold_login')
        held, release_id, transition = self.held_background(held_id); provider.append(held)
        provider.append(self.observe('after-release')); native.append(self.snapshot('after-release')); cipher.append(self.observer.ciphertext_fingerprints())
        atomic_json(self.output / 'after-release-ciphertext-fingerprints.json', cipher[-1])
        self.phase('current-unusable', 'cold-unknown')
        provider.append(self.observe('cold-unknown')); native.append(self.snapshot('cold-unknown')); cipher.append(self.observer.ciphertext_fingerprints())
        atomic_json(self.output / 'cold-unknown-ciphertext-fingerprints.json', cipher[-1])
        hold = read_json(self.owner / 'control-receipts' / (held_id + '.json'))
        release = read_json(self.owner / 'control-receipts' / (release_id + '.json'))
        checks = validate_background(provider, native, cipher, hold, release, read_json(self.output / 'original-held-marker.json'),
                                     read_json(self.output / 'original-held-released-marker.json'), transition)
        require(self.phases[1]['pid'] != self.phases[2]['pid'], 'RELEASE_BACKGROUND_NEW_COLD_PROCESS')
        result = dict(schema=1, passed=True, kind='android-signed-release-background-auth', package=APP, abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']), compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs / 'runtime-inputs.json'), installed_binary=self.identities['successor'],
            phases=self.phases, original_checks=checks, actual_default_trusted_HTTPS_same_process_background_before_original_release=True,
            pre_Home_native_index_or_cipher_not_observed=True, private_auth_index_or_ciphertext_or_UI_or_credentials_exported=False,
            device_kind='owned-hosted-emulator', physical_phone_verified=False, certificate_rejection_verified=False,
            bidirectional_sync_or_worker_verified=False, published=False)
        atomic_json(self.output / 'auth-background-runtime-proof.json', result)
        return result


if __name__ == '__main__': raise SystemExit(main(ReleaseAuthBackground))
