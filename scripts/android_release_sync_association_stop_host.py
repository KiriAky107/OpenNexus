"""Source-owned handoff and required-port Stop reports; no live adapter or CLI.

Local token discipline and controlled reports are not original private factory,
separate syscall, collector-bracket, Android Stop, Native or ACK verification.
"""
from abc import ABC, abstractmethod
import json
import shlex

from android_release_sync_association_host import OriginalAssociationCompletionHandoff
import android_release_sync_association_records as records
import android_release_sync_quota_records as common


class OriginalAssociationStopPorts(ABC):
    @abstractmethod
    def admit(self, original_ports, *, scope, identity, lifetime, collection_id, command_id, writer_guard_ms):
        """Bind actual same private factory/handles/context/host clock; None."""

    @abstractmethod
    def monotonic_ms(self):
        """Original SAME host-domain integer, never a device query."""

    @abstractmethod
    def product_owner(self):
        """Readonly same original product lifetime/boot/root/resumed; NO poll."""

    @abstractmethod
    def record_intent(self, raw):
        """Original exclusive private semantic Stop intent<=4096B; None."""

    @abstractmethod
    def stop(self, arguments, before_send, *, stop_intent_id, timeout_ms):
        """One original private command journal then guard(actual argv), <=2s.

        Guard failure must prevent physical send; this still needs a concrete
        original mapping. Return exact rc/unknown/stdout/stderr response only.
        """

    @abstractmethod
    def observe_stopped(self, index, observation_id, *, stop_intent_id, lifetime, identity, deadline_ms):
        """One fresh bounded private raw-derived observation per index1-2.

        Same-boot/root/UID/original-PID absence, all owned package/service
        absence and user0 force-stopped; no raw process data or cached pair.
        """

    @abstractmethod
    def record(self, raw):
        """Original exclusive approved final typed buffer<=32768B; None."""


class OriginalAssociationHostStop:
    def __init__(self, ports, *, handoff, stop_intent_id, observation_ids):
        self._state = 'NEW'
        self._handoff = handoff if type(handoff) is OriginalAssociationCompletionHandoff else None
        self._stopped_raw = None
        try:
            self._require(self._handoff is not None)
            # Consume before accepting arguments; a bad/unknown construction
            # cannot obtain a second permission or repair the first attempt.
            raw, intent, stdout, stderr, guard, original_ports = self._handoff._claim(self)
            self._require(isinstance(ports, OriginalAssociationStopPorts))
            report = records.bounded_json(raw, records.LIMIT)
            records.closed(report, ('schema','profile','completion','association_intent','completion_only',
                'completed_driver_reported','stop_reported','original_managed_process_independently_verified',
                'raw_intent_reader_independently_verified','original_private_files_independently_verified',
                'actual_separate_syscalls_verified','collector_bracketing_samples_verified',
                'capture_before_quota_chronology_verified','Native_or_client_ACK','runtime_verified'))
            expected = records.validate_association_completion(report['completion'], intent, stdout, stderr)
            records.exact(report, expected)
            value = expected['completion']
            records.number(guard)
            self._require(value['driver']['final_owner']['verified_monotonic_ms'] <= guard <= value['driver']['deadline_monotonic_ms'])
            used = records._known_ids(value['capture_scope'])
            records._fresh(value['collection_id'], used); records._fresh(value['driver']['command_id'], used)
            records._fresh(stop_intent_id, used)
            self._require(type(observation_ids) is tuple and len(observation_ids) == 2)
            for identifier in observation_ids:records._fresh(identifier, used)
            self._ports, self._original_ports = ports, original_ports
            self._completion, self._buffers = value, (intent, stdout, stderr)
            self._completion_raw, self._writer_guard = raw, guard
            self._stop_id, self._sample_ids = stop_intent_id, observation_ids
            self._last, self._deadline = guard, None
        except BaseException:
            self._abort()

    def _abort(self):
        self._state = 'FAILED'
        if self._handoff is not None:
            try:self._handoff._invalidate(self)
            except Exception:pass
        raise RuntimeError('SIGNED_ASSOCIATION_HOST_STOP_UNAVAILABLE') from None

    def _require(self, condition):
        if not condition:self._abort()

    def _active(self):
        self._require(self._state == 'RUNNING')
        self._handoff._check(self)

    def _call(self, callback, *args, **kwargs):
        self._active()
        try:result = callback(*args, **kwargs)
        except Exception:self._abort()
        self._active()
        return result

    def _now(self):
        value = self._call(self._ports.monotonic_ms)
        self._require(type(value) is int and self._last <= value <= records.MAX
                      and (self._deadline is None or value <= self._deadline))
        self._last = value
        return value

    def _owner(self, *, at_command=False):
        value = self._call(self._ports.product_owner)
        life, identity = self._completion['original_lifetime'], self._completion['identity']
        expected = dict(original_lifetime=life, owned_boot=identity,
            same_original_foreground_PID_UID_boot=True, app_resumed=True, root_uid=0, native_cycle_acknowledged=False)
        records.exact(value, expected)
        result = dict(expected, verified_monotonic_ms=self._now(), original_driver_completed=True)
        if at_command:result['observed_after_private_command_intent'] = True
        return result

    def run(self):
        self._require(self._state == 'NEW')
        self._state = 'RUNNING'
        try:return self._run()
        except BaseException:self._abort()

    def _run(self):
        value = self._completion
        self._now()
        admitted = self._call(self._ports.admit, self._original_ports,
            scope=dict(value['capture_scope']), identity=dict(value['identity']), lifetime=dict(value['original_lifetime']),
            collection_id=value['collection_id'], command_id=value['driver']['command_id'], writer_guard_ms=self._writer_guard)
        self._require(admitted is None)
        self._now()
        before = self._owner()
        arguments = ['shell','-T',shlex.join(('am','force-stop','--user','0',common.APP))]
        intent = dict(arguments=arguments, original_driver_completed=True, snapshot_stop_only=True, automatic_retry=False)
        intent_raw = common.encoded(dict(schema=1, phase=records.PHASE, driver_command_id=value['driver']['command_id'],
            stop_intent_id=self._stop_id, collection_id=value['collection_id'], original_lifetime=value['original_lifetime'],
            owned_boot=value['identity'], completion_writer_guard_monotonic_ms=self._writer_guard, intent=intent))
        self._require(len(intent_raw) <= 4096)
        written = self._call(self._ports.record_intent, intent_raw)
        self._require(written is None)
        written_at = self._now()
        waiting, guarded, at_owner, started = True, False, None, None

        def before_send(actual_arguments):
            nonlocal guarded, at_owner, started
            try:
                self._active(); self._require(waiting and not guarded)
                guarded = True
                records.exact(actual_arguments, arguments)
                at_owner = self._owner(at_command=True)
                started = self._now()
                self._require(started <= records.MAX-5000)
                self._deadline = started+5000
            except BaseException:self._abort()

        response = self._call(self._ports.stop, list(arguments), before_send,
                              stop_intent_id=self._stop_id, timeout_ms=2000)
        waiting = False
        self._require(guarded and at_owner is not None and started is not None)
        records.closed(response, ('returncode','unknown_result','stdout','stderr'))
        self._require(type(response['returncode']) is int and response['returncode'] == 0 and response['unknown_result'] is False
            and type(response['stdout']) is bytes and response['stdout'] == b''
            and type(response['stderr']) is bytes and response['stderr'] == b'')
        returned = self._now()
        self._require(returned <= started+2000)
        samples = []
        for index, observation_id in enumerate(self._sample_ids, 1):
            begin = self._now()
            facts = self._call(self._ports.observe_stopped, index, observation_id, stop_intent_id=self._stop_id,
                lifetime=dict(value['original_lifetime']), identity=dict(value['identity']), deadline_ms=self._deadline)
            expected = dict(sample_index=index, observation_id=observation_id, original_pid=value['original_lifetime']['pid'],
                product_uid=value['original_lifetime']['uid'], owned_boot=value['identity'], root_uid=0,
                original_pid_absent=True, all_owned_package_processes_absent=True, user0_force_stopped=True,
                raw_process_list_exported=False)
            records.exact(facts, expected)
            end = self._now()
            samples.append(dict(expected, schema=1, stop_intent_id=self._stop_id, package=common.APP,
                sample_start_monotonic_ms=begin, sample_end_monotonic_ms=end,
                original_driver_completed=True, native_cycle_acknowledged=False))
        stop = dict(schema=1, phase=records.PHASE, driver_command_id=value['driver']['command_id'],
            stop_intent_id=self._stop_id, original_lifetime=value['original_lifetime'], owned_boot=value['identity'],
            intent=intent, intent_written_monotonic_ms=written_at, before_command_owner=before, at_command_owner=at_owner,
            result=dict(returncode=0, automatic_retry=False, stdout_bytes=0, stdout_sha256=common.EMPTY,
                stderr_bytes=0, stderr_sha256=common.EMPTY, unvalidated_streams_exported=False,
                unknown_result=False, device_command_invoked=True),
            command_clock=dict(started_monotonic_ms=started, returned_monotonic_ms=returned,
                observation_deadline_monotonic_ms=self._deadline), observations=samples)
        complete = dict(value, stop=stop)
        correlation = records.validate_association_reports(complete, *self._buffers)
        packet = dict(schema=1, profile='signed-association-owned-stop-host-v1', records=complete, correlation=correlation,
            completion_writer_guard_monotonic_ms=self._writer_guard, source_owned_single_use_handoff_reported=True,
            actual_private_factory_bound=False, actual_separate_syscalls_verified=False,
            collector_bracketing_samples_verified=False, Native_or_client_ACK=False, runtime_verified=False)
        raw = common.encoded(packet)
        self._require(len(raw) <= records.LIMIT)
        saved = self._call(self._ports.record, raw)
        self._require(saved is None)
        self._now()
        self._handoff._finish(self)
        self._stopped_raw = raw
        self._state = 'DONE'
        return json.loads(raw)
