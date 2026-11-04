"""Required-port quota completion sequencing; no concrete live adapter or CLI.

After input-finish the product guard must not sample the managed driver. Its
first local exit poll belongs after the failure clock. Reports are not Native,
file readback, separate syscall or runtime acceptance evidence.
"""
from abc import ABC, abstractmethod
import hashlib
import json
import shlex

import android_release_sync_quota_host_prefix as prefix
import android_release_sync_quota_records as records
import android_release_sync_quota_upload as upload
import android_release_sync_quota_validation as quota


class OriginalQuotaCompletionPorts(ABC):
    @abstractmethod
    def monotonic_ms(self):
        """Original host-domain integer sample, never a device clock."""

    @abstractmethod
    def product_owner(self):
        """Read-only same boot/root/product PID-UID-start/resumed; NO driver poll."""

    @abstractmethod
    def wait_failed(self, path, deadline_ms, owner):
        """Original bounded wait/single4096B/1s reader, readonly guards both sides."""

    @abstractmethod
    def clock(self):
        """Exactly one original failure elapsed query with original host bracket."""

    @abstractmethod
    def poll_driver(self):
        """Original managed local child poll, no prior post-finish non-None poll."""

    @abstractmethod
    def wait_turn(self, *, maximum_ms, deadline_ms):
        """Host-only wait <=50ms and original deadline; no device/process poll."""

    @abstractmethod
    def streams(self):
        """Once, original bounded private stdout/stderr bytes of the completed child."""

    @abstractmethod
    def record(self, name, raw):
        """Original exclusive local writer on this exact approved buffer; return None."""


def need(value):
    if not value:
        raise RuntimeError('SIGNED_QUOTA_HOST_COMPLETION_UNAVAILABLE')


def input_value(wrapper):
    """Close the supplied prefix without inventing a completion to validate it."""
    records.closed(wrapper, ('schema','records','input_prefix_only','completed_driver_reported',
        'original_files_independently_verified','live_deadlines_independently_verified',
        'native_cycle_acknowledged','runtime_verified'))
    value = wrapper['records']
    keys = ('schema','scope','identity','phase','entry','parent','context','context_write',
        'context_ready_signal','ready','original_lifetime','input_owner','input_owner_at_command',
        'input_clock','command_clock','host_window','touch_intent','touch_result',
        'post_input_owner','input_finish_signal')
    records.closed(value, keys)
    scope = quota._scope(value['scope'])
    prefix.base_scope({k:v for k,v in scope.items() if k not in ('parent_id','input_id','window')})
    records.number(scope['window'],1,65535)
    records.owned_identity(value['identity'],scope)
    life = value['original_lifetime'];records.lifetime(life,scope)
    parent = upload.parent_value(scope['parent_id'],scope['input_id'])
    records.exact(value['parent'],parent)
    context = upload.context_from_parent(parent,owner_run=scope['owner_run'],
        remote_vault=scope['remote_vault'],device_id=scope['android_device'])
    ready = prefix.ready_value(value['ready'],parent,scope['uid'])
    records.exact(ready['window'],scope['window'])
    age1 = records.input_owner(value['input_owner'],life)
    age2 = records.input_owner(value['input_owner_at_command'],life,at_command=True)
    first,end1,elapsed1 = records.clock(value['input_clock'])
    start2,end2,elapsed2 = records.clock(value['command_clock'])
    need(end1 <= start2 and age1 <= age2 and elapsed1 == ready['elapsed_ms']+age1
         and elapsed2 == ready['elapsed_ms']+age2)
    deadline = records.until(first,42000);need(end2 <= deadline)
    window = dict(original_ready_elapsed_ms=ready['elapsed_ms'],preinput_monotonic_ms=first,
        host_completion_deadline_monotonic_ms=deadline,driver_failure_bound_ms=36000,
        product_client_bound_ms=30000,ready_age_bound_ms=2000,clock_reset_on_marker=False,
        no_fault_injection=True,native_timeout_verified=False)
    records.exact(value['host_window'],window)
    records.signal(value['context_ready_signal'],'owned-sync-quota-context-'+scope['parent_id']+'.finish')
    records.signal(value['input_finish_signal'],'owned-sync-quota-touch-'+scope['input_id']+'.finish')
    expected = dict(value,schema=1,phase=records.PHASE,entry=records.ENTRY,scope=scope,parent=parent,
        ready=ready,context=context,context_write=dict(phase=records.PHASE,context_file_sha256=records.digest(context),
        same_writer_buffer_reported=True,original_buffer_write_returned=True,automatic_retry=False,
        native_cycle_acknowledged=False),touch_intent=dict(arguments=['shell','-T',
        shlex.join(('input','touchscreen','tap',str(ready['x']),str(ready['y'])))],package=records.APP,
        automatic_retry=False),post_input_owner=dict(input_command_returned=True,native_cycle_acknowledged=False,
        observation_unavailable=False,original_instrumentation_alive=True,same_original_PID=True,
        app_resumed=True,same_owned_boot_identity=True))
    records.empty_result(value['touch_result'],invoked=True)
    records.exact(value,expected)
    records.exact(wrapper,dict(schema=1,records=expected,input_prefix_only=True,
        completed_driver_reported=False,original_files_independently_verified=False,
        live_deadlines_independently_verified=False,native_cycle_acknowledged=False,runtime_verified=False))
    need(all(len(records.encoded(v)) <= 4096 for v in expected.values())
         and len(records.encoded(expected)) <= records.MAX_BUNDLE)
    return json.loads(records.encoded(expected))


def failed_value(value, original):
    records.closed(value,('schema','phase','parent_intent_id','input_intent_id','package','uid','window',
        'elapsed_ms','scene_scan_elapsed_ms','scene_counts','public_alert_rule_id','failed_cycle_UI_observed',
        'quota_error_UI_observed','raw_UI_recorded','native_original_state_verified'))
    at = records.number(value['elapsed_ms'])
    need(original['command_clock']['sampled_elapsed_ms'] <= at <= original['ready']['elapsed_ms']+36000)
    scope = original['scope']
    expected = dict(schema=1,phase=records.PHASE,parent_intent_id=scope['parent_id'],
        input_intent_id=scope['input_id'],package=records.APP,uid=scope['uid'],window=scope['window'],
        elapsed_ms=at,scene_scan_elapsed_ms=at,scene_counts=[1,1,1,1,0,0,0,0],
        public_alert_rule_id=records.RULE,failed_cycle_UI_observed=True,quota_error_UI_observed=True,
        raw_UI_recorded=False,native_original_state_verified=False)
    records.exact(value,expected)
    return expected


class OriginalQuotaHostCompletion:
    def __init__(self,ports,*,input_prefix):
        need(isinstance(ports,OriginalQuotaCompletionPorts))
        self._original = input_value(input_prefix)
        self._ports = ports;self._state = 'NEW'
        self._last = self._original['command_clock']['sample_end_monotonic_ms']
        self._deadline = self._original['host_window']['host_completion_deadline_monotonic_ms']
        self._written = set()

    def close(self):
        self._state = 'CLOSED'

    def _live(self):
        need(self._state == 'RUNNING')

    def _call(self,method,*args,**kwargs):
        self._live();value = method(*args,**kwargs);self._live()
        return value

    def _mono(self):
        at = self._call(self._ports.monotonic_ms);records.number(at)
        need(self._last <= at <= self._deadline);self._last = at
        return at

    def _owner(self):
        value = self._call(self._ports.product_owner)
        expected = dict(original_lifetime=self._original['original_lifetime'],owned_boot=self._original['identity'],
            same_original_foreground_PID_UID_boot=True,app_resumed=True,root_uid=0,
            managed_driver_not_sampled=True,native_cycle_acknowledged=False)
        records.exact(value,expected)
        return expected

    def _record(self,name,raw):
        need(name not in self._written and type(raw) is bytes and 0 < len(raw) <= 4096)
        self._written.add(name);self._mono()
        need(self._call(self._ports.record,name,raw) is None);self._mono()

    def run(self):
        if self._state != 'NEW':
            self.close();need(False)
        self._state = 'RUNNING'
        try:
            read_deadline = min(self._deadline,records.until(self._original['input_clock']['sample_start_monotonic_ms'],38000))
            need(self._mono() <= read_deadline)
            raw = self._call(self._ports.wait_failed,
                'no_backup/owned-sync-quota-'+records.PHASE+'-failed-ready.json',read_deadline,self._owner)
            failed = failed_value(raw,self._original)
            need(self._mono() <= read_deadline)
            self._owner()
            sample = self._call(self._ports.clock)
            start,end,elapsed = records.clock(sample)
            need(self._last <= start <= end <= self._deadline
                 and failed['elapsed_ms'] <= elapsed <= records.until(failed['elapsed_ms'],2000))
            clock = dict(sample);self._last = end
            # No driver poll occurs in the post-finish reader/product guards or
            # failure clock. This first non-None is the first observed exit here.
            exit_at = None
            for _ in range(841):
                self._mono()
                code = self._call(self._ports.poll_driver)
                observed = self._mono()
                if code is not None:
                    need(type(code) is int and code == 0)
                    exit_at = observed;break
                self._owner();now = self._mono();need(now < self._deadline)
                need(self._call(self._ports.wait_turn,maximum_ms=50,deadline_ms=self._deadline) is None)
                need(self._mono() <= records.until(now,50))
            need(exit_at is not None)
            streams = self._call(self._ports.streams)
            need(type(streams) is tuple and len(streams) == 2)
            out,err = streams
            need(type(err) is bytes and err == b'')
            records.stdout(out)  # Unknown/private bytes are never hashed or exported.
            self._mono();owner = self._owner();owner_at = self._mono()
            final_owner = dict(owner);del final_owner['managed_driver_not_sampled']
            final_owner['original_driver_completed'] = True
            scope = self._original['scope']
            suffix = dict(failed_ready=failed,failed_clock=clock,
                instrument_result=dict(returncode=0,unknown_result=False,automatic_retry=False,
                    early_cleanup_not_success=False,no_final_product_force_stop_or_reopen=True),
                instrument_streams=dict(stdout_bytes=len(out),stdout_sha256=hashlib.sha256(out).hexdigest(),
                    stderr_bytes=0,stderr_sha256=records.EMPTY,raw_private_streams_exported=False),
                final_clock=dict(driver_exit_seen_monotonic_ms=exit_at,final_owner_verified_monotonic_ms=owner_at),
                final_owner=final_owner,phase_result=dict(schema=1,phase=records.PHASE,label=records.LABEL,
                    pid=self._original['original_lifetime']['pid'],passed=True,original_parent_intent_id=scope['parent_id'],
                    original_input_intent_id=scope['input_id'],quota_error_UI_observed=True,
                    native_original_state_verified=False,client_receipt_known=False))
            complete = dict(self._original,**suffix)
            projection = records.validate_completion(complete,out)
            for name,value in suffix.items():self._record(name.replace('_','-'),records.encoded(value))
            self._record('instrument-stdout',out)
            return dict(schema=1,records=json.loads(records.encoded(complete)),original_stdout=out,
                completion_projection=projection,controlled_port_reports_only=True,
                concrete_admission_verified=False,owned_stop_verified=False,runtime_verified=False)
        finally:
            # No failure reads, writes, retries, process termination or product action.
            self.close()
