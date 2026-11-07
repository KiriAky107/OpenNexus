"""Required-port pre-stop association sequencing; no live adapter or CLI.

Original private factory/process/stream/command provenance and failure resource
policy remain required. Completion reports never attest Native, Stop or ACK.
"""
from abc import ABC, abstractmethod
import json
import shlex

import android_release_sync_association_records as records
import android_release_sync_quota_records as common

_HANDOFF_KEY = object()


class OriginalAssociationCompletionHandoff:
    """Single-use in-process ownership discipline, not private runtime proof.

    No caller metadata constructor or raw public accessor. Python object/type
    identity is not a security boundary or independent factory/syscall evidence.
    """
    def __init__(self, issuer, key):
        if key is not _HANDOFF_KEY or type(issuer) is not OriginalAssociationHostCompletion:
            raise RuntimeError('SIGNED_ASSOCIATION_COMPLETION_HANDOFF_UNAVAILABLE')
        self._issuer, self._consumer = issuer, None

    def _claim(self, consumer):
        issuer = self._issuer
        issuer._require(issuer._state == 'HANDED_OFF' and issuer._handoff is self and self._consumer is None)
        self._consumer = consumer
        issuer._state = 'CONSUMED'
        return (issuer._writer_buffer, *issuer._approved, issuer._writer_guard, issuer._ports)

    def _check(self, consumer):
        issuer = self._issuer
        issuer._require(issuer._state == 'CONSUMED' and issuer._handoff is self and self._consumer is consumer)

    def _invalidate(self, consumer):
        if self._consumer is consumer:
            self._issuer._state = 'FAILED'

    def _finish(self, consumer):
        self._check(consumer)
        self._issuer._state = 'STOP_REPORTED'


class OriginalAssociationCompletionPorts(ABC):
    @abstractmethod
    def admit(self, *, launch_scope, identity, lifetime, collection_id, command_id):
        """Original owned normal fixture/private config/session/Save lineage; None."""

    @abstractmethod
    def monotonic_ms(self):
        """Original host-domain integer sample only; no device query or poll."""

    @abstractmethod
    def product_owner(self):
        """Readonly original product lifetime/boot/root/resumed; NO driver poll."""

    @abstractmethod
    def instrument(self, arguments, before_send, *, command_id, timeout_ms, deadline_ms):
        """Own private managed child/journal; exact guard once after intent; None.

        Pass actual arguments to before_send immediately before original send.
        Guard failure must abort send. The original deadline never resets;
        all handles/logs stay private. Concrete enforcement is still required.
        """

    @abstractmethod
    def poll_driver(self):
        """Only original local managed child; no hidden prior non-None poll."""

    @abstractmethod
    def wait_turn(self, *, maximum_ms, deadline_ms):
        """Host-only wait<=50ms; no device/UI/driver poll; return None."""

    @abstractmethod
    def streams(self):
        """Once, original bounded nonlink private handles: (stdout, stderr)."""

    @abstractmethod
    def read_intent(self, arguments, *, bound_bytes, timeout_ms):
        """One original own-driver response: rc/unknown/stdout/stderr, no retry."""

    @abstractmethod
    def record(self, name, raw):
        """Original exclusive writer on this approved<=32768B buffer; None."""


class OriginalAssociationHostCompletion:
    def __init__(self, ports, *, launch_scope, identity, lifetime, collection_id, command_id):
        self._state = 'NEW'
        self._approved = None
        self._writer_buffer = self._writer_guard = self._handoff = None
        self._require(isinstance(ports, OriginalAssociationCompletionPorts))
        base = records.association_launch_scope(launch_scope)
        common.owned_identity(identity, base)
        common.lifetime(lifetime, base)
        used = records._known_ids(base)
        records._fresh(collection_id, used); records._fresh(command_id, used)
        self._context = json.loads(common.encoded(dict(launch_scope=base, identity=identity,
            lifetime=lifetime, collection_id=collection_id, command_id=command_id)))
        self._ports = ports
        self._last = self._deadline = None

    def _require(self, condition):
        if not condition:
            self._state = 'FAILED'
            raise RuntimeError('SIGNED_ASSOCIATION_HOST_COMPLETION_UNAVAILABLE') from None

    def _call(self, callback, *args, **kwargs):
        self._require(self._state == 'RUNNING')
        try:
            result = callback(*args, **kwargs)
        except Exception:
            self._state = 'FAILED'
            raise RuntimeError('SIGNED_ASSOCIATION_HOST_COMPLETION_UNAVAILABLE') from None
        self._require(self._state == 'RUNNING')
        return result

    def _now(self):
        value = self._call(self._ports.monotonic_ms)
        self._require(type(value) is int and 0 <= value <= records.MAX)
        self._require(self._last is None or self._last <= value)
        self._require(self._deadline is None or value <= self._deadline)
        self._last = value
        return value

    def _owner(self):
        context = self._context
        value = self._call(self._ports.product_owner)
        records.exact(value, dict(original_lifetime=context['lifetime'], owned_boot=context['identity'],
            same_original_foreground_PID_UID_boot=True, app_resumed=True, root_uid=0,
            native_cycle_acknowledged=False))
        return self._now()

    def run(self):
        self._require(self._state == 'NEW')
        self._state = 'RUNNING'
        try:
            return self._run()
        except BaseException:
            self._state = 'FAILED'
            raise RuntimeError('SIGNED_ASSOCIATION_HOST_COMPLETION_UNAVAILABLE') from None

    def handoff(self):
        """Transfer original immutable RAM once, without new port/read/clock."""
        self._require(self._state == 'DONE' and self._handoff is None and self._approved is not None
                      and type(self._writer_buffer) is bytes and type(self._writer_guard) is int)
        self._state = 'HANDED_OFF'
        self._handoff = OriginalAssociationCompletionHandoff(self, _HANDOFF_KEY)
        return self._handoff

    def _run(self):
        context = self._context
        start = self._now()
        self._require(start <= records.MAX-180000)
        self._deadline = start+180000
        admitted = self._call(self._ports.admit, **json.loads(common.encoded(context)))
        self._require(admitted is None)
        self._now(); self._owner()
        arguments = ['shell','-T',shlex.join(('am','instrument','-w','-e','phase',records.PHASE,
                                             common.DRIVER+'/.'+records.ENTRY))]
        waiting, guarded = True, False

        def before_send(actual_arguments):
            nonlocal guarded
            try:
                self._require(self._state == 'RUNNING' and waiting and not guarded)
                guarded = True
                records.exact(actual_arguments, arguments)
                self._owner()
            except BaseException:
                # A swallowed refusal cannot resume this producer. Actual send
                # enforcement remains an original private port obligation.
                self._state = 'FAILED'
                raise RuntimeError('SIGNED_ASSOCIATION_HOST_COMPLETION_UNAVAILABLE') from None

        launched = self._call(self._ports.instrument, list(arguments), before_send,
            command_id=context['command_id'], timeout_ms=180000, deadline_ms=self._deadline)
        waiting = False
        self._require(launched is None and guarded)
        self._now()
        for _ in range(3601):
            self._owner()
            result = self._call(self._ports.poll_driver)
            # An unknown/bad non-None result causes no follow-up clock/read.
            if result is not None:
                self._require(type(result) is int and result == 0)
                exited = self._now()
                break
            pending_at = self._now()
            self._require(pending_at < self._deadline)
            waited = self._call(self._ports.wait_turn, maximum_ms=min(50,self._deadline-pending_at),
                                deadline_ms=self._deadline)
            self._require(waited is None)
            self._now()
        else:
            self._require(False)

        stream_start = self._now()
        streams = self._call(self._ports.streams)
        self._require(type(streams) is tuple and len(streams) == 2)
        stdout, stderr = streams
        records.approved_stdout(stdout)
        self._require(type(stderr) is bytes and stderr == b'')
        stream_end = self._now()
        read_start = self._now()
        read_arguments = ['shell','-T','run-as '+common.DRIVER+' sh -c '+shlex.quote(
            'test -f '+records.INTENT_PATH+' && test ! -L '+records.INTENT_PATH+' && cat '+records.INTENT_PATH)]
        response = self._call(self._ports.read_intent, list(read_arguments), bound_bytes=2048, timeout_ms=1000)
        records.closed(response, ('returncode','unknown_result','stdout','stderr'))
        self._require(type(response['returncode']) is int and response['returncode'] == 0
            and response['unknown_result'] is False and type(response['stdout']) is bytes
            and type(response['stderr']) is bytes and response['stderr'] == b'')
        intent = response['stdout']
        scope = records.capture_scope_from_intent(context['launch_scope'], intent,
            collection_id=context['collection_id'], command_id=context['command_id'])
        # These are original host observations AFTER validating known buffers;
        # they conservatively include validation, not independent syscall times.
        read_end = self._now()
        self._require(read_end <= read_start+1000)
        final = self._owner()
        final_owner = dict(verified_monotonic_ms=final, original_lifetime=context['lifetime'],
            owned_boot=context['identity'], original_driver_completed=True,
            same_original_foreground_PID_UID_boot=True, app_resumed=True, root_uid=0,
            native_cycle_acknowledged=False)
        value = dict(schema=1, capture_scope=scope, identity=context['identity'],
            collection_id=context['collection_id'], original_lifetime=context['lifetime'],
            driver=dict(phase=records.PHASE, entry=records.ENTRY, command_id=context['command_id'],
                arguments=arguments, timeout_ms=180000, started_monotonic_ms=start,
                deadline_monotonic_ms=self._deadline, first_exit_seen_monotonic_ms=exited,
                streams_read_started_monotonic_ms=stream_start, streams_read_returned_monotonic_ms=stream_end,
                instrument_result=dict(returncode=0, unknown_result=False, automatic_retry=False,
                    early_cleanup_not_success=False, no_final_product_force_stop_or_reopen=True), final_owner=final_owner),
            intent_read=dict(arguments=read_arguments, bound_bytes=2048, timeout_ms=1000, read_count=1,
                returncode=0, unknown_result=False, automatic_retry=False, stderr_bytes=0,
                unvalidated_streams_exported=False, started_monotonic_ms=read_start, returned_monotonic_ms=read_end))
        report = records.validate_association_completion(value, intent, stdout, stderr)
        raw = common.encoded(report)
        self._require(len(raw) <= records.LIMIT)
        written = self._call(self._ports.record, 'signed-association-bootstrap-completion.json', raw)
        self._require(written is None)
        writer_guard = self._now()
        # Approved immutable originals remain private RAM for a future owning
        # stop/admission design; no raw public accessor or readback ACK exists.
        self._approved = (intent, stdout, stderr)
        self._writer_buffer, self._writer_guard = raw, writer_guard
        self._state = 'DONE'
        return json.loads(raw)
