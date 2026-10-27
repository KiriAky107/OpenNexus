"""Real command journals and control CAS; Android, delivery and GUI remain fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_process_host as host
import android_sync_stop_host as controls
import android_sync_host_touch as old
import android_release_observation as observation
from android_auth_fixture import atomic_json,read_json
from android_release_upgrade import ReleaseUpgrade
from android_sync_fixture import arm_control,freeze_target,initialize_control

P='20000000-0000-0000-0000-000000000001'
A='20000000-0000-0000-0000-000000000002'
C='20000000-0000-0000-0000-000000000003'
VAULT='b'*32


def parent(phase=host.LOSS):
    return dict(schema=1,phase=phase,parent_intent_id=P,input_intent_id=A,native_invocation_id_observed=False)


def ready(phase=host.LOSS):
    return dict(schema=1,phase=phase,action='cycle-start' if phase==host.LOSS else 'cycle-resume',parent_intent_id=P,
        driver_intent_id=A,package=host.APP,uid=12345,window=4,original=[100,200,500,600],visible=[100,200,500,600],
        x=300,y=400,elapsed_ms=1000)


def loss_ready():
    return dict(schema=1,phase=host.LOSS,parent_intent_id=P,input_intent_id=A,package=host.APP,uid=12345,window=4,
        elapsed_ms=1000,product_UI_observation_closed=True,process_absence_verified=False)


class Process:
    def __init__(self,out):self.out=out;self.returncode=None;self.terminate=Mock(side_effect=self.end);self.kill=self.terminate
    def end(self):self.returncode=-15
    def poll(self):return self.returncode
    def wait(self,timeout):
        if self.returncode is None:raise subprocess.TimeoutExpired('fixture',timeout)
        return self.returncode
    def emit(self,value):self.out.write(value+b'\n');self.out.flush()


class Harness:
    def __init__(self,folder,phase=host.LOSS):
        self.root=Path(folder);self.owner=self.root/'owner';self.owner.mkdir()
        self.control_root=initialize_control(self.owner);freeze_target(self.owner,VAULT)
        control=arm_control(self.owner,C,'hold_revision',0)
        self.held=host.OriginalHeldRevision(self.owner,VAULT,control)
        self.phase=phase;self.events=[];self.signals={};self.now=100.;self.process=None;self.gone=False
        self.expected=False;self.first=False;self.kill_attempted=False;self.partial_reads=0
        self.variant='';self.lifetime_changes=0;self.after_intent_hook=None
        out=self.root/'out';out.mkdir();tools=self.root/'tools';tools.mkdir()
        identity=dict(avd='opennexus-ci-17-1',qemu='1',api='36',build_type='userdebug',debuggable='1',boot_id=P)
        s=SimpleNamespace(output=out,tools=tools,owner=self.owner,args=SimpleNamespace(serial='emulator-5562'),adb=Path('not-executed-adb'),
            driver_entry=host.ENTRY,activity=host.APP+'/.MainActivity',identity=identity,phases=[],stop=Mock())
        s.observer=SimpleNamespace(observe_identity=Mock(side_effect=lambda:dict(identity,boot_id=C)
            if self.variant=='boot-after-tap' and 'tap' in self.events or self.variant=='boot-after-kill' and self.kill_attempted else identity))
        s.shell=self.shell;s.device=Mock(side_effect=self.device);s.original_command=ReleaseUpgrade.original_command.__get__(s)
        self.subject=s

    def shell(self,*args,**kwargs):
        def result(raw,code=0,err=b''):return SimpleNamespace(returncode=code,stdout=raw,stderr=err)
        if args==('id','-u'):return result(b'2000\n' if self.variant=='root-after-kill' and self.kill_attempted else b'0\n')
        if args==('pidof',host.APP):
            if self.kill_attempted and self.variant=='replacement':return result(b'4321\n')
            if self.kill_attempted and self.variant=='unknown-absence':return result(b'',3,b'private-query-error')
            if self.kill_attempted and self.variant=='delayed-death' and self.now<100.1:return result(b'1234\n')
            return result(b'',1) if self.gone else result(b'1234\n')
        if args==('pm','list','packages','-U',host.APP):return result(b'package:cc.kronecker.opennexus.testdriver uid:12346\npackage:cc.kronecker.opennexus uid:12345\npackage:cc.kronecker.opennexus.testime uid:12347\n')
        if args==('cat','/proc/1234/stat'):
            fields=[b'0']*20;fields[19]=b'9876' if self.variant=='reused-before-kill' and (self.subject.output/'original-lifecycle-intent.json').exists() else b'4567'
            return result(b'1234 (opennexus) '+b' '.join(fields)+b'\n')
        if args==('cat','/proc/1234/cmdline'):return result((host.APP if self.variant!='foreign-cmdline' else 'cc.foreign').encode()+b'\0')
        if args==('cat','/proc/1234/status'):return result(b'Tgid:\t1234\nUid:\t12345\t12345\t12345\t12345\n')
        if args==('ps','-A','-o','PID,NAME'):
            raw=b'PID NAME\n1 init\n100 zygote64\n'
            if self.variant=='colon-process':raw+=b'4321 cc.kronecker.opennexus:service\n'
            if self.variant=='pid-reused-after-kill':raw+=b'1234 cc.foreign\n'
            return result(raw)
        if args==('dumpsys','package',host.APP):
            flag='false' if self.variant=='not-force-stopped' else 'true'
            return result(('Packages:\n  Package ['+host.APP+'] (abc):\n    User 0: stopped='+flag+'\n').encode())
        if args==('cat','/proc/uptime'):return result(b'4.001 0.5\n' if self.expected and self.variant=='stale-loss-ready' else b'1.200 0.5\n')
        if args[:4]==('run-as',host.DRIVER,'sh','-c'):
            path=args[4].rsplit('cat ',1)[1]
            if path.endswith('.finish'):
                return result(b'finish!' if self.variant=='bad-readback' else self.signals.get(path,b''),0 if path in self.signals else 1)
            if self.variant=='partial' and self.partial_reads<2:
                self.partial_reads+=1;return result(b'' if self.partial_reads==1 else b'{"schema":')
            if path=='no_backup/owned-sync-process-'+self.phase+'-intent.json':value=parent(self.phase)
            elif path.endswith('-loss-ready.json'):
                assert self.expected and self.first
                value=loss_ready()
                if self.variant=='bad-loss-parent':value['parent_intent_id']=C
            else:value=ready(self.phase)
            return result(json.dumps(value,separators=(',',':')).encode())
        raise AssertionError('Unapproved process fixture observation: '+repr(args))

    def device(self,*args,**kwargs):
        def result(code=0,err=b''):return SimpleNamespace(returncode=code,stdout=b'',stderr=err)
        assert args[:2]==('shell','-T')
        tokens=shlex.split(args[2])
        if tokens[:2]==['am','start']:self.events.append('launch');return result()
        if tokens[:2]==['input','touchscreen']:
            assert tokens==['input','touchscreen','tap','300','400'];self.events.append('tap')
            if self.variant=='unknown-tap':raise subprocess.TimeoutExpired(args,5,stderr=b'private-input-error')
            if self.phase==host.LOSS and self.variant!='missing-held':
                value=dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
                if self.variant=='wrong-held':value['service_status']=503
                atomic_json(self.control_root/'fault-events'/(C+'.json'),value,exclusive=True);self.events.append('held')
            return result()
        if tokens[:2]==['am','force-stop']:
            assert tokens==['am','force-stop','--user','0',host.APP] and kwargs['timeout']==2
            assert self.expected and self.first and self.process.poll() is None and self.held.marker is not None
            assert (self.subject.output/'original-lifecycle-intent.json').is_file()
            assert (self.subject.output/'original-lifecycle-owner.json').is_file()
            assert (self.subject.tools/'process-streams/original/lifecycle/original-force-stop-intent.json').is_file()
            self.events.append('force-stop');self.kill_attempted=True;self.gone=True
            if self.variant=='unknown-kill':raise subprocess.TimeoutExpired(args,2,stderr=b'private-lifecycle-error')
            if self.variant=='failed-kill':return result(1,b'private-lifecycle-error')
            if self.variant=='driver-exit-after-kill':self.process.returncode=0
            return result()
        assert tokens[:3]==['run-as',host.DRIVER,'sh']
        path=re.search(r'cat > (no_backup/[a-z0-9.\-]+)',args[2])[1]
        assert kwargs['input']==b'finish\n' and path not in self.signals
        self.signals[path]=b'finish\n'
        if path=='no_backup/owned-sync-process-touch-'+A+'.finish':
            if self.phase==host.LOSS:assert self.held.marker is not None
            self.first=True;self.events.append('input-finish')
            if self.variant=='unknown-finish':raise subprocess.TimeoutExpired(args,5)
            if self.phase==host.RESUME:self.finish_driver()
        elif path=='no_backup/owned-sync-process-expect-loss-'+P+'.finish':
            assert self.first;self.expected=True;self.events.append('expect-loss')
            if self.variant not in ('missing-boundary','delayed-boundary'):self.process.emit(host.BOUNDARY)
            if self.variant=='early-driver-exit':self.process.returncode=0
        else:
            assert path=='no_backup/owned-sync-process-released-'+P+'.finish' and self.gone
            assert read_json(self.control_root/'control.json')['mode']=='release'
            self.events.append('released-signal')
            if self.variant=='unknown-released-signal':raise subprocess.TimeoutExpired(args,5)
            self.finish_driver()
        return result(err=b'private-signal-stderr' if self.variant=='signal-stderr' else b'')

    def finish_driver(self):
        self.process.emit(host.RELEASED if self.phase==host.LOSS else host.READ)
        self.process.emit(('OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY '+host.SUMMARY.format(
            loss='true' if self.phase==host.LOSS else 'false',released='true' if self.phase==host.LOSS else 'false')).encode())
        self.process.emit(('OPENNEXUS_RELEASE_SYNC_PROCESS_PHASE_OK '+self.phase).encode())
        if self.variant=='unapproved-stream':self.process.emit(b'private-unapproved-AX-or-error-sentinel')
        self.process.returncode=0

    def popen(self,args,**kwargs):
        assert args[-1].endswith(host.DRIVER+'/.'+host.ENTRY)
        self.process=Process(kwargs['stdout']);return self.process

    def release(self,owner,operation,mode,revision):
        assert owner==self.owner and mode=='release' and revision==1 and self.process.poll() is None and self.gone
        assert (self.subject.output/'original-original-process-absence.json').is_file()
        self.events.append('release-CAS');receipt=arm_control(owner,operation,mode,revision)
        atomic_json(self.control_root/'fault-events'/(C+'.released.json'),dict(operation_id=C,
            original_response_send_attempted=True,ASGI_send_returned=False,client_receipt_known=False),exclusive=True)
        if self.variant=='unknown-release':raise TimeoutError('private release error')
        return receipt

    def execute(self):
        with patch.object(host,'require_hosted_emulator'),patch.dict(host.os.environ,{'GITHUB_RUN_ID':'17','GITHUB_RUN_ATTEMPT':'1'}), \
             patch.object(host.subprocess,'Popen',side_effect=self.popen),patch.object(controls,'arm_control',side_effect=self.release), \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda subject:not self.gone), \
             patch.object(host.time,'monotonic',side_effect=lambda:self.now), \
             patch.object(host.time,'sleep',side_effect=self.sleep):
            host.run_hosted_process_cycle(self.subject,self.phase,'original',held=self.held if self.phase==host.LOSS else None)

    def sleep(self,delay):
        self.now+=delay
        if self.variant=='delayed-boundary' and self.expected and self.now>=100.1:
            if host.BOUNDARY not in Path(self.process.out.name).read_bytes():self.process.emit(host.BOUNDARY)


class ProcessHost(unittest.TestCase):
    def test_actual_recorders_once_force_stop_after_boundary_absence_then_same_hold_release(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='partial';h.execute()
            self.assertEqual(h.events,['launch','tap','held','input-finish','expect-loss','force-stop','release-CAS','released-signal'])
            self.assertEqual(h.subject.stop.call_count,1);h.process.terminate.assert_not_called()
            intent=read_json(h.subject.output/'original-lifecycle-intent.json')
            self.assertEqual(shlex.split(intent['arguments'][2]),['am','force-stop','--user','0',host.APP])
            self.assertEqual(intent['original_lifetime'],dict(package=host.APP,pid=1234,uid=12345,start_ticks=4567))
            self.assertTrue(intent['product_UI_observation_closed']);self.assertFalse(intent['native_invocation_id_observed'])
            absence=read_json(h.subject.output/'original-original-process-absence.json')
            self.assertTrue(absence['original_driver_alive'] and absence['original_pid_absent'] and absence['user0_force_stopped'])
            released=read_json(h.subject.output/'original-original-response-release.json')
            self.assertFalse(released['ASGI_send_returned']);self.assertFalse(released['client_receipt_known'])
            self.assertEqual(h.subject.phases[0]['original_input_intent_id'],A)
            calls=h.subject.device.call_count
            with self.assertRaises((FileExistsError,RuntimeError)):h.execute()
            self.assertEqual(h.subject.device.call_count,calls)

    def test_explicit_read_has_one_original_input_no_control_or_tested_force_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder,host.RESUME);h.execute()
            self.assertEqual(h.events,['launch','tap','input-finish'])
            self.assertEqual(h.subject.stop.call_count,1);self.assertEqual(read_json(h.control_root/'control.json')['revision'],1)
            self.assertFalse(h.subject.phases[0]['actual_process_absence_observed'])
            self.assertFalse((h.subject.output/'original-lifecycle-intent.json').exists())

    def test_exact_parent_action_owner_geometry_clock_and_loss_ready_types(self):
        host.validate_parent(parent(),host.LOSS);host.validate_ready(ready(),parent(),1200,12345)
        host.validate_loss_ready(loss_ready(),parent(),1200,12345,4)
        for key,value in [('schema',True),('input_intent_id',P),('phase',host.RESUME),('native_invocation_id_observed',True),('extra','private')]:
            bad=parent();bad[key]=value
            with self.subTest(parent=key),self.assertRaises(RuntimeError):host.validate_parent(bad,host.LOSS)
        for key,value in [('schema',True),('uid',True),('window',True),('driver_intent_id',C),('action','cycle-stop'),('package','foreign'),
            ('visible',[0,0,20000,10]),('x',301),('elapsed_ms',True),('extra','private')]:
            bad=ready();bad[key]=value
            with self.subTest(ready=key),self.assertRaises(RuntimeError):host.validate_ready(bad,parent(),1200,12345)
        for key,value in [('schema',True),('window',True),('input_intent_id',C),('product_UI_observation_closed',False),('process_absence_verified',True)]:
            bad=loss_ready();bad[key]=value
            with self.subTest(loss=key),self.assertRaises(RuntimeError):host.validate_loss_ready(bad,parent(),1200,12345,4)
        for clock in (999,3001,True):
            with self.subTest(clock=clock),self.assertRaises(RuntimeError):host.validate_ready(ready(),parent(),clock,12345)
            with self.subTest(loss_clock=clock),self.assertRaises(RuntimeError):host.validate_loss_ready(loss_ready(),parent(),clock,12345,4)
        with self.assertRaises(RuntimeError):host.closed_json(b'{"schema":1,"schema":1}')

    def test_cross_entry_phase_control_and_private_artifact_paths_rejected_before_any_action(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder)
            for entry in (old.__name__,'ReleaseSyncRuntimeProbe','ReleaseSyncStopRuntimeProbe','ReleaseSyncRecoveryRuntimeProbe'):
                h.subject.driver_entry=entry
                with self.assertRaises(RuntimeError):host.run_hosted_process_cycle(h.subject,host.LOSS,'original',held=h.held)
            h.subject.driver_entry=host.ENTRY
            for phase in ('cycle','cycle-stop-original','cycle-drop-original','cycle-rename','cycle-incoming'):
                with self.assertRaises(RuntimeError):host.run_hosted_process_cycle(h.subject,phase,'original',held=h.held)
            with self.assertRaises(RuntimeError):host.run_hosted_process_cycle(h.subject,host.RESUME,'original',held=h.held)
            h.subject.tools=h.subject.output
            with self.assertRaises(RuntimeError):h.execute()
            h.subject.stop.assert_not_called();h.subject.device.assert_not_called()
            for entry in ('ReleaseSyncRuntimeProbe','ReleaseSyncMutationRuntimeProbe','ReleaseSyncRecoveryRuntimeProbe'):
                self.assertFalse(old.admitted_phase(entry,host.LOSS));self.assertFalse(old.admitted_phase(entry,host.RESUME))

    def test_unknown_original_input_and_finish_do_not_kill_release_or_repeat(self):
        for variant in ('unknown-tap','unknown-finish','boot-after-tap'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises((RuntimeError,subprocess.TimeoutExpired)):h.execute()
                self.assertEqual(h.events.count('tap'),1);self.assertNotIn('force-stop',h.events);self.assertNotIn('release-CAS',h.events)
                self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)
                self.assertTrue(read_json(h.subject.output/'original-instrument-result.json')['unknown_result'])

    def test_held_service_marker_and_expected_boundary_must_precede_lifecycle(self):
        for variant in ('missing-held','wrong-held','missing-boundary','early-driver-exit','stale-loss-ready','bad-loss-parent'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertNotIn('force-stop',h.events);self.assertNotIn('release-CAS',h.events);self.assertFalse(h.subject.phases)
                self.assertEqual(h.subject.stop.call_count,1)

    def test_lifecycle_unknown_original_command_preserves_private_recorder_without_reentry(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='unknown-kill'
            with self.assertRaises(subprocess.TimeoutExpired):h.execute()
            self.assertEqual(h.events.count('force-stop'),1);self.assertNotIn('release-CAS',h.events)
            value=read_json(h.subject.output/'original-lifecycle-result.json');self.assertTrue(value['unknown_result']);self.assertIsNone(value['returncode'])
            journal=h.subject.tools/'process-streams/original/lifecycle'
            self.assertEqual((journal/'original-force-stop.stderr').read_bytes(),b'private-lifecycle-error')
            exported=b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file())
            self.assertNotIn(b'private-lifecycle-error',exported);self.assertEqual(h.subject.stop.call_count,1)

    def test_known_failed_lifecycle_returncode_is_retained_and_never_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='failed-kill'
            with self.assertRaises(RuntimeError):h.execute()
            value=read_json(h.subject.output/'original-lifecycle-result.json')
            self.assertEqual(value['returncode'],1);self.assertFalse(value['unknown_result'])
            self.assertEqual(h.events.count('force-stop'),1);self.assertNotIn('release-CAS',h.events)

    def test_original_lifetime_rechecked_after_exclusive_lifecycle_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='reused-before-kill'
            with self.assertRaises(RuntimeError):h.execute()
            self.assertTrue((h.subject.output/'original-lifecycle-intent.json').exists())
            self.assertFalse((h.subject.output/'original-lifecycle-result.json').exists())
            self.assertNotIn('force-stop',h.events);self.assertFalse(h.subject.phases)

    def test_no_process_observations_reject_replacement_pid_services_unknown_root_or_driver_exit(self):
        for variant in ('replacement','colon-process','pid-reused-after-kill','unknown-absence','not-force-stopped','root-after-kill','boot-after-kill','driver-exit-after-kill'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('force-stop'),1);self.assertNotIn('release-CAS',h.events)
                self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)

    def test_delayed_original_pid_absence_is_read_only_wait_not_another_kill(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='delayed-death';h.execute()
            self.assertGreaterEqual(h.now,100.1);self.assertEqual(h.events.count('force-stop'),1)
            self.assertEqual(h.events.count('release-CAS'),1)

    def test_unknown_release_CAS_retains_original_ID_and_no_final_signal_or_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='unknown-release'
            with self.assertRaises(TimeoutError):h.execute()
            self.assertTrue(h.held.release_attempted);self.assertEqual(h.events.count('release-CAS'),1)
            self.assertNotIn('released-signal',h.events);self.assertFalse(h.subject.phases)
            intent=(h.subject.output/'original-release-control-intent.json').read_bytes()
            self.assertEqual(read_json(h.control_root/'control.json')['operation_id'],json.loads(intent)['operation_id'])
            self.assertFalse((h.subject.output/'original-release-control-receipt.json').exists())

    def test_unknown_final_signal_does_not_turn_confirmed_kill_and_release_into_success(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='unknown-released-signal'
            with self.assertRaises(subprocess.TimeoutExpired):h.execute()
            self.assertEqual(h.events.count('force-stop'),1);self.assertEqual(h.events.count('release-CAS'),1)
            self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)
            self.assertTrue(read_json(h.subject.output/'original-released-result.json')['unknown_result'])

    def test_unapproved_driver_stream_only_hashes_are_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='unapproved-stream'
            with self.assertRaises(RuntimeError):h.execute()
            self.assertFalse((h.subject.output/'original-instrument.stdout').exists())
            exported=b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file())
            self.assertNotIn(b'private-unapproved-AX-or-error-sentinel',exported)
            self.assertFalse(h.subject.phases)

    def test_wait_rejects_clock_reversal_and_closed_stream_refuses_duplicates_or_wrong_order(self):
        values=iter((100.,99.))
        with patch.object(host.time,'monotonic',side_effect=lambda:next(values)),self.assertRaises(RuntimeError):
            host._wait(102.,lambda:None,lambda:True)
        summary=('OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY '+host.SUMMARY.format(loss='false',released='false')).encode()
        ok=('OPENNEXUS_RELEASE_SYNC_PROCESS_PHASE_OK '+host.RESUME).encode()
        host._approved_stdout(b'\n'.join((host.READ,summary,ok)),host.RESUME)
        for raw in (b'\n'.join((summary,host.READ,ok)),b'\n'.join((host.READ,host.READ,summary,ok)),host.READ+b'\nprivate'):
            with self.assertRaises(RuntimeError):host._approved_stdout(raw,host.RESUME)

    def test_wait_deadline_still_applies_when_guard_or_observation_itself_finishes_late(self):
        for late in ('guard','observe'):
            with self.subTest(late=late):
                clock=[100.]
                def guard():
                    if late=='guard':clock[0]=103.
                def observe():
                    if late=='observe':clock[0]=103.
                    return True
                with patch.object(host.time,'monotonic',side_effect=lambda:clock[0]),self.assertRaises(RuntimeError):
                    host._wait(102.,guard,observe)

    def test_actual_finish_zero_result_code_wrapper_is_closed_static_GUI_metadata(self):
        summary=('OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY '+host.SUMMARY.format(loss='false',released='false')).encode()
        ok=('OPENNEXUS_RELEASE_SYNC_PROCESS_PHASE_OK '+host.RESUME).encode()
        raw=b'INSTRUMENTATION_STATUS: stream='+host.READ+b'\n\nINSTRUMENTATION_STATUS_CODE: 1\n'
        raw+=b'INSTRUMENTATION_STATUS: stream='+summary+b'\n\nINSTRUMENTATION_STATUS_CODE: 1\n'
        raw+=b'INSTRUMENTATION_RESULT: stream='+ok+b'\n\nINSTRUMENTATION_CODE: 0\n'
        host._approved_stdout(raw,host.RESUME)
        with self.assertRaises(RuntimeError):host._approved_stdout(raw+b'private-unapproved-wrapper\n',host.RESUME)

    def test_existing_identity_reader_is_bounded_on_private_copy_without_mutating_owner(self):
        identity=dict(avd='opennexus-ci-17-1',qemu='1',api='36',build_type='userdebug',debuggable='1',boot_id=P)
        replies={('getprop','ro.kernel.qemu'):b'1\n',('getprop','ro.build.version.sdk'):b'36\n',
            ('getprop','ro.build.type'):b'userdebug\n',('getprop','ro.debuggable'):b'1\n',
            ('cat','/proc/sys/kernel/random/boot_id'):(P+'\n').encode()}
        calls=[]
        def device(*args,**kwargs):
            calls.append((args,kwargs));self.assertEqual(kwargs['timeout'],1)
            raw=b'opennexus-ci-17-1\nOK\n' if args==('emu','avd','name') else replies[tuple(shlex.split(args[2]))]
            return SimpleNamespace(returncode=0,stdout=raw,stderr=b'')
        reader=object.__new__(observation.StoppedReleaseObserver);reader.serial='emulator-5562';reader.device=Mock()
        original=reader.device
        subject=SimpleNamespace(observer=reader,device=device,shell=Mock(),identity=identity,output=Path('unused'))
        with patch.object(observation,'require_hosted_emulator'),patch.dict(host.os.environ,{'GITHUB_RUN_ID':'17','GITHUB_RUN_ATTEMPT':'1'}):
            self.assertEqual(host._identity(subject),identity)
        self.assertEqual(len(calls),6);self.assertIs(reader.device,original);original.assert_not_called()
        subject.shell.return_value=SimpleNamespace(returncode=0,stdout=b'0\n',stderr=b'')
        self.assertEqual(host._query(subject,('id','-u'),16),b'0\n')
        self.assertEqual(subject.shell.call_args.kwargs,dict(check=False,timeout=1))

    def test_preinstrument_lifetime_refusal_is_recorded_before_instrumentation_or_input(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='foreign-cmdline'
            with self.assertRaises(RuntimeError):h.execute()
            value=read_json(h.subject.output/'original-preparation-failure.json')
            self.assertEqual(value['stage'],'original-process-lifetime');self.assertTrue(value['instrumentation_not_started'])
            self.assertIsNone(h.process);self.assertEqual(h.events,['launch'])
            self.assertFalse((h.subject.output/'original-instrument-intent.json').exists())

    def test_ready_file_may_precede_static_boundary_stream_without_another_action(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='delayed-boundary';h.execute()
            self.assertGreaterEqual(h.now,100.1);self.assertEqual(h.events.count('tap'),1)
            self.assertEqual(h.events.count('force-stop'),1);self.assertEqual(h.events.count('expect-loss'),1)

    def test_signal_bad_readback_or_unapproved_stderr_is_never_exported_or_retried(self):
        for variant in ('bad-readback','signal-stderr'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.variant=variant
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('input-finish'),1);self.assertNotIn('force-stop',h.events)
                self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)
                raw=b''.join(p.read_bytes() for p in h.subject.output.iterdir() if p.is_file())
                self.assertNotIn(b'private-signal-stderr',raw)


class StaticBoundaryAdmission(unittest.TestCase):
    def test_local_static_marker_budget_does_not_repeat_expensive_ADB_owner_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);owner=host._owner
            def measured_owner(*args):
                owner(*args)
                h.now+=.30  # Controlled latency, not a measurement of Android.
            with patch.object(host,'_owner',side_effect=measured_owner):h.execute()
            self.assertEqual(h.events.count('tap'),1);self.assertEqual(h.events.count('force-stop'),1)
            self.assertEqual(h.events.count('release-CAS'),1);self.assertEqual(h.subject.stop.call_count,1)

    def test_owner_change_after_static_marker_still_refuses_before_lifecycle_intent_or_kill(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);marker=host._marker
            def changed(path,token):
                result=marker(path,token)
                if result is not None:h.variant='foreign-cmdline'
                return result
            with patch.object(host,'_marker',side_effect=changed),self.assertRaises(RuntimeError):h.execute()
            self.assertNotIn('force-stop',h.events);self.assertNotIn('release-CAS',h.events)
            self.assertFalse((h.subject.output/'original-lifecycle-intent.json').exists())
            self.assertEqual(h.subject.stop.call_count,1)

    def test_static_boundary_driver_exit_or_late_local_read_still_rejects(self):
        for mode in ('driver-exit','late-local-read'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                clock=[100.];process=SimpleNamespace(returncode=None)
                process.poll=lambda:process.returncode
                def marker(path,token):
                    self.assertEqual(token,host.BOUNDARY)
                    if mode=='driver-exit':process.returncode=0
                    else:clock[0]+=.501
                    return True
                with patch.object(host.time,'monotonic',side_effect=lambda:clock[0]),patch.object(host,'_marker',side_effect=marker), \
                     patch.object(host,'_owner') as owner,patch.object(host,'_query') as query:
                    with self.assertRaises(RuntimeError):host._wait_boundary(process,Path(folder)/'stdout',100.5)
                    owner.assert_not_called();query.assert_not_called()


if __name__=='__main__':unittest.main()
