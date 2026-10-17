"""Owned stdio parent for the original debugger; never performs UI or IPC."""
import http.client
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
from urllib.parse import urlsplit

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_runtime_probe import APP
import android_original_outcome_process as original
from android_original_outcome_driver_stop import original_app_exited


def original_pages(port):
    connection = http.client.HTTPConnection('127.0.0.1', int(port), timeout=5)
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse(); body = response.read(1024**2+1)
        require(response.status == 200 and 0 < len(body) <= 1024**2, 'OUTCOME_ORIGINAL_PAGE_UNAVAILABLE')
        return json.loads(body)
    finally:
        connection.close()


class OriginalCompletionBridge:
    def __init__(self, probe, context, manifest, expected_hash, baseline, *, factory=None, metadata=None):
        original.scope(probe, context, manifest)
        require(type(expected_hash) is str and re.fullmatch(r'[0-9a-f]{64}', expected_hash), 'OUTCOME_ORIGINAL_HASH_REFUSED')
        original.same_original(probe, manifest)
        pid = probe.shell('pidof', APP, check=False)
        require(pid.returncode == 0 and not pid.stderr.strip()
                and pid.stdout.strip() == str(manifest['original_pid']).encode('ascii'), 'OUTCOME_ORIGINAL_PID_CHANGED')
        self.probe, self.context, self.manifest = probe, context, manifest
        self.events = queue.Queue(maxsize=16); self.seen = []; self.sent = []; self.failed = False; self.stderr_bytes = 0
        self.closed = False
        atomic_json(probe.output/'original-completion-debugger-intent.json', {'context': context, 'nonce': manifest['nonce'],
            'original_pid': manifest['original_pid'], 'fingerprint': manifest['fingerprint'], 'expected_hash': expected_hash,
            'automatic_retry': False, 'callback_rewrite_or_native_IPC': False}, exclusive=True)
        result = probe.device('forward', 'tcp:0', 'localabstract:webview_devtools_remote_'+str(manifest['original_pid']), check=False)
        require(result.returncode == 0 and not result.stderr.strip(), 'OUTCOME_ORIGINAL_FORWARD_UNCONFIRMED')
        port = result.stdout.decode('ascii').strip()
        require(re.fullmatch(r'[0-9]{4,5}', port) and 1024 <= int(port) <= 65535, 'OUTCOME_ORIGINAL_FORWARD_UNCONFIRMED')
        self.port = port
        atomic_json(probe.output/'original-completion-own-forward.json', {
            'nonce': manifest['nonce'], 'original_pid': manifest['original_pid'], 'port': int(port),
            'only_this_original_owned_forward_may_be_removed': True}, exclusive=True)
        pages = (metadata or original_pages)(port)
        require(type(pages) is list and len(pages) <= 16, 'OUTCOME_ORIGINAL_PAGE_REFUSED')
        selected = [page for page in pages if type(page) is dict and page.get('type') == 'page']
        require(len(selected) == 1 and selected[0].get('title') == 'OpenNexus', 'OUTCOME_ORIGINAL_PAGE_REFUSED')
        origin = urlsplit(selected[0].get('url', ''))
        require(origin.scheme == 'http' and origin.hostname == 'tauri.localhost' and origin.port is None
                and not origin.username and not origin.password, 'OUTCOME_ORIGINAL_PAGE_REFUSED')
        socket = urlsplit(selected[0].get('webSocketDebuggerUrl', ''))
        require(socket.scheme == 'ws' and socket.hostname == '127.0.0.1' and socket.port == int(port)
                and not socket.username and not socket.password and not socket.query and not socket.fragment
                and re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+', socket.path), 'OUTCOME_ORIGINAL_PAGE_REFUSED')
        value = {'configuration': {'mode': 'complete', 'nonce': manifest['nonce'], 'original_pid': manifest['original_pid'],
                 'sequence': 2, 'choice': 'copy', 'destination': 'runtime-conflict-copy.md',
                 'fingerprint': manifest['fingerprint'], 'expected_hash': expected_hash},
                 'context': {'run_id': context['run_id'], 'tools_commit': context['tools_commit'],
                             'socket_url': 'ws://127.0.0.1:'+port+socket.path}, 'baseline': baseline}
        command = ['node', '--max-old-space-size=128', str(Path(__file__).parent / 'android-runtime/original_outcome_bridge.mjs')]
        self.process = (factory or subprocess.Popen)(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.reader = threading.Thread(target=self._read, daemon=True); self.errors = threading.Thread(target=self._stderr, daemon=True)
        self.reader.start(); self.errors.start()
        self._write(value)

    def _write(self, value):
        body = json.dumps(value, separators=(',',':')).encode('ascii')+b'\n'
        require(len(body) <= 65536 and not self.closed, 'OUTCOME_ORIGINAL_BRIDGE_INPUT_REFUSED')
        # No resend if write/flush raises after the child may have consumed it.
        self.process.stdin.write(body); self.process.stdin.flush()

    def _read(self):
        total = 0
        try:
            while line := self.process.stdout.readline(65537):
                total += len(line)
                require(0 < len(line) <= 65536 and line.endswith(b'\n') and total <= 1024**2, 'OUTCOME_BRIDGE_STREAM_BOUND')
                value = json.loads(line)
                require(type(value) is dict and value.get('event') in ('armed','paused','owned-stop-authorized','original-exited',
                    'aborted-after-original-exit','fault','parent-command-refused','unconfirmed-connection-closed'),
                    'OUTCOME_BRIDGE_EVENT_REFUSED')
                self.events.put_nowait(value)
        except Exception:
            self.failed = True
            try: self.events.put_nowait({'event':'fault'})
            except queue.Full: pass

    def _stderr(self):
        try:
            while block := self.process.stderr.read(4096):
                self.stderr_bytes += len(block)
                if self.stderr_bytes > 65536: self.failed = True; return
        except Exception:
            self.failed = True

    def expect(self, event, *, timeout=20):
        order = ('armed','paused','owned-stop-authorized','original-exited')
        require(not self.failed and not self.closed and len(self.seen) < len(order)
                and event == order[len(self.seen)] and type(timeout) is int and 1 <= timeout <= 20,
                'OUTCOME_ORIGINAL_BRIDGE_ORDER_REFUSED')
        try: value = self.events.get(timeout=timeout)
        except queue.Empty:
            self.failed = True; raise RuntimeError('OUTCOME_ORIGINAL_BRIDGE_EVENT_UNCONFIRMED') from None
        valid = value.get('event') == event and value.get('nonce') == self.manifest['nonce'] \
                and value.get('original_pid') == self.manifest['original_pid'] and not self.failed
        if not valid: self.failed = True
        require(valid, 'OUTCOME_ORIGINAL_BRIDGE_EVENT_UNCONFIRMED')
        if event == 'armed':
            require(value.get('callback_binding_rewritten') is False and value.get('UI_input_sent') is False,
                    'OUTCOME_ORIGINAL_CALLBACK_CHANGED')
        if event in ('paused','original-exited'):
            require(value.get('original_callback_consumed') is False and value.get('callback_bindings_rewritten') is False,
                    'OUTCOME_ORIGINAL_CALLBACK_CHANGED')
            require(value.get('frozen_fingerprint') == self.manifest['fingerprint'], 'OUTCOME_ORIGINAL_REVIEW_CHANGED')
        atomic_json(self.probe.output/('original-completion-'+event+'.json'), value, exclusive=True)
        self.seen.append(event)
        return value

    def send(self, command, proof):
        require(command in ('native-before-stop','original-exited') and command not in self.sent and not self.failed,
                'OUTCOME_ORIGINAL_BRIDGE_COMMAND_REFUSED')
        require((command == 'native-before-stop' and self.seen == ['armed','paused'])
                or (command == 'original-exited' and self.seen == ['armed','paused','owned-stop-authorized']),
                'OUTCOME_ORIGINAL_BRIDGE_COMMAND_REFUSED')
        atomic_json(self.probe.output/('original-completion-command-'+command+'-intent.json'), {
            'context': self.context, 'nonce': self.manifest['nonce'], 'command': command,
            'original_readonly_proof': proof, 'automatic_retry': False}, exclusive=True)
        self.sent.append(command)
        self._write({'command': command, 'proof': proof})

    def close_after_original_exit(self, proof):
        original_app_exited(proof, self.manifest['original_pid'])
        require(self.seen == ['armed','paused','owned-stop-authorized','original-exited'] and not self.closed and not self.failed,
                'OUTCOME_ORIGINAL_BRIDGE_EXIT_UNPROVEN')
        self.process.stdin.close()
        require(self.process.wait(timeout=5) == 0, 'OUTCOME_ORIGINAL_BRIDGE_EXIT_UNPROVEN')
        self.reader.join(timeout=5); self.errors.join(timeout=5)
        require(not self.reader.is_alive() and not self.errors.is_alive() and not self.failed, 'OUTCOME_ORIGINAL_BRIDGE_STREAM_UNCONFIRMED')
        original.same_original(self.probe, self.manifest)
        pid = self.probe.shell('pidof', APP, check=False)
        state = self.probe.device('get-state', check=False)
        require(pid.returncode == 1 and not pid.stdout.strip() and not pid.stderr.strip()
                and state.returncode == 0 and state.stdout.strip() == b'device' and not state.stderr.strip(),
                'OUTCOME_ORIGINAL_BRIDGE_EXIT_UNPROVEN')
        removed = self.probe.device('forward', '--remove', 'tcp:'+self.port, check=False)
        require(removed.returncode == 0 and not removed.stderr.strip(), 'OUTCOME_ORIGINAL_FORWARD_CLEANUP_UNCONFIRMED')
        self.closed = True
        atomic_json(self.probe.output/'original-completion-debugger-closed.json', {
            'nonce': self.manifest['nonce'], 'original_pid': self.manifest['original_pid'], 'original_bridge_exit': 0,
            'own_forward_removed_after_original_exit': True, 'Debugger_resume_sent': False,
            'stderr_bytes_discarded': self.stderr_bytes, 'raw_error_or_scopes_exported': False}, exclusive=True)
