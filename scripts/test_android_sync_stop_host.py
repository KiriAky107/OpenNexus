"""Real recorder/control writers; only device, service delivery and GUI are fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_sync_stop_host as host
import android_sync_host_touch as old
from android_auth_fixture import atomic_json, read_json
from android_release_upgrade import ReleaseUpgrade
from android_sync_fixture import arm_control, control_root, freeze_target, initialize_control

P='10000000-0000-0000-0000-000000000001'
A='10000000-0000-0000-0000-000000000002'
B='10000000-0000-0000-0000-000000000003'
C='10000000-0000-0000-0000-000000000004'
VAULT='a'*32
STOPPED=b'OPENNEXUS_SYNC_STOPPED_UI inputs_finished=2 stopped_UI_observed=true release_signal_observed=false native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false\n'
RELEASED=b'OPENNEXUS_SYNC_STOP_RELEASE_GUARD inputs_finished=2 stopped_UI_observed=true release_signal_observed=true native_invocation_id_observed=false native_cycle_acknowledged=false raw_UI_recorded=false\n'
READ=b'OPENNEXUS_SYNC_AFTER_STOP_UI cycle_completed_observed=true native_original_state_verified=false raw_UI_recorded=false\n'


def parent(phase=host.STOP):
    return dict(schema=1,phase=phase,parent_intent_id=P,first_intent_id=A,second_intent_id=B if phase==host.STOP else None,native_invocation_id_observed=False)


def ready(phase=host.STOP,action='cycle-start'):
    return dict(schema=1,phase=phase,action=action,parent_intent_id=P,driver_intent_id=B if action=='cycle-stop' else A,
        package=host.APP,uid=12345,window=4,original=[100,200,500,600],visible=[100,200,500,600],x=300,y=400,elapsed_ms=1000)


class Process:
    def __init__(self,out):self.out=out;self.returncode=None;self.terminate=Mock(side_effect=self.end);self.kill=self.terminate
    def end(self):self.returncode=-15
    def poll(self):return self.returncode
    def wait(self,timeout):
        if self.returncode is None:raise subprocess.TimeoutExpired('fixture',timeout)
        return self.returncode
    def emit(self,value):self.out.write(value);self.out.flush()


class Harness:
    def __init__(self,folder,phase=host.STOP):
        self.root=Path(folder);self.owner=self.root/'owner';self.owner.mkdir()
        self.control_root=initialize_control(self.owner);freeze_target(self.owner,VAULT)
        self.control=arm_control(self.owner,C,'hold_revision',0)
        self.held=host.OriginalHeldRevision(self.owner,VAULT,self.control)
        self.phase=phase;self.events=[];self.signals={};self.first_finished=False;self.process=None;self.now=100.
        self.partial=False;self.partial_reads=0;self.unknown_tap=False;self.unknown_signal=False;self.no_hold=False
        self.no_stopped=False;self.owner_changed=False;self.wrong_marker=False;self.stale=False;self.bad_ready=False;self.early_exit=False
        self.change_after_tap=False;self.unknown_release=False;self.bad_readback=False
        out=self.root/'out';out.mkdir();tools=self.root/'tools';tools.mkdir()
        s=SimpleNamespace(output=out,tools=tools,owner=self.owner,args=SimpleNamespace(serial='emulator-5580'),adb=Path('not-executed-adb'),
            driver_entry=host.ENTRY,activity=host.APP+'/.MainActivity',identity={'api':'36','boot_id':P},phases=[],stop=Mock())
        s.observer=SimpleNamespace(observe_identity=Mock(side_effect=lambda:{'api':'36','boot_id':B} if self.owner_changed else s.identity))
        s.shell=self.shell;s.device=Mock(side_effect=self.device);s.original_command=ReleaseUpgrade.original_command.__get__(s)
        self.subject=s

    def shell(self,*args,**kwargs):
        def result(raw,code=0):return SimpleNamespace(returncode=code,stdout=raw,stderr=b'')
        if args==('pidof',host.APP):return result(b'1234\n')
        if args==('pm','list','packages','-U',host.APP):return result(b'package:cc.kronecker.opennexus uid:12345\n')
        if args==('cat','/proc/uptime'):return result(b'4.001 0.5\n' if self.stale else b'1.200 0.5\n')
        if args[:4]==('run-as',host.DRIVER,'sh','-c'):
            script=args[4];path=script.rsplit('cat ',1)[1]
            if path.endswith('.finish'):return result(b'finish!' if self.bad_readback else self.signals.get(path,b''),0 if path in self.signals else 1)
            if path=='no_backup/owned-sync-stop-'+self.phase+'-intent.json':value=parent(self.phase)
            else:
                action='cycle-stop' if '-cycle-stop-ready.json' in path else 'cycle-resume' if '-cycle-resume-ready.json' in path else 'cycle-start'
                if action=='cycle-stop':assert self.first_finished,'Stop ready read before original first finish'
                value=ready(self.phase,action)
                if self.bad_ready:value['uid']=54321
                if self.partial and self.partial_reads<2:
                    self.partial_reads+=1;return result(b'' if self.partial_reads==1 else b'{"schema":')
            return result(json.dumps(value,separators=(',',':')).encode())
        raise AssertionError('Unapproved device observation')

    def device(self,*args,**kwargs):
        def result():return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        assert args[:2]==('shell','-T')
        if args[2].startswith('am start'):self.events.append('launch');return result()
        if args[2].startswith('input touchscreen'):
            assert shlex.split(args[2])==['input','touchscreen','tap','300','400']
            number=1+sum(v.startswith('tap') for v in self.events);self.events.append('tap'+str(number))
            if self.unknown_tap:raise subprocess.TimeoutExpired(args,5,output=b'original-static',stderr=b'')
            if self.change_after_tap:self.owner_changed=True
            if self.phase==host.STOP and number==1 and not self.no_hold:
                marker=dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
                if self.wrong_marker:marker['mode']='drop_revision'
                atomic_json(self.control_root/'fault-events'/(C+'.json'),marker,exclusive=True);self.events.append('held-marker')
            if number==2:assert self.first_finished and self.held.marker is not None
            return result()
        assert args[2].startswith('run-as '+host.DRIVER)
        path=re.search(r'cat > (no_backup/[a-z0-9.\-]+)',args[2])[1]
        assert kwargs['input']==b'finish\n' and path not in self.signals
        if self.unknown_signal:raise subprocess.TimeoutExpired(args,5)
        self.signals[path]=b'finish\n'
        if path.endswith(A+'.finish'):
            if self.phase==host.STOP:
                assert self.held.marker is not None and (self.subject.output/'original-held-service-marker.json').is_file()
                self.events.append('first-finish');self.first_finished=True
            else:
                self.process.emit(READ+self.ok());self.process.returncode=0
        elif path.endswith(B+'.finish'):
            self.events.append('second-finish')
            if not self.no_stopped:self.process.emit(STOPPED)
            if self.early_exit:self.process.returncode=0
        else:
            assert path=='no_backup/owned-sync-stop-release-'+P+'.finish'
            assert read_json(self.control_root/'control.json')['mode']=='release'
            assert (self.control_root/'fault-events'/(C+'.released.json')).is_file()
            self.events.append('final-finish');self.process.emit(RELEASED+self.ok());self.process.returncode=0
        return result()

    def ok(self):return ('OPENNEXUS_RELEASE_SYNC_STOP_PHASE_OK '+self.phase+'\n').encode()
    def popen(self,args,**kwargs):
        assert args[-1].endswith(host.DRIVER+'/.'+host.ENTRY)
        self.process=Process(kwargs['stdout']);return self.process
    def release(self,owner,operation,mode,revision):
        assert owner==self.owner and mode=='release' and revision==1 and self.process.poll() is None
        assert STOPPED in Path(self.process.out.name).read_bytes()
        self.events.append('release-CAS')
        receipt=arm_control(owner,operation,mode,revision)
        atomic_json(self.control_root/'fault-events'/(C+'.released.json'),dict(operation_id=C,
            original_response_send_attempted=True,ASGI_send_returned=False,client_receipt_known=False),exclusive=True)
        if self.unknown_release:raise TimeoutError('fixture lost original release result')
        return receipt
    def execute(self):
        with patch.object(host,'require_hosted_emulator'),patch.object(host.subprocess,'Popen',side_effect=self.popen), \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=True),patch.object(host,'arm_control',side_effect=self.release), \
             patch.object(host.time,'monotonic',side_effect=lambda:self.now),patch.object(host.time,'sleep',side_effect=lambda delay:setattr(self,'now',self.now+delay)):
            host.run_hosted_stop_cycle(self.subject,self.phase,'original',held=self.held if self.phase==host.STOP else None)


class StopHost(unittest.TestCase):
    def test_closed_parent_ready_and_no_default_entry_scope_expansion(self):
        self.assertEqual(host.validate_parent(parent(),host.STOP),parent())
        self.assertEqual(host.validate_ready(ready(),parent(),'cycle-start',1200,12345),ready())
        for key,value in [('schema',True),('second_intent_id',A),('parent_intent_id','bad'),('native_invocation_id_observed',True),('extra','secret')]:
            bad=parent();bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):host.validate_parent(bad,host.STOP)
        for key,value in [('schema',True),('uid',True),('uid',54321),('window',True),('action','cycle-stop'),('phase',host.RESUME),
                          ('parent_intent_id',B),('driver_intent_id',B),('package','foreign'),('x',301),('visible',[0,0,20000,10]),('extra','secret')]:
            bad=ready();bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):host.validate_ready(bad,parent(),'cycle-start',1200,12345)
        for clock in (999,3001,True):
            with self.subTest(clock=clock),self.assertRaises(RuntimeError):host.validate_ready(ready(),parent(),'cycle-start',clock,12345)
        with self.assertRaises(RuntimeError):host.closed_json(b'{"schema":1,"schema":1}')
        for entry in ('ReleaseSyncRuntimeProbe','ReleaseSyncMutationRuntimeProbe','ReleaseSyncRecoveryRuntimeProbe'):
            self.assertFalse(old.admitted_phase(entry,host.STOP));self.assertFalse(old.admitted_phase(entry,host.RESUME))

    def test_real_recorders_two_inputs_one_held_connection_and_release_after_stopped_UI(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.partial=True;h.execute()
            self.assertEqual(h.events,['launch','tap1','held-marker','first-finish','tap2','second-finish','release-CAS','final-finish'])
            self.assertEqual(h.subject.stop.call_count,2);h.process.terminate.assert_not_called()
            self.assertEqual(h.subject.phases[0]['original_input_intent_ids'],[A,B])
            self.assertFalse(h.subject.phases[0]['native_original_state_verified'])
            for action in ('cycle-start','cycle-stop'):
                original=json.loads((h.subject.output/('original-'+action+'-host-touch-intent.json')).read_bytes())
                self.assertEqual(shlex.split(original['arguments'][2]),['input','touchscreen','tap','300','400'])
                owner=json.loads((h.subject.output/('original-'+action+'-host-touch-after-input-owner.json')).read_bytes())
                self.assertTrue(all(owner[k] for k in ('original_instrumentation_alive','same_original_PID','app_resumed','same_owned_boot_identity')))
            saved=json.loads((h.subject.output/'original-original-response-release.json').read_bytes())
            self.assertFalse(saved['ASGI_send_returned']);self.assertFalse(saved['client_receipt_known'])
            calls=h.subject.device.call_count
            with self.assertRaises((FileExistsError,RuntimeError)):h.execute()
            self.assertEqual(h.subject.device.call_count,calls)

    def test_explicit_read_one_original_input_and_real_GUI_result_without_new_control(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder,host.RESUME);h.execute()
            self.assertEqual(h.events,['launch','tap1']);self.assertEqual(len(h.signals),1)
            self.assertEqual(read_json(h.control_root/'control.json')['revision'],1)
            self.assertFalse((h.subject.output/'original-release-control-intent.json').exists())
            self.assertTrue(h.subject.phases[0]['passed'])

    def test_unknown_original_tap_or_finish_never_retried_and_preserves_real_recorder(self):
        for which in ('tap','finish'):
            with self.subTest(which=which),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.unknown_tap=which=='tap';h.unknown_signal=which=='finish'
                with self.assertRaises(subprocess.TimeoutExpired):h.execute()
                self.assertEqual(h.events.count('tap1'),1);self.assertNotIn('tap2',h.events);self.assertNotIn('release-CAS',h.events)
                self.assertFalse(h.subject.phases);h.process.terminate.assert_called_once()
                original=json.loads((h.subject.output/('original-cycle-start-host-touch-result.json' if which=='tap' else 'original-cycle-start-finish-result.json')).read_bytes())
                self.assertTrue(original['unknown_result']);self.assertFalse(original['automatic_retry'])
                self.assertFalse(h.signals)

    def test_missing_wrong_held_marker_never_finishes_first_input_or_sends_Stop(self):
        for wrong in (False,True):
            with self.subTest(wrong=wrong),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.no_hold=not wrong;h.wrong_marker=wrong
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('tap1'),1);self.assertNotIn('tap2',h.events);self.assertFalse(h.signals)
                self.assertFalse(h.subject.phases)

    def test_changed_owner_after_real_recorded_input_leaves_facts_and_no_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.change_after_tap=True
            with self.assertRaises(RuntimeError):h.execute()
            facts=json.loads((h.subject.output/'original-cycle-start-host-touch-after-input-owner.json').read_bytes())
            self.assertTrue(facts['original_instrumentation_alive'] and facts['same_original_PID'] and facts['app_resumed'])
            self.assertFalse(facts['same_owned_boot_identity']);self.assertFalse(facts['native_cycle_acknowledged'])
            self.assertEqual(h.events.count('tap1'),1);self.assertNotIn('tap2',h.events);self.assertFalse(h.signals)

    def test_unknown_release_CAS_preserves_same_original_intent_and_never_signals_or_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.unknown_release=True
            with self.assertRaises(TimeoutError):h.execute()
            self.assertEqual(h.events.count('release-CAS'),1);self.assertNotIn('final-finish',h.events)
            original=(h.subject.output/'original-release-control-intent.json').read_bytes()
            with self.assertRaises(RuntimeError):h.held.begin_release(h.subject.output,'another')
            self.assertEqual((h.subject.output/'original-release-control-intent.json').read_bytes(),original)
            self.assertFalse((h.subject.output/'original-release-control-receipt.json').exists());self.assertFalse(h.subject.phases)

    def test_wrong_signal_readback_never_sends_Stop_or_claims_GUI_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.bad_readback=True
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(h.events.count('tap1'),1);self.assertNotIn('tap2',h.events);self.assertFalse(h.subject.phases)

    def test_stale_foreign_ready_or_changed_boot_never_sends_original_input(self):
        for kind in ('stale','ready','boot'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.stale=kind=='stale';h.bad_ready=kind=='ready';h.owner_changed=kind=='boot'
                with self.assertRaises(RuntimeError):h.execute()
                self.assertNotIn('tap1',h.events);self.assertFalse(h.signals);self.assertFalse(h.subject.phases)

    def test_missing_stopped_UI_or_early_driver_exit_never_releases_or_claims_success(self):
        for kind in ('missing','exit'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder);h.no_stopped=kind=='missing';h.early_exit=kind=='exit'
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(h.events.count('tap2'),1);self.assertNotIn('release-CAS',h.events)
                self.assertFalse(h.subject.phases)
                self.assertTrue(json.loads((h.subject.output/'original-instrument-result.json').read_bytes())['unknown_result'])

    def test_foreign_driver_phase_or_extra_control_refused_before_profile_or_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder)
            for entry,phase,held in [('ReleaseSyncRuntimeProbe',host.STOP,h.held),(host.ENTRY,'cycle',h.held),(host.ENTRY,host.RESUME,h.held),(host.ENTRY,host.STOP,None)]:
                h.subject.driver_entry=entry
                with self.assertRaises(RuntimeError):host.run_hosted_stop_cycle(h.subject,phase,'bad',held=held)
            h.subject.stop.assert_not_called();h.subject.device.assert_not_called()

    def test_real_control_lineage_partial_marker_closed_types_and_once_only_release(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);self.assertIsNone(h.held.read_held())
            f=h.control_root/'fault-events'/(C+'.json');f.write_bytes(b'');self.assertIsNone(h.held.read_held())
            f.write_bytes(b'{');self.assertIsNone(h.held.read_held())
            marker=dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
            f.write_text(json.dumps(dict(marker,service_response_complete=1)),'utf-8')
            with self.assertRaises(RuntimeError):h.held.read_held()
            f.write_text(json.dumps(marker),'utf-8');self.assertEqual(h.held.read_held(),marker)
            h.held.begin_release(h.subject.output,'control')
            original=(h.subject.output/'control-release-control-receipt.json').read_bytes()
            with self.assertRaises(RuntimeError):h.held.begin_release(h.subject.output,'again')
            self.assertEqual((h.subject.output/'control-release-control-receipt.json').read_bytes(),original)
            released=h.control_root/'fault-events'/(C+'.released.json');released.write_text(json.dumps(dict(operation_id=C,original_response_send_attempted=True,ASGI_send_returned=False,client_receipt_known=False)),'utf-8')
            self.assertIs(h.held.read_released()['ASGI_send_returned'],False)
            released.write_text(json.dumps(dict(operation_id=C,original_response_send_attempted=True,ASGI_send_returned=False,client_receipt_known=True)),'utf-8')
            with self.assertRaises(RuntimeError):h.held.read_released()

    def test_existing_marker_or_changed_target_refused_before_any_GUI_or_control_write(self):
        for target in (False,True,'boolean-schema'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as folder:
                h=Harness(folder)
                if target=='boolean-schema':atomic_json(h.control_root/'target.json',dict(schema=True,vault_id=VAULT))
                elif target:atomic_json(h.control_root/'target.json',dict(schema=1,vault_id='b'*32))
                else:atomic_json(h.control_root/'fault-events'/(C+'.json'),dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False),exclusive=True)
                with self.assertRaises(RuntimeError):h.execute()
                h.subject.stop.assert_not_called();h.subject.device.assert_not_called()

    def test_wait_clock_rollback_and_expired_deadline_refuse_before_more_observation(self):
        s=SimpleNamespace();observe=Mock(return_value=None)
        with patch.object(host,'_owner'),patch.object(host.time,'monotonic',side_effect=[100.,99.]):
            with self.assertRaises(RuntimeError):host._wait(s,None,b'1',110.,observe)
        observe.assert_not_called()
        with patch.object(host.time,'monotonic',return_value=100.):
            with self.assertRaises(RuntimeError):host._wait(s,None,b'1',99.,observe)

    def test_unvalidated_private_streams_cannot_be_written_inside_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.subject.tools=h.subject.output/'bad-private';h.subject.tools.mkdir()
            with self.assertRaises(RuntimeError):h.execute()
            h.subject.stop.assert_not_called();h.subject.device.assert_not_called()


if __name__=='__main__':unittest.main()
