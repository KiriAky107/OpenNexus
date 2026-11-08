"""Required-port private config sequence; no concrete factory or runtime CLI.

Actual original quota32 history/login/Save/Setup, private handles, driver root,
clock and command/create provenance must be supplied by the owning factory.
Types, successful controlled ports and shell flags do not attest those facts.
"""
from abc import ABC, abstractmethod
import json
import re
import shlex

import android_release_sync_association_records as records
import android_release_sync_quota_records as common

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_STAGING_UNAVAILABLE'
_PATH = 'no_backup/owned-sync.json'
_SCRIPT = ('umask 077; set -C; test ! -e ' + _PATH + ' && test ! -L ' + _PATH
           + ' && cat > ' + _PATH)
_ARGUMENTS = ('shell', '-T', 'run-as ' + common.DRIVER + ' sh -c ' + shlex.quote(_SCRIPT))


class OriginalAssociationConfigPorts(ABC):
    @abstractmethod
    def admit(self, *, launch_scope, vault_name, command_id):
        """Read-only actual normal quota32/history/login-Save/Setup source; None.

        Consume historical prepare-zero; never query zero after login/Setup.
        Bind exact original name and same private factory/driver/boot/root/clock.
        No device action, hidden local child poll or prospective product PID.
        """

    @abstractmethod
    def monotonic_ms(self):
        """Original host-domain exact integer; no device query or local poll."""

    @abstractmethod
    def verify_config_owner(self):
        """Independent readonly original driver/boot/root/fixed product gate; None."""

    @abstractmethod
    def record_config_intent(self, raw):
        """Dedicated exclusive private config-command intent<=4096B; None.

        Do not map this into an instrument/Stop intent role or artifact path.
        """

    @abstractmethod
    def stage_config(self, arguments, original_input, before_send, *, command_id,
                     timeout_ms, deadline_ms):
        """One original command, exact four-field response; no automatic retry.

        After the original journal, before physical send, call the guard once
        with actual argv and SAME supplied immutable stdin buffer. A guard
        failure MUST prevent send, even if a caller tries to swallow it. Bind
        original handles and actual exclusive/nofollow private device creation;
        shell flags or successful response metadata alone cannot prove this.
        The timeout includes guard time; enforce the SAME host deadline at
        physical send and completion, never a fresh post-guard ten seconds.
        """

    @abstractmethod
    def record_config_result(self, raw):
        """Dedicated exclusive private closed typed result<=4096B; None."""


class OriginalAssociationConfigStaging:
    def __init__(self, ports, *, launch_scope, vault_name, command_id):
        self._state = 'NEW'
        self._approved = None
        self._require(isinstance(ports, OriginalAssociationConfigPorts))
        try:
            base = records.association_launch_scope(launch_scope)
            self._require(type(vault_name) is str and re.fullmatch(
                r'Owned Android Sync [0-9a-f]{32}', vault_name) is not None)
            records._fresh(command_id, records._known_ids(base))
            self._context = json.loads(common.encoded(dict(launch_scope=base,
                vault_name=vault_name, command_id=command_id)))
            # Same two fields/order as the original private producer; no hash.
            self._input = json.dumps(dict(vault_id=base['remote_vault'],
                vault_name=vault_name)).encode('utf-8')
            self._require(0 < len(self._input) <= 2048)
        except Exception:
            self._fail()
        self._ports = ports
        self._last = self._deadline = None

    def _fail(self):
        self._state = 'FAILED'
        self._approved = None
        raise RuntimeError(_ERROR) from None

    def _require(self, value):
        if not value:
            self._fail()

    def _call(self, function, *args, **kwargs):
        self._require(self._state in ('RUNNING', 'SENDING'))
        value = function(*args, **kwargs)
        self._require(self._state in ('RUNNING', 'SENDING'))
        return value

    def _clock(self):
        value = self._call(self._ports.monotonic_ms)
        self._require(type(value) is int and 0 <= value <= common.MAX)
        self._require(self._last is None or self._last <= value)
        self._require(self._deadline is None or value <= self._deadline)
        self._last = value
        return value

    def run(self):
        self._require(self._state == 'NEW')
        self._state = 'RUNNING'
        try:
            self._run()
        except Exception:
            self._fail()

    def _run(self):
        start = self._clock()
        self._require(start <= common.MAX - 10000)
        self._deadline = start + 10000
        context = json.loads(common.encoded(self._context))
        self._require(self._call(self._ports.admit, **context) is None)
        self._clock()
        self._require(self._call(self._ports.verify_config_owner) is None)
        self._clock()
        intent = common.encoded(dict(schema=1, launch_scope=self._context['launch_scope'],
            command_id=self._context['command_id'], arguments=list(_ARGUMENTS),
            private_destination=_PATH, stdin_bytes=len(self._input), automatic_retry=False))
        self._require(len(intent) <= 4096)
        self._require(self._call(self._ports.record_config_intent, intent) is None)
        staged = self._clock()
        self._require(staged < self._deadline)
        self._state = 'SENDING'
        guard_calls = 0
        send_at = None

        def before_send(actual_arguments, actual_input):
            nonlocal guard_calls, send_at
            self._require(self._state == 'SENDING' and guard_calls == 0)
            guard_calls += 1
            self._require(type(actual_arguments) is list and len(actual_arguments) == len(_ARGUMENTS)
                and all(type(x) is str for x in actual_arguments)
                and tuple(actual_arguments) == _ARGUMENTS and actual_input is self._input)
            self._clock()
            self._require(self._call(self._ports.verify_config_owner) is None)
            send_at = self._clock()
            self._require(send_at < self._deadline)

        response = self._call(self._ports.stage_config, list(_ARGUMENTS), self._input,
            before_send, command_id=self._context['command_id'],
            timeout_ms=self._deadline-staged, deadline_ms=self._deadline)
        self._require(guard_calls == 1 and send_at is not None)
        # Validate all private fields before any subsequent clock or writer.
        self._require(type(response) is dict and len(response) == 4
            and all(type(k) is str for k in response)
            and set(response) == {'returncode', 'unknown_result', 'stdout', 'stderr'})
        self._require(type(response['returncode']) is int and response['returncode'] == 0
            and response['unknown_result'] is False
            and type(response['stdout']) is bytes and len(response['stdout']) == 0
            and type(response['stderr']) is bytes and len(response['stderr']) == 0)
        self._state = 'RUNNING'
        returned = self._clock()
        result = common.encoded(dict(schema=1, command_id=self._context['command_id'],
            started_monotonic_ms=start, deadline_monotonic_ms=self._deadline,
            before_send_monotonic_ms=send_at, result_observed_monotonic_ms=returned,
            returncode=0, unknown_result=False, stdout_bytes=0, stderr_bytes=0,
            config_only=True, actual_private_factory_verified=False,
            independent_private_file_or_syscall_verified=False,
            device_config_readback_verified=False, native_client_acknowledged=False,
            runtime_acceptance=False))
        self._require(len(result) <= 4096)
        self._require(self._call(self._ports.record_config_result, result) is None)
        writer_guard = self._clock()
        self._approved = (intent, self._input, result, writer_guard)
        self._state = 'DONE'
