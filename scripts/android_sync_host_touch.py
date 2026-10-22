"""One owner-bound system touch at the closed driver's observed cycle button."""
import json
import re
import shlex
import subprocess
import time

from android_release_auth_background import ReleaseAuthBackground
from android_release_observation import APP, exclusive
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from release_plan import atomic_json

UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'


def validate_ready(value, phase, elapsed_ms):
    require(phase in ('cycle', 'cycle-incoming') and isinstance(value, dict)
            and set(value) == {'driver_intent_id', 'phase', 'package', 'window', 'original', 'visible', 'x', 'y', 'elapsed_ms'},
            'SYNC_HOST_TOUCH_READY_SCOPE')
    require(value['phase'] == phase and value['package'] == APP
            and isinstance(value['driver_intent_id'], str) and re.fullmatch(UUID, value['driver_intent_id']),
            'SYNC_HOST_TOUCH_READY_OWNER')
    require(all(type(value[k]) is int for k in ('window', 'x', 'y', 'elapsed_ms')) and value['window'] >= 0
            and 0 <= value['elapsed_ms'] <= elapsed_ms <= value['elapsed_ms'] + 2000,
            'SYNC_HOST_TOUCH_READY_CLOCK')
    for name in ('original', 'visible'):
        r = value[name]
        require(isinstance(r, list) and len(r) == 4 and all(type(n) is int and -16384 <= n <= 16384 for n in r)
                and r[0] < r[2] and r[1] < r[3] and r[0] <= value['x'] < r[2] and r[1] <= value['y'] < r[3],
                'SYNC_HOST_TOUCH_READY_GEOMETRY')
    a, b = value['original'], value['visible']
    require(a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
            and value['x'] == (b[0] + b[2]) // 2 and value['y'] == (b[1] + b[3]) // 2
            and value['x'] >= 0 and value['y'] >= 0, 'SYNC_HOST_TOUCH_READY_CENTER')
    return dict(value)


def run_hosted_cycle(subject, phase, label):
    require(phase in ('cycle', 'cycle-incoming') and re.fullmatch('[a-z][a-z0-9-]{0,47}', label), 'SYNC_HOST_TOUCH_PHASE')
    require_hosted_emulator(subject.args.serial)
    # Exclusive journal precedes launch and every input. A second invocation
    # cannot replay this original intent, including after an unknown result.
    exclusive(subject.output / (label + '-host-touch-intent.json'), json.dumps(dict(
        phase=phase, input_method='hosted_adb_touchscreen_tap', automatic_retry=False)).encode())
    subject.stop()
    subject.original_command(label + '-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', subject.activity))))
    pid = subject.shell('pidof', APP).stdout.strip()
    require(re.fullmatch(rb'[1-9][0-9]{0,9}', pid), 'SYNC_HOST_TOUCH_ORIGINAL_PID')
    args = ('shell', '-T', shlex.join(('am', 'instrument', '-w', '-e', 'phase', phase, DRIVER + '/.' + subject.driver_entry)))
    atomic_json(subject.output / (label + '-instrument-intent.json'), dict(arguments=list(args), automatic_retry=False))
    process = subprocess.Popen([str(subject.adb), '-s', subject.args.serial, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    complete = False; stdout = stderr = b''
    try:
        deadline = time.monotonic() + 35
        path = 'no_backup/owned-sync-touch-ready-' + phase + '-intent.json'
        while True:
            require(process.poll() is None, 'SYNC_HOST_TOUCH_DRIVER_ENDED_BEFORE_READY')
            ready = subject.shell('run-as', DRIVER, 'sh', '-c', 'test -f ' + path + ' && test ! -L ' + path + ' && cat ' + path, check=False)
            require(len(ready.stdout) <= 4096, 'SYNC_HOST_TOUCH_READY_BOUND')
            value = None
            if ready.returncode == 0:
                try: value = json.loads(ready.stdout)
                except (ValueError, UnicodeDecodeError): pass  # One original write may still be incomplete.
            if value is not None: break
            require(time.monotonic() < deadline, 'SYNC_HOST_TOUCH_READY_TIMEOUT'); time.sleep(.1)
        clock = subject.shell('cat', '/proc/uptime').stdout.decode('ascii').split()[0]
        require(re.fullmatch(r'[0-9]+\.[0-9]{1,3}', clock), 'SYNC_HOST_TOUCH_UPTIME_SHAPE')
        value = validate_ready(value, phase, int(float(clock) * 1000))
        atomic_json(subject.output / (label + '-host-touch-ready.json'), value)
        require(process.poll() is None and subject.shell('pidof', APP).stdout.strip() == pid
                and ReleaseAuthBackground.app_is_resumed(subject) and subject.observer.observe_identity() == subject.identity,
                'SYNC_HOST_TOUCH_FOREGROUND_OWNER_CHANGED')
        final_clock = subject.shell('cat', '/proc/uptime').stdout.decode('ascii').split()[0]
        require(re.fullmatch(r'[0-9]+\.[0-9]{1,3}', final_clock), 'SYNC_HOST_TOUCH_FINAL_UPTIME_SHAPE')
        elapsed_ms = int(float(final_clock) * 1000)
        validate_ready(value, phase, elapsed_ms)
        atomic_json(subject.output / (label + '-host-touch-owner.json'), dict(pid=int(pid), same_original_PID=True,
            original_instrumentation_alive=True, app_resumed=True, ready_age_bound_ms=2000,
            observed_ready_age_ms=elapsed_ms-value['elapsed_ms'], input_method='hosted_adb_touchscreen_tap'))
        # A standard OS touchscreen command, never a WebView or product IPC.
        subject.original_command(label + '-host-touch', ('shell', '-T', shlex.join(
            ('input', 'touchscreen', 'tap', str(value['x']), str(value['y'])))), timeout=10)
        finish = 'no_backup/owned-sync-touch-' + value['driver_intent_id'] + '.finish'
        command = ('shell', '-T', 'run-as ' + DRIVER + ' sh -c ' + shlex.quote(
            'umask 077; test ! -e ' + finish + ' && test ! -L ' + finish + ' && cat > ' + finish))
        atomic_json(subject.output / (label + '-host-touch-finish-intent.json'), dict(arguments=list(command), bytes=7, automatic_retry=False))
        result = subject.device(*command, input=b'finish\n', check=False)
        require(len(result.stdout) <= 4096 and len(result.stderr) <= 4096, 'SYNC_HOST_TOUCH_FINISH_OUTPUT_BOUND')
        exclusive(subject.output / (label + '-host-touch-finish.stdout'), result.stdout)
        exclusive(subject.output / (label + '-host-touch-finish.stderr'), result.stderr)
        atomic_json(subject.output / (label + '-host-touch-finish-result.json'), dict(returncode=result.returncode, automatic_retry=False))
        require(result.returncode == 0, 'SYNC_HOST_TOUCH_FINISH_FAILED')
        readback = subject.shell('run-as', DRIVER, 'cat', finish, check=False)
        require(readback.returncode == 0 and readback.stdout == b'finish\n', 'SYNC_HOST_TOUCH_FINISH_READBACK')
        atomic_json(subject.output / (label + '-host-touch-finish-readback.json'), dict(exact_static_bytes=True, bytes=7))
        stdout, stderr = process.communicate(timeout=40); complete = True
        require(process.returncode == 0 and ('OPENNEXUS_RELEASE_SYNC_PHASE_OK ' + phase).encode() in stdout
                and b'_PHASE_FAILED' not in stdout and b'INSTRUMENTATION_FAILED' not in stdout, 'SYNC_HOST_TOUCH_ORIGINAL_GUI_FAILED')
        subject.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True, input_method='hosted_adb_touchscreen_tap'))
    finally:
        if not complete:
            if process.poll() is None: process.terminate()
            try: stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); stdout, stderr = process.communicate(timeout=10)
        if len(stdout) <= 1024**2 and len(stderr) <= 1024**2:
            exclusive(subject.output / (label + '-instrument.stdout'), stdout); exclusive(subject.output / (label + '-instrument.stderr'), stderr)
        atomic_json(subject.output / (label + '-instrument-result.json'), dict(returncode=process.returncode, unknown_result=not complete, automatic_retry=False))
        subject.stop()
