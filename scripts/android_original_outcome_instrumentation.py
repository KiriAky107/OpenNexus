"""One continuous hosted UI driver; no early ready read or second invocation."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import uuid

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_product_pair_android import hosted_scope, observe_device, observe_packages
from android_runtime_probe import APP, DRIVER
from android_original_outcome_status import OriginalReviewStatus, STAGES


class OriginalOutcomeInstrumentation:
    def __init__(self, probe, context, nonce, original_pid, device, packages, *, factory=None):
        # Refuse local/physical-device use before filesystem or ADB access.
        hosted_scope(context, probe.args.serial, probe.owner)
        require(probe.args.commit == context['android_commit'] and probe.args.tools_commit == context['tools_commit']
                and type(nonce) is str and str(uuid.UUID(nonce)) == nonce
                and type(original_pid) is int and original_pid > 0, 'OUTCOME_STREAM_SCOPE_REFUSED')
        root = Path(probe.output)
        workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
        require(root.is_absolute() and root.is_dir() and root.resolve() == root and not root.is_symlink()
                and root.is_relative_to(workspace / '.build'), 'OUTCOME_STREAM_OUTPUT_REFUSED')
        require(observe_device(probe) == device and observe_packages(probe) == packages
                and packages[0]['package'] == APP and packages[0]['sha256'] == probe.binding['sha256'],
                'OUTCOME_STREAM_ORIGINAL_REPLACED')
        pid = probe.shell('pidof', APP, check=False)
        require(pid.returncode == 0 and pid.stdout.strip() == str(original_pid).encode('ascii')
                and not pid.stderr.strip(), 'OUTCOME_STREAM_APP_PID_REFUSED')
        self.probe, self.context, self.nonce, self.original_pid = probe, context, nonce, original_pid
        self.original_device, self.original_packages = device, packages
        self.parser = OriginalReviewStatus(nonce)
        self.events = queue.Queue(maxsize=16)
        self.delivered = []
        self.read_stages = []
        self.driver_pid = None
        self.closed = False
        self.failed = False
        self.stderr_bytes = 0
        self.stderr_overflow = False
        atomic_json(root / 'continuous-original-instrumentation-intent.json', {
            'context': context, 'nonce': nonce, 'original_app_pid': original_pid,
            'phase': 'conflict-stale-interrupt', 'automatic_retry': False,
            'new_instrumentation_between_freeze_and_confirm': False}, exclusive=True)
        command = [str(probe.adb), '-s', probe.args.serial, 'shell', 'am', 'instrument', '-w',
                   '-e', 'phase', 'conflict-stale-interrupt', DRIVER + '/.SyncRuntimeProbe']
        self.process = (factory or subprocess.Popen)(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.errors = threading.Thread(target=self._stderr, daemon=True)
        self.reader.start(); self.errors.start()

    def _emit(self, value):
        try: self.events.put_nowait(value)
        except queue.Full:
            self.stderr_overflow = True

    def _read(self):
        try:
            while line := self.process.stdout.readline(4097):
                stage = self.parser.feed(line)
                if stage is not None: self._emit(('stage', stage))
            result = self.parser.finish()
            self._emit(('eof', result))
        except Exception:
            self._emit(('fault', 'OUTCOME_ORIGINAL_STREAM_REFUSED'))

    def _stderr(self):
        try:
            while block := self.process.stderr.read(4096):
                self.stderr_bytes += len(block)
                if self.stderr_bytes > 65536:
                    self.stderr_overflow = True
                    self._emit(('fault', 'OUTCOME_ORIGINAL_STDERR_BOUND'))
                    return
                # Discard the original error text. Only the count is retained.
        except Exception:
            self._emit(('fault', 'OUTCOME_ORIGINAL_STDERR_UNCONFIRMED'))

    def next_ready(self, expected, *, timeout=40):
        require(not self.closed and not self.failed and len(self.delivered) < len(STAGES)
                and expected == STAGES[len(self.delivered)] and type(timeout) is int and 1 <= timeout <= 45,
                'OUTCOME_ORIGINAL_READY_ORDER_REFUSED')
        try: kind, value = self.events.get(timeout=timeout)
        except queue.Empty:
            self.failed = True
            raise RuntimeError('OUTCOME_ORIGINAL_READY_UNCONFIRMED') from None
        if kind != 'stage' or value != expected or self.stderr_overflow:
            self.failed = True
        require(kind == 'stage' and value == expected and not self.stderr_overflow,
                'OUTCOME_ORIGINAL_READY_UNCONFIRMED')
        require(observe_device(self.probe) == self.original_device
                and observe_packages(self.probe) == self.original_packages, 'OUTCOME_STREAM_ORIGINAL_REPLACED')
        app = self.probe.shell('pidof', APP, check=False)
        require(app.returncode == 0 and app.stdout.strip() == str(self.original_pid).encode('ascii')
                and not app.stderr.strip(), 'OUTCOME_STREAM_APP_PID_REFUSED')
        observed = self.probe.shell('pidof', DRIVER, check=False)
        require(observed.returncode == 0 and not observed.stderr.strip()
                and re.fullmatch(rb'[1-9][0-9]{0,9}', observed.stdout.strip()), 'OUTCOME_ORIGINAL_DRIVER_PID_REFUSED')
        if self.driver_pid is None:
            self.driver_pid = int(observed.stdout.strip())
            require(self.driver_pid != self.original_pid, 'OUTCOME_ORIGINAL_DRIVER_PID_REFUSED')
            atomic_json(self.probe.output / 'continuous-original-driver-identity.json', {
                'context': self.context, 'nonce': self.nonce, 'original_app_pid': self.original_pid,
                'original_driver_pid': self.driver_pid, 'device': self.original_device,
                'packages': self.original_packages}, exclusive=True)
        require(int(observed.stdout.strip()) == self.driver_pid, 'OUTCOME_ORIGINAL_DRIVER_PID_CHANGED')
        self.delivered.append(expected)

    def read_ready(self, stage):
        require(not self.closed and not self.failed and stage in self.delivered and stage not in self.read_stages
                and self.delivered[-1] == stage, 'OUTCOME_ORIGINAL_READ_BEFORE_READY_REFUSED')
        result = self.probe.shell('run-as', DRIVER, 'cat', 'no_backup/owned-outcome-' + stage + '.json', check=False)
        require(result.returncode == 0 and not result.stderr.strip() and 0 < len(result.stdout) <= 4096,
                'OUTCOME_ORIGINAL_READY_FILE_REFUSED')
        value = json.loads(result.stdout)
        require(type(value) is dict and value.get('nonce') == self.nonce, 'OUTCOME_ORIGINAL_READY_FILE_REFUSED')
        atomic_json(self.probe.output / ('original-driver-' + stage + '.json'), value, exclusive=True)
        self.read_stages.append(stage)
        return value

    def finish_after_driver_exit(self, proof):
        require(not self.closed and not self.failed and self.delivered == list(STAGES) and self.read_stages == list(STAGES)
                and type(proof) is dict and proof.get('nonce') == self.nonce
                and proof.get('original_app_pid') == self.original_pid
                and proof.get('original_driver_pid') == self.driver_pid
                and proof.get('cold_original_entry_permitted') is True
                and proof.get('original_app_absence_observed') is True
                and proof.get('original_driver_absence_observed') is True
                and proof.get('same_original_device_packages_UIDs') is True
                and proof.get('owned_emulator_still_connected') is True,
                'OUTCOME_ORIGINAL_DRIVER_EXIT_UNPROVEN')
        self.closed = True
        # This only closes the original host-side adb handle after actual own
        # app AND test-driver absence. Terminating adb is not the exit proof.
        host_handle_closed = False
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate(); host_handle_closed = True
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
        self.reader.join(timeout=5); self.errors.join(timeout=5)
        require(not self.reader.is_alive() and not self.errors.is_alive() and not self.stderr_overflow,
                'OUTCOME_ORIGINAL_STREAM_CLOSE_UNCONFIRMED')
        kind, stream = self.events.get(timeout=1)
        require(kind == 'eof' and stream['all_review_ready_frames_observed'] is True,
                'OUTCOME_ORIGINAL_STREAM_CLOSE_UNCONFIRMED')
        value = {'nonce': self.nonce, 'original_app_pid': self.original_pid, 'original_driver_pid': self.driver_pid,
                 'readiness_stages': list(self.delivered), 'instrumentation_invocations': 1,
                 'original_host_handle_closed_after_actual_driver_absence': host_handle_closed,
                 'instrumentation_returncode': self.process.returncode, 'raw_error_text_exported': False,
                 'stderr_bytes_discarded': self.stderr_bytes, 'bounded_original_stream_complete': True,
                 'original_UI_phase_deliberately_interrupted': True, 'ordinary_UI_phase_success_claimed': False}
        atomic_json(self.probe.output / 'continuous-original-instrumentation-closed.json', value, exclusive=True)
        return value
