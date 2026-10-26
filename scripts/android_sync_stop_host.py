"""Closed original Start/Stop handshake with one live owning instrumentation.

Only testdriver files and the existing owned delivery-control namespace are
used. Driver IDs, delivery markers and GUI facts never substitute for Native
invocation, durable state or PG/S3 evidence. No input/CAS retry or fallback.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from android_auth_fixture import read_json
from android_release_auth_background import ReleaseAuthBackground
from android_release_observation import APP, exclusive
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from android_sync_fixture import arm_control, control_root, selected_target
from android_sync_host_touch import retain_post_input_owner

ENTRY = 'ReleaseSyncStopRuntimeProbe'
STOP, RESUME = 'cycle-stop-original', 'cycle-after-stop-original'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
LIMIT = 1024 * 1024


def record(path, value):
    exclusive(path, (json.dumps(value, separators=(',', ':'), ensure_ascii=False)+'\n').encode())


def closed_json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 4096, 'SYNC_STOP_JSON_BOUND')
    def pairs(items):
        value = dict(items)
        require(len(value) == len(items), 'SYNC_STOP_DUPLICATE_JSON')
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def canonical(value):
    return type(value) is str and re.fullmatch(UUID, value) is not None


def same_fields(value, expected):
    return type(value) is dict and set(value) == set(expected) and all(
        type(value[k]) is type(v) and value[k] == v for k, v in expected.items())


def validate_parent(value, phase):
    require(phase in (STOP, RESUME) and type(value) is dict and set(value) == {
        'schema', 'phase', 'parent_intent_id', 'first_intent_id', 'second_intent_id', 'native_invocation_id_observed'},
        'SYNC_STOP_PARENT_SCOPE')
    require(type(value['schema']) is int and value['schema'] == 1 and value['phase'] == phase
            and value['native_invocation_id_observed'] is False, 'SYNC_STOP_PARENT_METADATA')
    ids = [value['parent_intent_id'], value['first_intent_id']]
    if phase == STOP: ids.append(value['second_intent_id'])
    else: require(value['second_intent_id'] is None, 'SYNC_STOP_RESUME_SECOND_INPUT')
    require(all(canonical(v) for v in ids) and len(set(ids)) == len(ids), 'SYNC_STOP_PARENT_IDS')
    return dict(value)


def validate_ready(value, parent, action, elapsed_ms, uid, window=None):
    phase = parent['phase']; validate_parent(parent, phase)
    allowed = ('cycle-start', 'cycle-stop') if phase == STOP else ('cycle-resume',)
    require(action in allowed and type(value) is dict and set(value) == {
        'schema', 'phase', 'action', 'parent_intent_id', 'driver_intent_id', 'package', 'uid', 'window',
        'original', 'visible', 'x', 'y', 'elapsed_ms'}, 'SYNC_STOP_READY_SCOPE')
    expected = parent['second_intent_id'] if action == 'cycle-stop' else parent['first_intent_id']
    require(type(value['schema']) is int and value['schema'] == 1 and value['phase'] == phase and value['action'] == action
            and value['parent_intent_id'] == parent['parent_intent_id'] and value['driver_intent_id'] == expected
            and value['package'] == APP, 'SYNC_STOP_READY_ORIGINAL_OWNER')
    require(all(type(value[k]) is int for k in ('uid', 'window', 'x', 'y', 'elapsed_ms'))
            and type(uid) is int and 10000 <= uid < 100000 and value['uid'] == uid
            and 0 <= value['window'] <= 65535 and (window is None or value['window'] == window)
            and type(elapsed_ms) is int and 0 <= value['elapsed_ms'] <= elapsed_ms <= value['elapsed_ms']+2000,
            'SYNC_STOP_READY_IDENTITY_OR_CLOCK')
    for key in ('original', 'visible'):
        rect = value[key]
        require(type(rect) is list and len(rect) == 4 and all(type(v) is int and -16384 <= v <= 16384 for v in rect)
                and rect[0] < rect[2] and rect[1] < rect[3]
                and rect[0] <= value['x'] < rect[2] and rect[1] <= value['y'] < rect[3], 'SYNC_STOP_READY_GEOMETRY')
    a, b = value['original'], value['visible']
    require(a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
            and value['x'] == (b[0]+b[2])//2 and value['y'] == (b[1]+b[3])//2
            and value['x'] >= 0 and value['y'] >= 0, 'SYNC_STOP_READY_CENTER')
    return dict(value)


class OriginalHeldRevision:
    """An already armed original hold receipt in one frozen owned vault."""
    def __init__(self, owner, vault, control):
        require(isinstance(owner, Path), 'SYNC_STOP_CONTROL_OWNER')
        require(owner.is_absolute() and owner.is_dir() and not owner.is_symlink() and owner.resolve() == owner,
                'SYNC_STOP_CONTROL_OWNER_ALIAS')
        require(type(vault) is str and re.fullmatch(r'[0-9a-f]{32}', vault), 'SYNC_STOP_CONTROL_VAULT')
        require(type(control) is dict and set(control) == {'operation_id', 'mode', 'expected_revision', 'result_revision'}
                and canonical(control['operation_id']) and control['mode'] == 'hold_revision'
                and type(control['expected_revision']) is int and type(control['result_revision']) is int
                and control['expected_revision'] == 0 and control['result_revision'] == 1,
                'SYNC_STOP_ORIGINAL_HOLD_RECEIPT')
        self.owner, self.vault, self.control = owner, vault, dict(control)
        self.marker = self.release_control = None
        self.release_attempted = False

    def current(self, receipt):
        root = control_root(self.owner)
        require(all((root/name).is_dir() and not (root/name).is_symlink() for name in ('control-receipts', 'fault-events')),
                'SYNC_STOP_CONTROL_NAMESPACE_ALIAS')
        require(same_fields(read_json(root/'target.json'), dict(schema=1,vault_id=self.vault))
                and selected_target(self.owner) == self.vault and same_fields(read_json(root/'control-receipts'/(receipt['operation_id']+'.json')), receipt)
                and same_fields(read_json(root/'control.json'), dict(revision=receipt['result_revision'], operation_id=receipt['operation_id'], mode=receipt['mode'])),
                'SYNC_STOP_CONTROL_OR_TARGET_CHANGED')
        return root

    def marker_json(self, path):
        if not path.exists():
            require(not path.is_symlink(), 'SYNC_STOP_MARKER_SYMLINK'); return None
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4096, 'SYNC_STOP_MARKER_BOUND_OR_ALIAS')
        raw=path.read_bytes()
        if not raw:return None
        try:return closed_json(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):return None

    def read_held(self):
        root = self.current(self.control); path = root/'fault-events'/(self.control['operation_id']+'.json')
        marker = self.marker_json(path)
        if marker is None:return None
        expected = dict(operation_id=self.control['operation_id'], mode='hold_revision', revision=self.control['result_revision'],
                        action='revision', service_status=200, service_response_complete=True, raw_body_recorded=False)
        require(same_fields(marker, expected), 'SYNC_STOP_ORIGINAL_HELD_SERVICE_MARKER')
        require(self.marker is None or self.marker == marker, 'SYNC_STOP_HELD_MARKER_CHANGED')
        self.marker = dict(marker); return marker

    def begin_release(self, output, label):
        require(self.marker is not None and not self.release_attempted, 'SYNC_STOP_RELEASE_NOT_ORIGINAL_OR_REPEATED')
        self.release_attempted = True  # Unknown outcomes cannot re-enter the CAS.
        self.current(self.control)
        operation = str(uuid.uuid4())
        record(output/(label+'-release-control-intent.json'), dict(operation_id=operation, mode='release',
            expected_revision=self.control['result_revision'], held_operation_id=self.control['operation_id'],
            automatic_retry=False, client_receipt_known=False))
        receipt = arm_control(self.owner, operation, 'release', self.control['result_revision'])
        require(same_fields(receipt, dict(operation_id=operation, mode='release', expected_revision=self.control['result_revision'],
                                result_revision=self.control['result_revision']+1)), 'SYNC_STOP_RELEASE_CONTROL_CHANGED')
        record(output/(label+'-release-control-receipt.json'), receipt)
        self.release_control = receipt; return dict(receipt)

    def read_released(self):
        require(self.release_control is not None, 'SYNC_STOP_RELEASE_NOT_RECORDED')
        root = self.current(self.release_control); path = root/'fault-events'/(self.control['operation_id']+'.released.json')
        value = self.marker_json(path)
        if value is None:return None
        require(type(value) is dict and set(value) == {'operation_id', 'original_response_send_attempted', 'ASGI_send_returned', 'client_receipt_known'}
                and value['operation_id'] == self.control['operation_id'] and value['original_response_send_attempted'] is True
                and type(value['ASGI_send_returned']) is bool and value['client_receipt_known'] is False,
                'SYNC_STOP_RELEASE_MARKER_NOT_ORIGINAL')
        return value


def _owner(subject, process, pid):
    require(process.poll() is None and subject.shell('pidof', APP).stdout.strip() == pid
            and ReleaseAuthBackground.app_is_resumed(subject) and subject.observer.observe_identity() == subject.identity,
            'SYNC_STOP_ORIGINAL_OWNER_CHANGED')


def _clock(subject):
    raw = subject.shell('cat', '/proc/uptime').stdout.decode('ascii').split()[0]
    require(re.fullmatch(r'[0-9]+\.[0-9]{1,3}', raw), 'SYNC_STOP_UPTIME_SHAPE')
    # Decimal arithmetic keeps the boundary exact without float rounding.
    whole, frac = raw.split('.')
    return int(whole)*1000+int(frac.ljust(3, '0'))


def _wait(subject, process, pid, deadline, observe):
    last = time.monotonic()
    require(0 <= last <= deadline, 'SYNC_STOP_WAIT_BOUND')
    while True:
        now = time.monotonic()
        require(last <= now <= deadline, 'SYNC_STOP_WAIT_CLOCK_OR_TIMEOUT'); last = now
        _owner(subject, process, pid)
        value = observe()
        if value is not None:
            _owner(subject, process, pid); return value
        time.sleep(.05)


def _device_json(subject, path):
    result = subject.shell('run-as', DRIVER, 'sh', '-c', 'test -f '+path+' && test ! -L '+path+' && cat '+path, check=False)
    require(len(result.stdout) <= 4096 and len(result.stderr) <= 4096, 'SYNC_STOP_PRIVATE_JSON_OUTPUT_BOUND')
    if result.returncode != 0 or not result.stdout: return None
    try: return closed_json(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError): return None  # The same original exclusive write may be partial.


def _signal(subject, label, path):
    command = ('shell', '-T', 'run-as '+DRIVER+' sh -c '+shlex.quote(
        'umask 077; test ! -e '+path+' && test ! -L '+path+' && cat > '+path))
    record(subject.output/(label+'-intent.json'), dict(arguments=list(command), bytes=7, automatic_retry=False))
    try:
        result = subject.device(*command, input=b'finish\n', timeout=5, check=False)
    except Exception as error:
        record(subject.output/(label+'-result.json'), dict(returncode=None, unknown_result=True, error_type=type(error).__name__, automatic_retry=False))
        raise
    require(len(result.stdout) <= 4096 and len(result.stderr) <= 4096, 'SYNC_STOP_SIGNAL_OUTPUT_BOUND')
    exclusive(subject.output/(label+'.stdout'), result.stdout); exclusive(subject.output/(label+'.stderr'), result.stderr)
    record(subject.output/(label+'-result.json'), dict(returncode=result.returncode, automatic_retry=False))
    require(result.returncode == 0, 'SYNC_STOP_SIGNAL_UNKNOWN_NO_RETRY')
    readback = subject.shell('run-as', DRIVER, 'sh', '-c', 'test -f '+path+' && test ! -L '+path+' && cat '+path, check=False)
    require(readback.returncode == 0 and readback.stdout == b'finish\n', 'SYNC_STOP_SIGNAL_READBACK')
    record(subject.output/(label+'-readback.json'), dict(exact_static_bytes=True, bytes=7, private_signal_bytes_exported=False))


def _stdout(path):
    require(path.stat().st_size <= LIMIT, 'SYNC_STOP_INSTRUMENT_STREAM_BOUND')
    value = path.read_bytes()
    require(b'_PHASE_FAILED' not in value and b'INSTRUMENTATION_FAILED' not in value, 'SYNC_STOP_GUI_FAILED')
    return value


def _marker(path, token):
    value = _stdout(path)
    require(value.count(token) <= 1, 'SYNC_STOP_DUPLICATE_GUI_MARKER')
    return True if token in value else None


def run_hosted_stop_cycle(subject, phase, label, *, held=None):
    require(subject.driver_entry == ENTRY and phase in (STOP, RESUME)
            and re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label)
            and (type(held) is OriginalHeldRevision and held.owner == subject.owner if phase == STOP else held is None),
            'SYNC_STOP_HOST_PHASE_OR_CONTROL')
    require_hosted_emulator(subject.args.serial)
    require(subject.identity.get('api') == '36', 'SYNC_STOP_API36_ONLY')
    require(subject.tools.is_dir() and not subject.tools.is_symlink()
            and not subject.tools.resolve().is_relative_to(subject.output.resolve()), 'SYNC_STOP_PRIVATE_STREAMS_NOT_ARTIFACT')
    if phase == STOP:
        require(held.marker is None and not held.release_attempted and held.read_held() is None,
                'SYNC_STOP_ALREADY_HELD_OR_RELEASED_BEFORE_ORIGINAL_INPUT')
    record(subject.output/(label+'-host-phase-intent.json'), dict(phase=phase, entry=ENTRY, automatic_retry=False, native_invocation_id_observed=False))
    streams = subject.tools/'stop-streams'
    require(not streams.is_symlink(), 'SYNC_STOP_PRIVATE_STREAM_ALIAS'); streams.mkdir(exist_ok=True)
    private = streams/label; private.mkdir(exist_ok=False)
    out_path, err_path = private/'stdout', private/'stderr'
    subject.stop()
    subject.original_command(label+'-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', subject.activity))))
    pid = subject.shell('pidof', APP).stdout.strip(); require(re.fullmatch(rb'[1-9][0-9]{0,9}', pid), 'SYNC_STOP_ORIGINAL_PID')
    uid_raw = subject.shell('pm', 'list', 'packages', '-U', APP).stdout
    match = re.fullmatch(rb'package:cc\.kronecker\.opennexus uid:([0-9]{5})\r?\n?', uid_raw)
    require(match is not None, 'SYNC_STOP_INSTALLED_UID'); uid = int(match[1])
    args = ('shell', '-T', shlex.join(('am', 'instrument', '-w', '-e', 'phase', phase, DRIVER+'/.'+ENTRY)))
    record(subject.output/(label+'-instrument-intent.json'), dict(arguments=list(args), automatic_retry=False))
    complete = False; process = None; stage = 'launch-instrumentation'; parent = None; inputs = []
    with out_path.open('xb') as out, err_path.open('xb') as err:
        try:
            process = subprocess.Popen([str(subject.adb), '-s', subject.args.serial, *args], stdout=out, stderr=err)
            stage = 'original-parent'
            parent = validate_parent(_wait(subject, process, pid, time.monotonic()+25,
                lambda:_device_json(subject, 'no_backup/owned-sync-stop-'+phase+'-intent.json')), phase)
            record(subject.output/(label+'-original-parent.json'), parent)
            window = None; previous_elapsed = -1; stop_deadline = None
            actions = ('cycle-start', 'cycle-stop') if phase == STOP else ('cycle-resume',)
            for action in actions:
                action_label = label+'-'+action
                stage = action+'-ready'
                deadline = time.monotonic()+10
                if stop_deadline is not None: deadline = min(deadline, stop_deadline)
                ready = _wait(subject, process, pid, deadline, lambda:_device_json(subject,
                    'no_backup/owned-sync-stop-'+phase+'-'+action+'-ready.json'))
                value = validate_ready(ready, parent, action, _clock(subject), uid, window)
                require(value['elapsed_ms'] >= previous_elapsed, 'SYNC_STOP_ACTION_CLOCK_REVERSED')
                record(subject.output/(action_label+'-ready.json'), value)
                if action == 'cycle-stop': require(held.read_held() is not None, 'SYNC_STOP_HOLD_DISAPPEARED')
                _owner(subject, process, pid)
                elapsed = _clock(subject); validate_ready(value, parent, action, elapsed, uid, window)
                record(subject.output/(action_label+'-owner.json'), dict(pid=int(pid), uid=uid, original_instrumentation_alive=True,
                    same_original_PID=True, app_resumed=True, same_owned_boot_identity=True, observed_ready_age_ms=elapsed-value['elapsed_ms'],
                    ready_age_bound_ms=2000, native_cycle_acknowledged=False))
                stage = action+'-input'
                if action == 'cycle-start': stop_deadline = time.monotonic()+30
                subject.original_command(action_label+'-host-touch', ('shell', '-T', shlex.join(
                    ('input', 'touchscreen', 'tap', str(value['x']), str(value['y'])))), timeout=5)
                retain_post_input_owner(subject, process, pid, action_label)
                if action == 'cycle-start':
                    stage = 'held-original-service-marker'
                    # Hold this first finish until the service marker. Only then
                    # does the driver freeze a fresh, separately settled Stop target.
                    marker = _wait(subject, process, pid, min(time.monotonic()+20, stop_deadline), held.read_held)
                    record(subject.output/(label+'-held-service-marker.json'), marker)
                stage = action+'-finish'
                _signal(subject, action_label+'-finish', 'no_backup/owned-sync-stop-touch-'+value['driver_intent_id']+'.finish')
                # Stop must remain held. An explicit read may already have
                # returned its final GUI marker and exited after this finish.
                if phase == STOP or process.poll() is None:_owner(subject, process, pid)
                window, previous_elapsed = value['window'], value['elapsed_ms']; inputs.append(value['driver_intent_id'])
            if phase == STOP:
                stage = 'original-stopped-GUI'
                _wait(subject, process, pid, min(time.monotonic()+8, stop_deadline), lambda:_marker(out_path,
                    b'OPENNEXUS_SYNC_STOPPED_UI inputs_finished=2 stopped_UI_observed=true release_signal_observed=false native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false'))
                record(subject.output/(label+'-stopped-UI-observed.json'), dict(original_instrumentation_alive=True, stopped_UI_observed=True, native_original_state_verified=False))
                stage = 'release-original-response'
                require(time.monotonic() < stop_deadline, 'SYNC_STOP_RELEASE_TOO_LATE'); _owner(subject, process, pid)
                held.begin_release(subject.output, label)
                marker = _wait(subject, process, pid, time.monotonic()+5, held.read_released)
                record(subject.output/(label+'-original-response-release.json'), marker)
                _owner(subject, process, pid)
                stage = 'same-parent-final-release-signal'
                _signal(subject, label+'-release-finish', 'no_backup/owned-sync-stop-release-'+parent['parent_intent_id']+'.finish')
            stage = 'final-original-GUI'
            deadline = time.monotonic()+10; last = time.monotonic()
            while process.poll() is None:
                now = time.monotonic(); require(last <= now <= deadline, 'SYNC_STOP_FINAL_WAIT_CLOCK_OR_TIMEOUT'); last = now
                _owner(subject, process, pid); _stdout(out_path); time.sleep(.05)
            stdout = _stdout(out_path)
            require(process.returncode == 0 and stdout.count(('OPENNEXUS_RELEASE_SYNC_STOP_PHASE_OK '+phase).encode()) == 1,
                    'SYNC_STOP_FINAL_GUI_NOT_COMPLETE')
            expected = (b'OPENNEXUS_SYNC_STOP_RELEASE_GUARD inputs_finished=2 stopped_UI_observed=true release_signal_observed=true native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false'
                if phase == STOP else b'OPENNEXUS_SYNC_AFTER_STOP_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false')
            require(stdout.count(expected) == 1, 'SYNC_STOP_FINAL_HELD_GUARD_MISSING')
            require(subject.shell('pidof', APP).stdout.strip() == pid and ReleaseAuthBackground.app_is_resumed(subject)
                    and subject.observer.observe_identity() == subject.identity, 'SYNC_STOP_OWNER_CHANGED_AT_FINAL_RESULT')
            require(err_path.stat().st_size <= LIMIT, 'SYNC_STOP_INSTRUMENT_STREAM_BOUND')
            complete = True
            subject.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True, input_method='hosted_adb_touchscreen_tap',
                original_parent_intent_id=parent['parent_intent_id'], original_input_intent_ids=inputs,
                same_live_instrumentation_through_original_release=phase == STOP,
                Native_RAM_invocation_id_observed=False, native_original_state_verified=False, client_receipt_known=False))
        except Exception as error:
            record(subject.output/(label+'-host-failure.json'), dict(stage=stage, error_type=type(error).__name__, automatic_retry=False,
                native_original_state_verified=False, whole_Stop_component_passed=False))
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill(); process.wait(timeout=5)
            for path, name in ((out_path,'stdout'),(err_path,'stderr')):
                size=path.stat().st_size
                if size<=LIMIT:exclusive(subject.output/(label+'-instrument.'+name),path.read_bytes())
                else:record(subject.output/(label+'-instrument-'+name+'-omitted.json'),dict(bytes=size,raw_exported=False))
            record(subject.output/(label+'-instrument-result.json'),dict(returncode=None if process is None else process.returncode,
                unknown_result=not complete, automatic_retry=False, early_cleanup_not_success=not complete))
            subject.stop()
