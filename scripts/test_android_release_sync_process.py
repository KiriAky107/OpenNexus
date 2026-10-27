"""Actual run/writers/CAS/SQLite and validators with controlled GUI/Provider/S3."""
from copy import deepcopy
import hashlib
import json
import os
import shlex
from subprocess import CompletedProcess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_release_sync_process as runtime
import android_release_sync_process_lifecycle as policy
import android_sync_process_cleanup as cleanup
import android_sync_stop_host as held_host
import android_sync_fixture as fixture
import android_sync_process_host as process_host
import test_android_release_sync_process_snapshot as base
from test_android_release_observation import Device,ROOT

PARENT='99999999-9999-4999-8999-999999999999'
INPUT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
LIFE='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def lifecycle_files(subject,label,pid):
    parent=dict(schema=1,phase=runtime.LOSS,parent_intent_id=PARENT,input_intent_id=INPUT,native_invocation_id_observed=False)
    lifetime=dict(package=runtime.APP,pid=pid,uid=12345,start_ticks=5678)
    ready=dict(schema=1,phase=runtime.LOSS,action='cycle-start',parent_intent_id=PARENT,driver_intent_id=INPUT,package=runtime.APP,
        uid=12345,window=4,original=[100,200,500,600],visible=[100,200,500,600],x=300,y=400,elapsed_ms=1000)
    loss=dict(schema=1,phase=runtime.LOSS,parent_intent_id=PARENT,input_intent_id=INPUT,package=runtime.APP,uid=12345,window=4,
        elapsed_ms=1500,product_UI_observation_closed=True,process_absence_verified=False)
    intent=dict(operation_id=LIFE,arguments=['shell','-T','am force-stop --user 0 '+runtime.APP],parent_intent_id=PARENT,input_intent_id=INPUT,
        original_lifetime=lifetime,owned_boot=subject.identity,held_operation_id=base.HOLD,product_UI_observation_closed=True,
        automatic_retry=False,ordinary_cleanup_is_not_tested_process_loss=True,native_invocation_id_observed=False)
    empty=hashlib.sha256(b'').hexdigest()
    result=dict(returncode=0,unknown_result=False,automatic_retry=False,unvalidated_streams_exported=False,original_operation_id=LIFE,
        stdout_bytes=0,stdout_sha256=empty,stderr_bytes=0,stderr_sha256=empty)
    owner=dict(original_lifetime=lifetime,same_original_foreground_PID_UID_boot=True,original_instrumentation_alive=True,ready_age_ms=200,ready_age_bound_ms=2000)
    absence=dict(package=runtime.APP,original_pid=pid,product_uid=12345,original_pid_absent=True,all_owned_package_processes_absent=True,
        user0_force_stopped=True,same_owned_root_boot=True,original_driver_alive=True,original_driver_completed=False,
        raw_process_list_exported=False,native_cycle_acknowledged=False)
    for suffix,value in (('original-parent',parent),('ready',ready),('loss-ready',loss),('lifecycle-intent',intent),('lifecycle-result',result),
        ('lifecycle-owner',owner),('original-process-absence',absence),('final-original-process-absence',dict(absence,original_driver_alive=False,original_driver_completed=True))):
        runtime.record(subject.output/(label+'-'+suffix+'.json'),value)
    return dict(phase=runtime.LOSS,label=label,pid=pid,passed=True,original_parent_intent_id=PARENT,original_input_intent_id=INPUT,
        actual_process_absence_observed=True,same_live_instrumentation_through_original_release=True,Native_RAM_invocation_id_observed=False,
        native_original_state_verified=False,client_receipt_known=False)


class ProcessOrchestrator(unittest.TestCase):
    def exercise(self,root,mode='complete'):
        subject=object.__new__(runtime.ReleaseSyncProcess);subject.owner=root/'owned';subject.owner.mkdir()
        control=fixture.initialize_control(subject.owner);fixture.freeze_target(subject.owner,base.VAULT)
        subject.output=root/'evidence';subject.output.mkdir();subject.tools=root/'driver-build';subject.tools.mkdir()
        subject.inputs=root/'inputs';subject.inputs.mkdir();(subject.inputs/'runtime-inputs.json').write_bytes(b'{}')
        subject.args=SimpleNamespace(serial='emulator-5562');subject.identity=deepcopy(base.IDENTITY)
        subject.device=Device();subject.device.rooted=True;subject.device.identity=deepcopy(base.IDENTITY)
        timeline=[];cas=[];originals=[];read_pid=None
        original_device=subject.device
        def device(*args,**kwargs):
            tokens=shlex.split(args[2]) if args[:2]==('shell','-T') else []
            if tokens==['am','force-stop','--user','0',runtime.APP]:
                self.assertEqual(kwargs['timeout'],2);self.assertTrue(subject.process_raw_killed_preserved)
                self.assertEqual(subject.phases[-1]['phase'],runtime.RESUME)
                self.assertTrue((subject.output/'process-completed-read-snapshot-stop-intent.json').is_file())
                timeline.append('explicit-completed-read-snapshot-stop')
                original_device.active=False;original_device.stopped=True
                return CompletedProcess(args,0,b'',b'')
            out=None
            if tokens==['id','-u']:out=b'0\n'
            elif tokens==['pidof',runtime.APP]:out=(str(read_pid)+'\n').encode()
            elif tokens==['cat','/proc/'+str(read_pid)+'/stat']:
                out=(str(read_pid)+' (opennexus) '+' '.join(['0']*19+['5678'])+'\n').encode()
            elif tokens==['cat','/proc/'+str(read_pid)+'/cmdline']:out=runtime.APP.encode()+b'\0'
            elif tokens==['cat','/proc/'+str(read_pid)+'/status']:
                out=('Tgid:\t'+str(read_pid)+'\nUid:\t12345\t12345\t12345\t12345\n').encode()
            return CompletedProcess(args,0,out,b'') if out is not None else original_device(*args,**kwargs)
        subject.device=device
        subject.shell=lambda *args,**kwargs:device('shell','-T',shlex.join(args),**kwargs)
        subject.stop=Mock(side_effect=AssertionError('implicit process stop forbidden'))
        subject.failure_observations=Mock(side_effect=AssertionError('ordinary failure hook forbidden'))
        subject.prepare=Mock(side_effect=lambda:timeline.append('normal-zero-device-prepare'))
        subject.enable_test_ime=Mock();subject.restore_test_ime=Mock();subject.stage_sync_input=Mock()
        subject.manifest=dict(successor=dict(source_commit=base.PRODUCT));subject.identities=dict(successor=dict(source_commit=base.PRODUCT))
        login=dict(operation_id=base.ASSOCIATION,phase='active',kind='login',device_id=base.DEVICE,endpoint='https://owned.example/',account='owned-a')
        auth=dict(intents=[login],selection=dict(operation_id=login['operation_id']))
        def snapshot(label):
            if label=='process-killed-auth':self.assertTrue(subject.process_raw_killed_preserved);self.assertIn('raw-killed',timeline)
            return deepcopy(auth)
        subject.snapshot=Mock(side_effect=snapshot)
        sealed={'no_backup/sync-credentials/owned/owned.bin':dict(bytes=32,sha256='d'*64)}
        subject.observer=SimpleNamespace(identity=deepcopy(base.IDENTITY),observe_identity=Mock(return_value=deepcopy(base.IDENTITY)),
            ciphertext_fingerprints=Mock(return_value=sealed))
        auth_provider=dict(users=[dict(devices=[dict(id=base.DEVICE),dict(id='b'*32)],
            session_counts=[dict(device_id=base.DEVICE,sessions=1),dict(device_id='b'*32,sessions=1)]),dict(devices=[],session_counts=[])],requests=dict(counts={'login:200':2}))
        def observe(label):
            value=deepcopy(auth_provider)
            if mode=='auth-change' and label=='process-killed-read-auth':value['requests']['counts']['refresh:200']=1
            return value
        subject.observe=Mock(side_effect=observe)
        p0,p1,hold,marker,release,released=base.providers();p2=deepcopy(p1);p2['delivery']['counts']['changes:200']+=1
        def provider(label):
            if label=='process-failure':return deepcopy(p1)
            if label=='killed':self.assertTrue(subject.process_raw_killed_preserved)
            value=deepcopy({'associated':p0,'killed':p1,'killed-read':p1,'reconciled':p2,'cold':p2}[label])
            if mode=='read-write' and label=='killed-read':value['delivery']['counts']['revision:200']=2
            return value
        subject.sync_provider=Mock(side_effect=provider)
        subject.original_association=Mock(return_value=dict(operation_id=base.ASSOCIATION,preview_fingerprint='b'*64,remote_vault=base.VAULT))
        setup=Mock();setup.endpoint='https://owned.example/'
        setup.run.side_effect=lambda:timeline.append('independent-setup') or dict(vault=dict(vault_id=base.VAULT),device_id='b'*32,provider=p0)
        setup.session.return_value=dict(access_token='private-GET-token-never-device-or-artifact')
        subject.phases=[]
        def arm(owner,operation,kind,revision):
            cas.append(kind)
            if mode=='unknown-hold' and kind=='hold_revision':raise TimeoutError('private original control result')
            result=fixture.arm_control(owner,operation,kind,revision)
            if kind=='release':fixture.original.atomic_json(control/'fault-events'/(base.HOLD+'.released.json'),released,exclusive=True)
            if mode=='unknown-release' and kind=='release':raise TimeoutError('private original release result')
            return result
        def phase(name,label):
            nonlocal read_pid
            timeline.append(name);pid=4242+len(subject.phases)
            if name==runtime.LOSS:
                holder=subject.process_control.held;fixture.original.atomic_json(control/'fault-events'/(base.HOLD+'.json'),marker,exclusive=True)
                holder.read_held()
                if mode=='unknown-kill':original_device.active=True;raise TimeoutError('private unknown lifecycle command')
                holder.begin_release(subject.output,cleanup.LABEL)
                entry=lifecycle_files(subject,label,pid)
                if mode=='bad-lifecycle':
                    path=subject.output/(label+'-lifecycle-result.json');value=json.loads(path.read_bytes());value['unknown_result']=True
                    path.write_text(json.dumps(value),'utf-8')
                subject.phases.append(entry);return
            if mode=='same-read-PID' and label=='process-killed-read':pid=subject.phases[-1]['pid']
            if name==runtime.RESUME:
                read_pid=pid;original_device.active=True;original_device.stopped=False
                parent=dict(schema=1,phase=runtime.RESUME,parent_intent_id=PARENT,input_intent_id=INPUT,native_invocation_id_observed=False)
                for suffix,value in (('original-parent',parent),('original-lifetime',dict(package=runtime.APP,pid=pid,uid=12345,start_ticks=5678)),
                    ('instrument-result',dict(returncode=0,unknown_result=False,automatic_retry=False,early_cleanup_not_success=False,no_final_product_force_stop_or_reopen=True))):
                    runtime.record(subject.output/(label+'-'+suffix+'.json'),value)
                stdout=b'\n'.join((process_host.READ,('OPENNEXUS_SYNC_PROCESS_INPUT_SUMMARY '+process_host.SUMMARY.format(loss='false',released='false')).encode(),
                    ('OPENNEXUS_RELEASE_SYNC_PROCESS_PHASE_OK '+runtime.RESUME).encode()))+b'\n'
                (subject.output/(label+'-instrument.stdout')).write_bytes(stdout)
                subject.phases.append(dict(phase=name,label=label,pid=pid,passed=True,original_parent_intent_id=PARENT,
                    original_input_intent_id=INPUT,actual_process_absence_observed=False,same_live_instrumentation_through_original_release=False,
                    Native_RAM_invocation_id_observed=False,native_original_state_verified=False,client_receipt_known=False))
                return
            subject.phases.append(dict(phase=name,label=label,pid=pid,passed=True))
        subject.phase=Mock(side_effect=phase)
        def collect(value,label,*,stage):
            if label=='killed':self.assertEqual(timeline[-1],runtime.LOSS);timeline.append('raw-killed')
            path=root/('fixture-'+stage);path.mkdir();raw,_=base.original_fixture(path,stage,outcome='interrupted' if mode=='already-interrupted' else 'running')
            before=base.retained(raw);originals.append((raw,before))
            original_device.files={ROOT+p.relative_to(raw).as_posix():p.read_bytes() for p in raw.rglob('*') if p.is_file() and p.name!='snapshot.json'}
            return policy.collect_original(value,label,stage=stage)
        with patch.object(runtime.ReleaseUpgrade,'phase',side_effect=lambda *args:timeline.append('one-new-GUI-Save')) as save, \
             patch.object(runtime,'Setup',return_value=setup),patch.object(runtime,'collect_original',side_effect=collect), \
             patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:base.HOLD)),patch.object(cleanup,'arm_control',side_effect=arm), \
             patch.object(held_host,'uuid',SimpleNamespace(uuid4=lambda:base.RELEASE)),patch.object(held_host,'arm_control',side_effect=arm), \
             patch.object(runtime,'read_original_object',return_value=b'wrong' if mode=='bad-S3' else runtime.BODY.encode()) as read_object, \
             patch.object(process_host.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda value:original_device.active), \
             patch.dict(os.environ,dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA='c'*40)), \
             patch('android_runtime_probe.sys.platform','linux'):
            if mode in ('complete','already-interrupted','live-read'):result=subject.run()
            else:
                with self.assertRaises((RuntimeError,TimeoutError)):subject.run()
                result=None
        for raw,original in originals:self.assertEqual(base.retained(raw),original)
        self.assertEqual(timeline[0],'normal-zero-device-prepare');self.assertLess(timeline.index('login-a'),timeline.index('independent-setup'))
        save.assert_called_once_with(subject,'markdown','process-seed-note');subject.restore_test_ime.assert_called_once()
        subject.stop.assert_not_called();subject.failure_observations.assert_not_called()
        artifacts=b'\n'.join(p.read_bytes() for p in subject.output.rglob('*') if p.is_file())
        self.assertNotIn(b'private-GET-token',artifacts);self.assertNotIn(b'private unknown',artifacts);self.assertNotIn(b'private original',artifacts)
        if result:
            self.assertEqual(timeline.count('explicit-completed-read-snapshot-stop'),1)
            self.assertTrue(result['passed']);self.assertFalse(result['Native_RAM_invocation_id_observed']);self.assertFalse(result['whole_Sync_A4_complete'])
            self.assertEqual(result['original_Save_id'],base.SAVE);self.assertEqual(result['original_association_upload_id'],base.UPLOAD)
            self.assertEqual(result['original_open_transition']['from_outcome'],'interrupted' if mode=='already-interrupted' else 'running')
            self.assertEqual(cas,['hold_revision','release']);self.assertEqual(len(originals),6);read_object.assert_called_once()
            self.assertEqual([p['phase'] for p in subject.phases],['login-a','associate',runtime.LOSS,'status',runtime.RESUME,'status'])
            self.assertEqual(len(list(subject.output.glob('*cipher-fingerprints.json'))),5)
            self.assertFalse(any('stop-component' in p.name for p in subject.output.rglob('*')))
        else:
            self.assertFalse((subject.output/'sync-process-runtime-proof.json').exists())
            self.assertFalse(json.loads((subject.output/'process-component-failure.json').read_bytes())['passed'])
            self.assertNotIn(runtime.RESUME,[p['phase'] for p in subject.phases])
        return subject,timeline,cas

    def test_actual_run_preserves_raw_running_before_open_then_same_record_reconcile_and_cold(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder))

    def test_completed_read_leaves_live_product_until_explicit_snapshot_stop(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'live-read')

    def test_actual_run_preserves_raw_already_interrupted_without_relabelling_or_second_attempt(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'already-interrupted')

    def test_unknown_lifecycle_command_uses_one_explicit_response_cleanup_and_no_second_kill_or_open(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,timeline,cas=self.exercise(Path(folder),'unknown-kill')
            self.assertEqual(cas,['hold_revision','release']);self.assertNotIn('status',timeline)
            self.assertFalse((subject.output/'content-originals/killed').exists())
            value=json.loads((subject.output/'process-failure-control-cleanup.json').read_bytes())
            self.assertEqual(value['state'],'one_explicit_cleanup_release');self.assertFalse(value['whole_process_component_passed'])

    def test_unknown_hold_or_release_is_never_reentered_or_repaired(self):
        for mode in ('unknown-hold','unknown-release'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,timeline,cas=self.exercise(Path(folder),mode)
                self.assertEqual(cas,['hold_revision'] if mode=='unknown-hold' else ['hold_revision','release'])
                self.assertNotIn('status',timeline)

    def test_bad_original_journal_or_S3_preserves_raw_killed_and_refuses_product_open(self):
        for mode in ('bad-lifecycle','bad-S3'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,timeline,cas=self.exercise(Path(folder),mode)
                self.assertTrue((subject.output/'content-originals/killed/snapshot/snapshot.json').is_file())
                self.assertNotIn('status',timeline);self.assertEqual(cas,['hold_revision','release'])

    def test_read_PID_Provider_write_or_auth_change_refuses_before_continue_intent(self):
        for mode in ('same-read-PID','read-write','auth-change'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,_,_=self.exercise(Path(folder),mode)
                self.assertFalse((subject.output/'process-explicit-original-continue-intent.json').exists())

    def test_adapter_exact_entry_two_phases_and_control_only_for_loss(self):
        subject=object.__new__(runtime.ReleaseSyncProcess);subject.driver_entry='ReleaseSyncRuntimeProbe'
        original=object();subject.process_control=SimpleNamespace(held=original)
        def hosted(value,phase,label,*,held):
            self.assertIs(value,subject);self.assertEqual(value.driver_entry,runtime.ENTRY)
            self.assertIs(held,original if phase==runtime.LOSS else None)
        with patch.object(runtime,'run_hosted_process_cycle',side_effect=hosted) as action:
            for phase in runtime.PHASES:subject.phase(phase,'process-bounded-label')
            self.assertEqual(action.call_count,2)
            subject.driver_entry='ReleaseSyncStopRuntimeProbe'
            with self.assertRaises(RuntimeError):subject.phase(runtime.LOSS,'process-refused')
            self.assertEqual(action.call_count,2)
        self.assertTrue(all(p not in runtime.ReleaseSync.allowed_phases for p in runtime.PHASES))


if __name__=='__main__':unittest.main()
