"""One-shot quota input-prefix sequencing through required source-bound ports.

No concrete device/clock/process/file adapter, CLI or complete-driver admission
is supplied. Controlled port reports cannot prove runtime execution or an ACK.
"""
from abc import ABC, abstractmethod
import hashlib
import json
import shlex

import android_release_sync_quota_records as records
import android_release_sync_quota_upload as upload
import android_release_sync_quota_validation as quota


class OriginalQuotaInputPorts(ABC):
    @abstractmethod
    def admit(self, base, identity, lifetime):
        """Validate owned normal fixture/prepare/session/associated context; return None."""

    @abstractmethod
    def monotonic_ms(self):
        """Return the supplied host monotonic sample as an integer."""

    @abstractmethod
    def owner(self):
        """Run the original same-foreground/instrument/PID-UID-start-boot/root guard."""

    @abstractmethod
    def wait_json(self, path, deadline_ms, owner):
        """Original bounded wait/4096B single reader; partial None remains pending."""

    @abstractmethod
    def freeze_context(self, parent, *, owner_run, remote_vault, device_id):
        """Original guarded quota factory's exact (context, same-buffer raw SHA) return."""

    @abstractmethod
    def record(self, name, raw):
        """Original local exclusive writer on one closed buffer; return exactly None."""

    @abstractmethod
    def signal(self, name, deadline_ms):
        """One original static7B signal + original readback; closed typed local records."""

    @abstractmethod
    def clock(self):
        """One original elapsed/device query with its supplied host clock bracket."""

    @abstractmethod
    def tap(self, arguments, before_send, *, timeout_ms):
        """Original private command, fixed5000ms; guard once immediately before send."""

    @abstractmethod
    def post_input_owner(self):
        """Return only the original known post-command ownership metadata."""


def need(value):
    if not value:
        raise RuntimeError('SIGNED_QUOTA_HOST_PREFIX_UNAVAILABLE')


def base_scope(value):
    operations = ('login_operation_id', 'setup_operation_id', 'save_operation_id',
                  'association_operation_id', 'upload_operation_id')
    keys = ('schema','owner_run','avd','boot_id','api','uid','product','sync',
            'remote_vault','binding_id','android_device','setup_device',*operations)
    need(quota._closed(value, keys))
    need(quota._pattern(value['owner_run'], r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}'))
    for key, expected in dict(schema=1,api=36,product=quota.PRODUCT,sync=quota.SYNC,
                              avd='opennexus-ci-'+value['owner_run']).items():
        records.exact(value[key], expected)
    records.number(value['uid'], 10000, 99999)
    need(all(quota._uuid(value[k]) for k in ('boot_id','binding_id',*operations)))
    need(len({value[k] for k in operations}) == len(operations))
    need(all(quota._pattern(value[k], r'[0-9a-f]{32}') for k in ('remote_vault','android_device','setup_device')))
    need(value['android_device'] != value['setup_device'])
    return dict(value)


def ready_value(value, parent, uid):
    records.closed(value, ('schema','phase','action','parent_intent_id','driver_intent_id',
                          'package','uid','window','original','visible','x','y','elapsed_ms'))
    at = records.number(value['elapsed_ms'], maximum=records.MAX-36000)
    records.number(value['window'], 1, 65535)
    x, y = (records.number(value[k], maximum=16384) for k in ('x','y'))
    for key in ('original','visible'):
        rect = value[key]
        need(type(rect) is list and len(rect) == 4)
        for n in rect:records.number(n, -16384, 16384)
        need(rect[0] <= x < rect[2] and rect[1] <= y < rect[3])
    a, b = value['original'], value['visible']
    need(a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
         and x == (b[0]+b[2])//2 and y == (b[1]+b[3])//2)
    result = dict(schema=1,phase=records.PHASE,action='cycle-start',parent_intent_id=parent['parent_intent_id'],
                  driver_intent_id=parent['input_intent_id'],package=records.APP,uid=uid,
                  window=value['window'],original=list(a),visible=list(b),x=x,y=y,elapsed_ms=at)
    records.exact(value, result)
    return result


def ready_age(ready, elapsed):
    records.number(elapsed)
    need(ready['elapsed_ms'] <= elapsed <= records.until(ready['elapsed_ms'], 2000))
    return elapsed-ready['elapsed_ms']


class OriginalQuotaInputPrefix:
    def __init__(self, ports, *, base, identity, lifetime):
        need(isinstance(ports, OriginalQuotaInputPorts))
        self._base = base_scope(base)
        records.owned_identity(identity, self._base)
        records.lifetime(lifetime, self._base)
        self._identity, self._life = dict(identity), dict(lifetime)
        self._ports = ports
        self._state = 'NEW'
        self._last = 0
        self._written = set()

    def close(self):
        self._state = 'CLOSED'

    def _live(self):
        need(self._state == 'RUNNING')

    def _call(self, method, *args, **kwargs):
        self._live()
        value = method(*args, **kwargs)
        self._live()
        return value

    def _owner(self):
        need(self._call(self._ports.owner) is None)

    def _mono(self):
        value = self._call(self._ports.monotonic_ms)
        records.number(value)
        need(value >= self._last)
        self._last = value
        return value

    def _clock(self):
        value = self._call(self._ports.clock)
        start, end, elapsed = records.clock(value)
        need(start >= self._last)
        self._last = end
        return dict(value), elapsed

    def _record(self, name, value):
        # Every caller supplies already-closed, primitive typed metadata.
        self._live();need(name not in self._written)
        self._written.add(name)
        raw = records.encoded(value);need(0 < len(raw) <= 4096)
        need(self._call(self._ports.record, name, raw) is None)

    def _signal(self, name, deadline):
        self._owner()
        need(records.until(self._mono(), 3000) <= deadline)
        value = self._call(self._ports.signal, name, deadline)
        records.closed(value, ('intent','result','readback'))
        records.signal(value, name)
        need(self._mono() <= deadline)
        # Copy only after the complete closed shape/types are known.
        return json.loads(records.encoded(value))

    def run(self):
        if self._state != 'NEW':
            self.close();need(False)
        self._state = 'RUNNING'
        try:
            need(self._call(self._ports.admit,dict(self._base),dict(self._identity),dict(self._life)) is None)
            parent = self._call(self._ports.wait_json,
                'no_backup/owned-sync-quota-'+records.PHASE+'-intent.json',records.until(self._mono(),25000),self._owner)
            expected_context = upload.context_from_parent(parent,owner_run=self._base['owner_run'],
                remote_vault=self._base['remote_vault'],device_id=self._base['android_device'])
            parent = dict(parent)
            self._record('parent', parent)
            context_deadline = records.until(self._mono(),10000)
            frozen = self._call(self._ports.freeze_context,dict(parent),owner_run=self._base['owner_run'],
                remote_vault=self._base['remote_vault'],device_id=self._base['android_device'])
            need(type(frozen) is tuple and len(frozen) == 2)
            records.exact(frozen[0], expected_context)
            context_raw = upload.encoded(expected_context);need(len(context_raw) <= 2048)
            digest = hashlib.sha256(context_raw).hexdigest()
            records.exact(frozen[1], digest)
            context_write = dict(phase=records.PHASE,context_file_sha256=digest,
                same_writer_buffer_reported=True,original_buffer_write_returned=True,
                automatic_retry=False,native_cycle_acknowledged=False)
            self._record('context-write',context_write)
            context_signal = self._signal('owned-sync-quota-context-'+parent['parent_intent_id']+'.finish',context_deadline)
            self._record('context-ready-signal',context_signal)
            raw_ready = self._call(self._ports.wait_json,
                'no_backup/owned-sync-quota-'+records.PHASE+'-cycle-start-ready.json',
                records.until(self._mono(),10000),self._owner)
            ready = ready_value(raw_ready,parent,self._base['uid'])
            self._owner();clock1,elapsed1 = self._clock()
            ready_age(ready,elapsed1)
            scope = quota._scope(dict(self._base,parent_id=parent['parent_intent_id'],
                input_id=parent['input_intent_id'],window=ready['window']))
            deadline = records.until(clock1['sample_start_monotonic_ms'],42000)
            owner1 = dict(original_lifetime=dict(self._life),same_original_foreground_PID_UID_boot=True,
                original_instrumentation_alive=True,ready_age_ms=elapsed1-ready['elapsed_ms'],
                ready_age_bound_ms=2000,native_cycle_acknowledged=False)
            host_window = dict(original_ready_elapsed_ms=ready['elapsed_ms'],
                preinput_monotonic_ms=clock1['sample_start_monotonic_ms'],
                host_completion_deadline_monotonic_ms=deadline,driver_failure_bound_ms=36000,
                product_client_bound_ms=30000,ready_age_bound_ms=2000,clock_reset_on_marker=False,
                no_fault_injection=True,native_timeout_verified=False)
            for name,value in (('ready',ready),('input-clock',clock1),('input-owner',owner1),('host-window',host_window)):
                self._record(name,value)
            args = ['shell','-T',shlex.join(('input','touchscreen','tap',str(ready['x']),str(ready['y'])))]
            intent = dict(arguments=list(args),package=records.APP,automatic_retry=False)
            self._record('touch-intent',intent)
            gate = {}
            def before_send():
                self._live()
                if gate:
                    self.close();need(False)
                gate['entered'] = True
                self._owner();clock2,elapsed2 = self._clock()
                ready_age(ready,elapsed2)
                need(elapsed2 >= elapsed1 and self._mono() <= deadline)
                gate.update(clock=clock2,owner=dict(original_lifetime=dict(self._life),
                    same_original_foreground_PID_UID_boot=True,original_instrumentation_alive=True,
                    ready_age_ms=elapsed2-ready['elapsed_ms'],ready_age_bound_ms=2000,
                    observed_after_private_command_intent=True,native_cycle_acknowledged=False))
            result = self._call(self._ports.tap,tuple(args),before_send,timeout_ms=5000)
            need(set(gate) == {'entered','clock','owner'})
            records.empty_result(result,invoked=True)
            result = dict(result)
            for name,value in (('command-clock',gate['clock']),('input-owner-at-command',gate['owner']),('touch-result',result)):
                self._record(name,value)
            post = self._call(self._ports.post_input_owner)
            records.exact(post,dict(input_command_returned=True,native_cycle_acknowledged=False,
                observation_unavailable=False,original_instrumentation_alive=True,same_original_PID=True,
                app_resumed=True,same_owned_boot_identity=True))
            post = dict(post);self._record('post-input-owner',post)
            finish = self._signal('owned-sync-quota-touch-'+parent['input_intent_id']+'.finish',deadline)
            self._record('input-finish-signal',finish)
            prefix = dict(schema=1,scope=scope,identity=dict(self._identity),phase=records.PHASE,entry=records.ENTRY,
                parent=parent,context=expected_context,context_write=context_write,context_ready_signal=context_signal,
                ready=ready,original_lifetime=dict(self._life),input_owner=owner1,input_owner_at_command=gate['owner'],
                input_clock=clock1,command_clock=gate['clock'],host_window=host_window,touch_intent=intent,
                touch_result=result,post_input_owner=post,input_finish_signal=finish)
            need(len(records.encoded(prefix)) <= records.MAX_BUNDLE)
            return dict(schema=1,records=json.loads(records.encoded(prefix)),input_prefix_only=True,
                completed_driver_reported=False,original_files_independently_verified=False,
                live_deadlines_independently_verified=False,native_cycle_acknowledged=False,runtime_verified=False)
        finally:
            # Unknown results cause no follow-up I/O, stop, readback or retry.
            self.close()
