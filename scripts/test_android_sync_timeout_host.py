"""Actual original recorders/CAS and read guards; device, UI and elapsed clocks are fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_sync_timeout_host as host
import android_sync_host_touch as defaults
import android_sync_process_host as process_host
import android_release_upgrade as upgrade
import android_sync_timeout_owner as release_owner
from android_auth_fixture import atomic_json,read_json
from android_sync_fixture import arm_control
from test_android_sync_process_host import Harness as OriginalHarness,Process,P,A,C


def parent(phase=host.TIMEOUT):
    return dict(schema=1,phase=phase,parent_intent_id=P,input_intent_id=A,native_invocation_id_observed=False)


def ready(phase=host.TIMEOUT):
    return dict(schema=1,phase=phase,action='cycle-start' if phase==host.TIMEOUT else 'cycle-resume',parent_intent_id=P,
        driver_intent_id=A,package=host.APP,uid=12345,window=4,original=[100,200,500,600],visible=[100,200,500,600],x=300,y=400,elapsed_ms=1000)


class Harness(OriginalHarness):
    def __init__(self,root,phase=host.TIMEOUT):
        super().__init__(root,process_host.RESUME)
        self.phase=phase;self.subject.driver_entry=host.ENTRY
        self.tap_at=None;self.failed_at=None;self.failed_ready=False;self.failed_marker=False;self.release_signal_at=None
        self.command_streams=[]

    def elapsed(self):return 1200+int(round((self.now-100)*1000))

    def failed(self):
        value=dict(schema=1,phase=host.TIMEOUT,parent_intent_id=P,input_intent_id=A,package=host.APP,uid=12345,window=4,
            elapsed_ms=1200+int(round((self.failed_at-100)*1000)),failed_cycle_UI_observed=True,network_error_UI_observed=True,native_timeout_verified=False)
        if self.variant=='bad-failed-parent':value['parent_intent_id']=C
        if self.variant=='bad-failed-UID':value['uid']=12346
        if self.variant=='bad-failed-boolean':value['schema']=True
        if self.variant=='bad-failed-window':value['window']=5
        if self.variant=='fake-native-timeout':value['native_timeout_verified']=True
        return value

    def shell(self,*args,**kwargs):
        def result(raw,code=0,err=b''):return SimpleNamespace(returncode=code,stdout=raw,stderr=err)
        if args==('sh','-c',release_owner.PROPERTIES):
            identity=self.subject.identity
            return result(('\n'.join(identity[k] for k in ('qemu','api','build_type','debuggable','boot_id'))+'\n').encode())
        if args==('cat','/proc/uptime'):
            elapsed=3001 if self.variant=='stale-ready' else self.elapsed()
            return result((str(elapsed//1000)+'.'+str(elapsed%1000).zfill(3)+' 0.50\n').encode())
        if args==('id','-u') and self.variant=='root-after-tap' and self.tap_at is not None:return result(b'2000\n')
        if args[:4]==('run-as',host.DRIVER,'sh','-c'):
            path=args[4].rsplit('cat ',1)[1]
            if path.endswith('.finish'):
                return result(b'finish!' if self.variant=='bad-readback' else self.signals.get(path,b''),0 if path in self.signals else 1)
            if path=='no_backup/owned-sync-timeout-'+self.phase+'-intent.json':value=parent(self.phase)
            elif path.endswith('-failed-ready.json'):
                if self.variant=='no-failed-ready' or self.now<self.failed_at:return result(b'',1)
                self.failed_ready=True
                if self.variant=='driver-exit-before-release':self.process.returncode=0
                if self.variant not in ('missing-static','delayed-static') and not self.failed_marker:
                    self.process.emit(host.FAILED);self.failed_marker=True
                value=self.failed()
            else:
                value=ready(self.phase)
                if self.variant=='bad-ready-schema':value['schema']=True
                if self.variant=='bad-ready-UID':value['uid']=12346
                if self.variant=='bad-ready-geometry':value['x']=301
            if self.variant=='partial-ready' and self.partial_reads<2:
                self.partial_reads+=1;return result(b'' if self.partial_reads==1 else b'{"schema":')
            return result(json.dumps(value,separators=(',',':')).encode())
        return super().shell(*args,**kwargs)

    def device(self,*args,**kwargs):
        def result(code=0,out=b'',err=b''):return SimpleNamespace(returncode=code,stdout=out,stderr=err)
        if args==('emu','avd','name'):return result(out=(self.subject.identity['avd']+'\nOK\n').encode())
        assert args[:2]==('shell','-T');tokens=shlex.split(args[2])
        if tokens[:2]==['am','start']:self.events.append('launch');return result(out=b'owned Activity launch metadata\n')
        if tokens[:2]==['input','touchscreen']:
            assert tokens==['input','touchscreen','tap','300','400'] and kwargs['timeout']==5
            self.events.append('tap');self.tap_at=self.now
            self.failed_at=self.tap_at+(10 if self.variant=='early-network-error' else 37.5 if self.variant=='late-failed-ready' else 31)
            if self.variant=='unknown-tap':raise subprocess.TimeoutExpired(args,5,stderr=b'private-input-error')
            if self.phase==host.TIMEOUT and self.variant!='missing-held':
                value=dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
                if self.variant=='wrong-held':value['service_status']=503
                atomic_json(self.control_root/'fault-events'/(C+'.json'),value,exclusive=True);self.events.append('held')
            if self.variant=='late-held-admission':self.now+=4.9
            return result()
        assert tokens[:3]==['run-as',host.DRIVER,'sh']
        import re
        path=re.search(r'cat > (no_backup/[a-z0-9.\-]+)',args[2])[1]
        assert kwargs['input']==b'finish\n' and path not in self.signals;self.signals[path]=b'finish\n'
        if path=='no_backup/owned-sync-timeout-touch-'+A+'.finish':
            if self.phase==host.TIMEOUT:assert self.held.marker is not None
            self.first=True;self.events.append('input-finish')
            if self.variant=='unknown-finish':raise subprocess.TimeoutExpired(args,2,stderr=b'private-finish-error')
            if self.phase==host.RESUME:self.finish_driver()
        else:
            assert path=='no_backup/owned-sync-timeout-released-'+P+'.finish' and self.failed_ready and self.process.poll() is None
            assert read_json(self.control_root/'control.json')['mode']=='release'
            self.events.append('released-signal');self.release_signal_at=self.now
            if self.variant=='unknown-release-signal':raise subprocess.TimeoutExpired(args,2,stderr=b'private-release-signal-error')
            self.finish_driver()
        return result(err=b'private-signal-error' if self.variant=='signal-stderr' else b'')

    def finish_driver(self):
        self.process.emit(host.RELEASED if self.phase==host.TIMEOUT else host.READ)
        self.process.emit(('OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY '+host.SUMMARY.format(
            failed='true' if self.phase==host.TIMEOUT else 'false',released='true' if self.phase==host.TIMEOUT else 'false')).encode())
        self.process.emit(('OPENNEXUS_RELEASE_SYNC_TIMEOUT_PHASE_OK '+self.phase).encode())
        if self.variant=='unapproved-stream':self.process.emit(b'private-unapproved-stream-sentinel')
        self.process.returncode=0

    def popen(self,args,**kwargs):
        assert args[-1].endswith(host.DRIVER+'/.'+host.ENTRY)
        self.process=Process(kwargs['stdout']);return self.process

    def release(self,owner,operation,mode,revision):
        assert owner==self.owner and mode=='release' and revision==1 and self.process.poll() is None and self.failed_ready
        assert (self.subject.output/'original-release-control-intent.json').is_file()
        self.events.append('release-CAS');receipt=arm_control(owner,operation,mode,revision)
        if self.variant!='missing-released':atomic_json(self.control_root/'fault-events'/(C+'.released.json'),dict(operation_id=C,
            original_response_send_attempted=True,ASGI_send_returned=False,client_receipt_known=False),exclusive=True)
        if self.variant=='unknown-release':raise TimeoutError('private-CAS-error')
        if self.variant=='late-release-CAS':self.now+=4
        return receipt

    def sleep(self,delay):
        self.now+=delay
        if self.variant=='delayed-static' and self.failed_ready and not self.failed_marker:
            self.process.emit(host.FAILED);self.failed_marker=True

    def execute(self):
        with patch.object(host,'require_hosted_emulator'),patch.object(release_owner,'require_hosted_emulator'),patch.dict(host.os.environ,dict(GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1')), \
             patch.object(host.subprocess,'Popen',side_effect=self.popen),patch.object(host,'arm_control',side_effect=self.release), \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=True), \
             patch.object(host.time,'monotonic',side_effect=lambda:self.now),patch.object(host.time,'sleep',side_effect=self.sleep):
            return host.run_hosted_timeout_cycle(self.subject,self.phase,'original',held=self.held if self.phase==host.TIMEOUT else None)


class TimeoutHost(unittest.TestCase):
    def test_original_recorders_single_input_failed_UI_then_release_and_same_parent_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.execute()
            self.assertEqual(h.events,['launch','tap','held','input-finish','release-CAS','released-signal'])
            self.assertEqual(h.subject.stop.call_count,1);self.assertTrue(h.held.release_attempted)
            self.assertGreaterEqual(h.release_signal_at-h.tap_at,30);self.assertLessEqual(h.release_signal_at-h.tap_at,42)
            self.assertEqual(read_json(h.control_root/'control.json')['revision'],2)
            self.assertFalse(h.subject.phases[-1]['native_timeout_verified']);self.assertFalse(h.subject.phases[-1]['native_cycle_acknowledged'])
            window=read_json(h.subject.output/'original-original-window.json');self.assertEqual(window['host_release_bound_ms'],42000)
            self.assertEqual(window['hold_release_deadline_monotonic_ms']-window['preinput_monotonic_ms'],42000)
            self.assertEqual(read_json(h.subject.output/'original-instrument-result.json')['unknown_result'],False)
            self.assertEqual(read_json(h.subject.output/'original-released-readback.json')['bytes'],7)
            self.assertFalse(read_json(h.subject.output/'original-original-response-release.json')['client_receipt_known'])

    def test_explicit_read_is_one_new_input_with_no_hold_release_or_product_final_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder,host.RESUME);h.execute()
            self.assertEqual(h.events,['launch','tap','input-finish']);self.assertEqual(h.subject.stop.call_count,1)
            self.assertFalse(h.held.release_attempted);self.assertFalse((h.subject.output/'original-release-control-intent.json').exists())
            self.assertFalse(h.subject.phases[-1]['same_live_instrumentation_through_original_release'])

    def test_cross_driver_phase_control_activity_and_reentry_refused_before_another_launch(self):
        for entry,phase in ((process_host.ENTRY,host.TIMEOUT),(host.ENTRY,process_host.LOSS),(host.ENTRY,'cycle')):
            with self.subTest(entry=entry,phase=phase),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.subject.driver_entry=entry
                with self.assertRaises(RuntimeError):host.run_hosted_timeout_cycle(h.subject,phase,'original',held=h.held)
                self.assertFalse(h.events);h.subject.stop.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.subject.activity='cc.foreign/.Main'
            with self.assertRaises(RuntimeError):h.execute()
            h.subject.stop.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.execute();before=list(h.events)
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(h.events,before)
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder,host.RESUME)
            with self.assertRaises(RuntimeError):host.run_hosted_timeout_cycle(h.subject,host.RESUME,'original',held=h.held)
            h.subject.stop.assert_not_called()
        for entry in ('ReleaseSyncRuntimeProbe','ReleaseSyncMutationRuntimeProbe','ReleaseSyncRecoveryRuntimeProbe'):
            self.assertFalse(defaults.admitted_phase(entry,host.TIMEOUT));self.assertFalse(defaults.admitted_phase(entry,host.RESUME))

    def test_closed_parent_ready_failed_schemas_types_geometry_and_clocks(self):
        for name,value in (('schema',True),('phase',process_host.LOSS),('input_intent_id',P),('extra','private')):
            data=parent();data[name]=value
            with self.subTest(name=name),self.assertRaises(RuntimeError):host.validate_parent(data,host.TIMEOUT)
        for name,value in (('schema',True),('uid',True),('x',301),('window',-1),('elapsed_ms',True),('extra','private')):
            data=ready();data[name]=value
            with self.subTest(name=name),self.assertRaises(RuntimeError):host.validate_ready(data,parent(),1200,12345)
        for elapsed in (999,3001):
            with self.assertRaises(RuntimeError):host.validate_ready(ready(),parent(),elapsed,12345)
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.failed_at=131
            for name,value in (('schema',True),('uid',12346),('parent_intent_id',C),('window',5),('native_timeout_verified',True),('elapsed_ms',38000),('extra','private')):
                data=h.failed();data[name]=value
                with self.subTest(name=name),self.assertRaises(RuntimeError):host.validate_failed(data,parent(),ready(),32200)

    def test_bad_ready_or_owner_is_saved_and_refused_before_finish_or_release(self):
        for variant in ('bad-ready-schema','bad-ready-UID','bad-ready-geometry','stale-ready','boot-after-tap','root-after-tap'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertNotIn('input-finish',h.events);self.assertNotIn('release-CAS',h.events);self.assertFalse(h.subject.phases)

    def test_service_marker_or_its_original_wait_margin_cannot_be_replaced_by_503_or_reset_clock(self):
        for variant in ('wrong-held','missing-held','late-held-admission'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('tap'),1);self.assertNotIn('input-finish',h.events);self.assertNotIn('release-CAS',h.events)

    def test_unknown_input_or_finish_never_exports_private_stream_or_retries(self):
        for variant in ('unknown-tap','unknown-finish','bad-readback','signal-stderr'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises((RuntimeError,subprocess.TimeoutExpired)):h.execute()
                self.assertEqual(h.events.count('tap'),1);self.assertNotIn('release-CAS',h.events);self.assertFalse(h.subject.phases)
                public=b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file())
                self.assertNotIn(b'private-input-error',public);self.assertNotIn(b'private-finish-error',public);self.assertNotIn(b'private-signal-error',public)
                self.assertEqual(h.subject.stop.call_count,1)

    def test_wrong_failed_original_or_early_network_error_is_not_a_timeout_release_admission(self):
        for variant in ('bad-failed-parent','bad-failed-UID','bad-failed-boolean','bad-failed-window','fake-native-timeout','early-network-error','late-failed-ready','no-failed-ready'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('input-finish'),1);self.assertNotIn('release-CAS',h.events);self.assertFalse(h.held.release_attempted)

    def test_same_driver_and_exact_static_failed_marker_required_before_release(self):
        for variant in ('missing-static','driver-exit-before-release'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertNotIn('release-CAS',h.events);self.assertFalse(h.subject.phases)

    def test_delayed_static_marker_is_local_read_only_and_no_second_input(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='delayed-static';h.execute()
            self.assertEqual(h.events.count('tap'),1);self.assertEqual(h.events.count('release-CAS'),1)

    def test_release_CAS_unknown_or_late_is_never_reentered_or_sent_as_finished(self):
        for variant in ('unknown-release','late-release-CAS','missing-released','unknown-release-signal'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises((RuntimeError,TimeoutError,subprocess.TimeoutExpired)):h.execute()
                self.assertEqual(h.events.count('release-CAS'),1);self.assertTrue(h.held.release_attempted)
                self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)
                public=b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file())
                self.assertNotIn(b'private-CAS-error',public);self.assertNotIn(b'private-release-signal-error',public)

    def test_owner_changed_after_release_intent_blocks_actual_CAS(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);owner=host._release_owner
            def observe(*args):
                if (h.subject.output/'original-release-control-intent.json').exists():raise RuntimeError('controlled owner changed')
                return owner(*args)
            with patch.object(host,'_release_owner',side_effect=observe),self.assertRaises(RuntimeError):h.execute()
            self.assertNotIn('release-CAS',h.events);self.assertFalse(h.held.release_attempted)
            self.assertTrue((h.subject.output/'original-release-control-intent.json').is_file())
            self.assertFalse((h.subject.output/'original-release-control-attempt.json').exists())

    def test_slow_exclusive_release_intent_preserves_original_and_refuses_before_CAS_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);record=host.record
            def slow(path,value):
                record(path,value)
                if path.name=='original-release-control-intent.json':h.now+=2
            with patch.object(host,'record',side_effect=slow),self.assertRaises(RuntimeError):h.execute()
            self.assertNotIn('release-CAS',h.events);self.assertFalse(h.held.release_attempted)
            self.assertTrue((h.subject.output/'original-release-control-intent.json').is_file())
            self.assertFalse((h.subject.output/'original-release-control-attempt.json').exists())
            self.assertEqual(read_json(h.control_root/'control.json')['revision'],1)

    def test_private_unapproved_driver_stream_is_not_exported_as_completed_result(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='unapproved-stream'
            with self.assertRaises(RuntimeError):h.execute()
            self.assertFalse((h.subject.output/'original-instrument.stdout').exists());self.assertFalse(h.subject.phases)
            self.assertNotIn(b'private-unapproved-stream-sentinel',b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file()))

    def test_controlled_300ms_owner_rounds_keep_local_marker_reads_local_and_original_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);owner=host._owner;release=host._release_owner
            def observe(*args):
                result=owner(*args);h.now+=.3;return result
            def release_observe(*args):
                result=release(*args);h.now+=.3;return result
            with patch.object(host,'_owner',side_effect=observe),patch.object(host,'_release_owner',side_effect=release_observe):h.execute()
            self.assertEqual(h.events.count('tap'),1);self.assertEqual(h.events.count('release-CAS'),1)
            self.assertLessEqual(h.release_signal_at-h.tap_at,42)

    def test_clock_queries_unknown_shape_or_crossing_read_bound_refuse(self):
        for raw in (b'1.20 private',b'1.200 0.50\nextra',b'-1.20 0.5',b'NaN 0.5'):
            with self.subTest(raw=raw),patch.object(host,'_query',return_value=raw),self.assertRaises(RuntimeError):host._clock_sample(object())
        clock=[100.]
        def late(*args):clock[0]+=1.01;return b'1.20 0.50\n'
        with patch.object(host.time,'monotonic',side_effect=lambda:clock[0]),patch.object(host,'_query',side_effect=late),self.assertRaises(RuntimeError):host._clock_sample(object())

    def test_ready_freshness_checked_after_private_actual_command_intent_before_device_input(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);exclusive=upgrade.exclusive
            def delayed(path,raw):
                exclusive(path,raw)
                if path.name=='once-intent.json' and 'original-host-touch' in path.parts:h.now+=2.1
            with patch.object(upgrade,'exclusive',side_effect=delayed):
                try:h.execute()
                except RuntimeError:pass
            self.assertNotIn('tap',h.events);self.assertNotIn('input-finish',h.events)
            self.assertFalse(h.subject.phases)

    def test_static_wrappers_wrong_order_duplicates_and_other_phase_refused(self):
        summary=('OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY '+host.SUMMARY.format(failed='false',released='false')).encode()
        ok=('OPENNEXUS_RELEASE_SYNC_TIMEOUT_PHASE_OK '+host.RESUME).encode()
        raw=b'INSTRUMENTATION_STATUS: stream='+host.READ+b'\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS: stream='+summary+b'\nINSTRUMENTATION_RESULT: stream='+ok+b'\nINSTRUMENTATION_CODE: 0\n'
        host._approved_stdout(raw,host.RESUME)
        for invalid in (raw+b'private',raw+host.READ,b'\n'.join((summary,host.READ,ok))):
            with self.assertRaises(RuntimeError):host._approved_stdout(invalid,host.RESUME)
        with self.assertRaises(RuntimeError):host._approved_stdout(raw,process_host.RESUME)


if __name__=='__main__':unittest.main()
