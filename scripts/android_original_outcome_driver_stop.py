"""Quiesce only the captured test-driver process before a new cold UI phase."""
import json
import re

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_runtime_probe import APP, DRIVER
import android_original_outcome_process as original


def absent(result):
    return result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip()


def original_app_exited(proof, pid):
    require(type(proof) is dict and proof.get('original_pid') == pid
            and proof.get('original_pid_before_stop') == pid and proof.get('force_stop_returncode') == 0
            and proof.get('original_pidof_returncode') == 1
            and proof.get('original_pidof_stdout_empty') is True and proof.get('original_pidof_stderr_empty') is True
            and proof.get('owned_emulator_still_connected') is True
            and proof.get('same_original_immutable_installed_app') is True,
            'OUTCOME_DRIVER_ORIGINAL_APP_EXIT_UNPROVEN')


def driver_intent(context, manifest, driver_pid):
    return {'context': context, 'nonce': manifest['nonce'], 'fingerprint': manifest['fingerprint'],
            'original_app_pid': manifest['original_pid'], 'original_driver_pid': driver_pid,
            'driver_package': DRIVER, 'packages': manifest['packages'], 'device': manifest['device'],
            'automatic_retry': False, 'new_cold_phase_not_started': True}


def reconcile_original_driver(probe, context, manifest, driver_pid, label):
    root = original.scope(probe, context, manifest)
    require(type(driver_pid) is int and driver_pid > 0 and driver_pid != manifest['original_pid']
            and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', label), 'OUTCOME_DRIVER_ORIGINAL_REFUSED')
    path = root / ('original-driver-stop-observation-' + label + '.json')
    require(not path.exists(), 'OUTCOME_DRIVER_OBSERVATION_EXISTS')
    intent = root / 'original-driver-stop-intent.json'
    require(intent.is_file() and not intent.is_symlink() and intent.stat().st_size <= 4096
            and json.loads(intent.read_text('utf-8')) == driver_intent(context, manifest, driver_pid),
            'OUTCOME_DRIVER_ORIGINAL_INTENT_CHANGED')
    command_path = root / 'original-driver-stop-command.json'
    require(command_path.is_file() and not command_path.is_symlink() and command_path.stat().st_size <= 1024,
            'OUTCOME_DRIVER_COMMAND_UNCONFIRMED')
    command = json.loads(command_path.read_text('utf-8'))
    require(type(command) is dict and set(command) == {'nonce', 'outcome', 'returncode'}
            and command['nonce'] == manifest['nonce'] and command['outcome'] in ('confirmed', 'unknown', 'already-absent')
            and ((command['outcome'] == 'confirmed' and type(command['returncode']) is int)
                 or (command['outcome'] != 'confirmed' and command['returncode'] is None)),
            'OUTCOME_DRIVER_COMMAND_UNCONFIRMED')
    original.same_original(probe, manifest)
    app = probe.shell('pidof', APP, check=False)
    driver = probe.shell('pidof', DRIVER, check=False)
    state = probe.device('get-state', check=False)
    connected = state.returncode == 0 and state.stdout.strip() == b'device' and not state.stderr.strip()
    confirmed = command['outcome'] == 'already-absent' or (command['outcome'] == 'confirmed' and command['returncode'] == 0)
    value = {'nonce': manifest['nonce'], 'original_app_pid': manifest['original_pid'],
             'original_driver_pid': driver_pid, 'same_original_device_packages_UIDs': True,
             'original_app_absence_observed': absent(app), 'original_driver_absence_observed': absent(driver),
             'owned_emulator_still_connected': connected, 'command_outcome': command['outcome'],
             'force_stop_returncode': command['returncode'], 'observation_sent_no_input': True,
             'cold_original_entry_permitted': confirmed and absent(app) and absent(driver) and connected,
             'original_instrumentation_UI_success_claimed': False}
    atomic_json(path, value, exclusive=True)
    return value


def stop_original_driver_once(probe, context, manifest, driver_pid, native_exit, bridge_exit):
    root = original.scope(probe, context, manifest)
    require(type(driver_pid) is int and driver_pid > 0 and driver_pid != manifest['original_pid'],
            'OUTCOME_DRIVER_ORIGINAL_REFUSED')
    require(not (root / 'original-driver-stop-intent.json').exists(), 'OUTCOME_DRIVER_STOP_ALREADY_REQUESTED')
    original_app_exited(native_exit, manifest['original_pid'])
    require(type(bridge_exit) is dict and bridge_exit.get('event') == 'original-exited'
            and bridge_exit.get('nonce') == manifest['nonce'] and bridge_exit.get('original_pid') == manifest['original_pid']
            and bridge_exit.get('original_callback_consumed') is False
            and bridge_exit.get('Debugger_resume_sent') is False,
            'OUTCOME_DRIVER_ORIGINAL_PAUSE_UNCONFIRMED')
    actual = original.reconcile_original_stop(probe, context, manifest, 'before-driver-stop')
    require(actual['original_exit_proof'] == native_exit and actual['original_absence_observed'],
            'OUTCOME_DRIVER_ORIGINAL_APP_EXIT_UNPROVEN')
    before = probe.shell('pidof', DRIVER, check=False)
    already = absent(before)
    require(already or (before.returncode == 0 and before.stdout.strip() == str(driver_pid).encode('ascii')
                       and not before.stderr.strip()), 'OUTCOME_DRIVER_PID_CHANGED')
    atomic_json(root / 'original-driver-stop-intent.json', driver_intent(context, manifest, driver_pid), exclusive=True)
    if already:
        command = {'nonce': manifest['nonce'], 'outcome': 'already-absent', 'returncode': None}
    else:
        try:
            result = probe.shell('am', 'force-stop', DRIVER, check=False)
            require(type(result.returncode) is int and not result.stderr.strip(), 'OUTCOME_DRIVER_STOP_UNCONFIRMED')
            command = {'nonce': manifest['nonce'], 'outcome': 'confirmed', 'returncode': result.returncode}
        except Exception:
            command = {'nonce': manifest['nonce'], 'outcome': 'unknown', 'returncode': None}
    atomic_json(root / 'original-driver-stop-command.json', command, exclusive=True)
    return reconcile_original_driver(probe, context, manifest, driver_pid, 'first')
