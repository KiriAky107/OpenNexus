"""One explicit software touch at the actual owned WebView's Preview button.

Not a physical-device input claim. The original native instrumentation signals
readiness once; neither missing/unknown input nor a lost result is replayed.
"""
import http.client
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from android_auth_fixture import atomic_json
from android_runtime_probe import APP, DRIVER, require, require_hosted_emulator


def touch_original_preview(probe, nonce, original_pid):
    require(str(uuid.UUID(nonce)) == nonce, 'Original preview dispatch is invalid')
    require_hosted_emulator(probe.args.serial)
    probe.wait_owned_connection()
    port = None
    report = {'driver_dispatch_id': nonce, 'original_app_pid': int(original_pid),
              'input_backend': 'Chromium Input.dispatchTouchEvent', 'physical_touch': False,
              'JS_click_or_direct_IPC': False, 'automatic_retry': False}
    failed = None
    try:
        require(probe.shell('pidof', APP).stdout.strip() == original_pid, 'Original preview app process changed')
        port = probe.device('forward', 'tcp:0', 'localabstract:webview_devtools_remote_' + original_pid.decode()).stdout.decode().strip()
        require(re.fullmatch(r'[0-9]{4,5}', port) and 1024 <= int(port) <= 65535, 'Unexpected original preview forwarding port')
        connection = http.client.HTTPConnection('127.0.0.1', int(port), timeout=10)
        try:
            connection.request('GET', '/json/list'); response = connection.getresponse(); body = response.read(1024**2 + 1)
            require(response.status == 200 and len(body) <= 1024**2, 'Original WebView metadata unavailable')
            pages = [x for x in json.loads(body) if x.get('type') == 'page' and x.get('title') == 'OpenNexus']
        finally:
            connection.close()
        require(len(pages) == 1, 'Original WebView page is ambiguous')
        socket = urlsplit(pages[0]['webSocketDebuggerUrl'])
        require(socket.scheme == 'ws' and socket.hostname in ('127.0.0.1', 'localhost') and socket.port == int(port)
                and re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+', socket.path) and not socket.query and not socket.fragment,
                'Unrelated preview WebView endpoint')
        script = Path(__file__).resolve().parent / 'android-runtime/sync_preview_touch.mjs'
        result = probe.command(['node', '--max-old-space-size=128', script,
                                'ws://127.0.0.1:' + port + socket.path, nonce], timeout=25, check=False)
        require(0 < len(result.stdout) <= 16384, 'Original preview input report exceeds bound')
        report['original_dispatch'] = json.loads(result.stdout)
        require(result.returncode == 0 and report['original_dispatch'].get('input_pair_accepted') is True,
                'Original browser input result is unknown or refused; no fallback')
        require(probe.shell('pidof', APP).stdout.strip() == original_pid, 'Original app process changed after input')
    except Exception as error:
        failed = error
        report.update(failed=True, error_class=type(error).__name__, raw_error_returned=False)
    finally:
        if port and re.fullmatch(r'[0-9]{4,5}', port):
            try:
                report['own_forward_removed'] = probe.device('forward', '--remove', 'tcp:' + port, check=False).returncode == 0
            except Exception as error:
                report.update(own_forward_removed=False, cleanup_error_class=type(error).__name__)
        atomic_json(probe.output / 'original-webview-preview-dispatch.json', report, exclusive=True)
    if failed is not None:
        raise RuntimeError('ORIGINAL_WEBVIEW_PREVIEW_INPUT_UNCONFIRMED') from None
    return report


def instrument_original_preview(probe, nonce):
    """Own one original adb process/stream. A ready marker never creates a retry."""
    require(str(uuid.UUID(nonce)) == nonce, 'Original preview dispatch is invalid')
    require_hosted_emulator(probe.args.serial)
    probe.wait_owned_connection()
    original_pid = probe.shell('pidof', APP).stdout.strip()
    require(re.fullmatch(rb'[1-9][0-9]{0,9}', original_pid), 'Original app PID is ambiguous')
    atomic_json(probe.output / 'original-webview-preview-intent.json', {
        'driver_dispatch_id': nonce, 'original_app_pid': int(original_pid),
        'compiled_source': probe.args.commit, 'tools_source': probe.args.tools_commit,
        'input_backend': 'Chromium Input.dispatchTouchEvent', 'physical_touch': False,
        'JS_click_or_direct_IPC': False, 'automatic_retry': False,
        'scope': 'One actual Preview button after this original native instrumentation readiness signal'}, exclusive=True)
    command = [str(probe.adb), '-s', probe.args.serial, 'shell', 'am', 'instrument', '-w', '-e', 'phase', 'associate',
               '-e', 'association_preview', 'webview-touch', '-e', 'preview_dispatch_id', nonce, DRIVER + '/.SyncRuntimeProbe']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ready, finished = threading.Event(), threading.Event()
    state = {'signals': 0, 'overflow': False}; chunks = []
    marker = ('INSTRUMENTATION_STATUS: owned_preview_ready=' + nonce).encode()
    def read_original():
        total = 0
        try:
            while line := process.stdout.readline(65537):
                total += len(line)
                if total > 1024**2 or len(line) > 65536:
                    state['overflow'] = True; break
                chunks.append(line)
                if line.strip() == marker:
                    state['signals'] += 1; ready.set()
        finally:
            finished.set()
    reader = threading.Thread(target=read_original, daemon=True); reader.start()
    deadline = time.monotonic() + 180
    failure = None; dispatched = False
    try:
        while not ready.is_set() and not finished.is_set() and time.monotonic() < deadline:
            ready.wait(.1)
        require(ready.is_set() and state['signals'] == 1 and not state['overflow'] and process.poll() is None,
                'Original native preview readiness was not confirmed')
        dispatched = True
        touch_original_preview(probe, nonce, original_pid)
    except Exception as error:
        failure = type(error).__name__
    finally:
        try:
            process.wait(timeout=max(.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            # Only this original owned adb handle; never create a replacement
            # instrument/UI action after an observation timeout.
            failure = 'OriginalInstrumentationTimeout'
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        reader.join(timeout=5)
        if state['overflow'] or state['signals'] != 1 or reader.is_alive(): failure = 'OriginalStreamUnconfirmed'
        atomic_json(probe.output / 'original-webview-preview-stream.json', {
            'driver_dispatch_id': nonce, 'readiness_signals': state['signals'], 'input_attempted': dispatched,
            'original_process_returncode': process.returncode, 'bounded_stream_complete': not reader.is_alive() and not state['overflow'],
            'failed': failure is not None, 'error_class': failure, 'automatic_retry': False,
            'raw_error_returned': False}, exclusive=True)
    return subprocess.CompletedProcess(command, process.returncode if failure is None else 1, b''.join(chunks), b'')
