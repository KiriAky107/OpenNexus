"""Actual orchestration/recorders/CAS/SQLite with controlled GUI and Provider/S3."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from subprocess import CompletedProcess,TimeoutExpired
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_release_sync_timeout as runtime
import android_release_sync_timeout_lifecycle as policy
import android_sync_timeout_cleanup as cleanup
import android_sync_stop_host as held_host
import android_sync_fixture as fixture
import android_sync_timeout_host as host
from test_android_release_observation import Device,ROOT
from test_android_release_sync_timeout_snapshot import original_fixture,retained,IDENTITY,host_records
from test_android_release_sync_stop_snapshot import SAVE,UPLOAD,HOLD,RELEASE,ASSOCIATION,DEVICE,VAULT,providers

PARENT='99999999-9999-4999-8999-999999999999'
INPUT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def phase_files(subject,label,phase,pid,hold,release,template):
    """Generate closed synthetic phase output; never replay a device action."""
    lifetime=dict(package=runtime.APP,pid=pid,uid=12345,start_ticks=5678)
    if phase==runtime.TIMEOUT:
        value,records,stdout,_,_=deepcopy(template)
        records['original-lifetime']=lifetime
        for key in ('input-owner','input-owner-at-command'):records[key]['original_lifetime']=lifetime
        records['held-service-marker']['operation_id']=hold['operation_id']
        records['original-response-release']['operation_id']=hold['operation_id']
        for key in ('release-control-intent','release-control-attempt'):
            records[key].update(operation_id=release['operation_id'],held_operation_id=hold['operation_id'])
        records['release-control-receipt']=release
        value.update(label=label,pid=pid)
    else:
        parent=dict(schema=1,phase=phase,parent_intent_id=PARENT,input_intent_id=INPUT,native_invocation_id_observed=False)
        records={'original-parent':parent,'original-lifetime':lifetime,
            'host-phase-intent':dict(phase=phase,entry=runtime.ENTRY,automatic_retry=False,native_timeout_verified=False),
            'instrument-result':dict(returncode=0,unknown_result=False,automatic_retry=False,early_cleanup_not_success=False,no_final_product_force_stop_or_reopen=True)}
        stdout=b'\n'.join((host.READ,('OPENNEXUS_SYNC_TIMEOUT_INPUT_SUMMARY '+host.SUMMARY.format(failed='false',released='false')).encode(),
            ('OPENNEXUS_RELEASE_SYNC_TIMEOUT_PHASE_OK '+phase).encode()))+b'\n'
        value=dict(phase=phase,label=label,pid=pid,passed=True,original_parent_intent_id=PARENT,original_input_intent_id=INPUT,
            same_live_instrumentation_through_original_release=False,failed_UI_observed_while_original_response_held=False,
            native_timeout_verified=False,native_cycle_acknowledged=False,client_receipt_known=False)
    for suffix,data in records.items():
        path=subject.output/(label+'-'+suffix+'.json')
        if suffix in ('release-control-intent','release-control-attempt') and path.exists():
            assert json.loads(path.read_bytes())==data
        else:runtime.record(path,data)
    runtime.exclusive(subject.output/(label+'-instrument.stdout'),stdout)
    return value


class TimeoutOrchestrator(unittest.TestCase):
    def exercise(self,root,mode='complete'):
        subject=object.__new__(runtime.ReleaseSyncTimeout);subject.owner=root/'owned';subject.owner.mkdir()
        control=fixture.initialize_control(subject.owner);fixture.freeze_target(subject.owner,VAULT)
        subject.output=root/'evidence';subject.output.mkdir();subject.tools=root/'driver-build';subject.tools.mkdir()
        subject.inputs=root/'inputs';subject.inputs.mkdir();(subject.inputs/'runtime-inputs.json').write_bytes(b'{}')
        subject.args=SimpleNamespace(serial='emulator-5562');subject.identity=deepcopy(IDENTITY)
        original_device=Device();original_device.rooted=True;original_device.identity=deepcopy(IDENTITY)
        timeline=[];cas=[];originals=[];live_pid=None
        with tempfile.TemporaryDirectory() as template_root:template=host_records(template_root)
        def device(*args,**kwargs):
            tokens=shlex.split(args[2]) if args[:2]==('shell','-T') else []
            if tokens==['am','force-stop','--user','0',runtime.APP]:
                self.assertEqual(kwargs['timeout'],2);self.assertTrue(original_device.active)
                self.assertIn(subject.timeout_stage,('timed-out-snapshot-stop','reconciled-snapshot-stop'))
                timeline.append(subject.timeout_stage)
                if mode=='unknown-snapshot-stop':raise TimeoutExpired(args,2,stderr=b'private unknown snapshot')
                original_device.active=False;original_device.stopped=True
                return CompletedProcess(args,0,b'',b'')
            out=None
            if tokens==['id','-u']:out=b'0\n'
            elif tokens==['pidof',runtime.APP] and original_device.active:out=(str(live_pid)+'\n').encode()
            elif tokens==['cat','/proc/'+str(live_pid)+'/stat']:out=(str(live_pid)+' (opennexus) '+' '.join(['0']*19+['5678'])+'\n').encode()
            elif tokens==['cat','/proc/'+str(live_pid)+'/cmdline']:out=runtime.APP.encode()+b'\0'
            elif tokens==['cat','/proc/'+str(live_pid)+'/status']:out=('Tgid:\t'+str(live_pid)+'\nUid:\t12345\t12345\t12345\t12345\n').encode()
            return CompletedProcess(args,0,out,b'') if out is not None else original_device(*args,**kwargs)
        subject.device=device;subject.shell=lambda *args,**kwargs:device('shell','-T',shlex.join(args),**kwargs)
        subject.stop=Mock(side_effect=AssertionError('implicit stop forbidden'))
        subject.failure_observations=Mock(side_effect=AssertionError('ordinary failure hook forbidden'))
        subject.prepare=Mock(side_effect=lambda:timeline.append('normal-zero-device-prepare'))
        subject.enable_test_ime=Mock();subject.restore_test_ime=Mock();subject.stage_sync_input=Mock()
        subject.manifest=dict(successor=dict(source_commit='d118b2ba63c922142f9fadee8772b6ec15791f48'))
        subject.identities=dict(successor=dict(source_commit=subject.manifest['successor']['source_commit']))
        login=dict(operation_id=ASSOCIATION,phase='active',kind='login',device_id=DEVICE,endpoint='https://owned.example/',account='owned-a')
        auth=dict(intents=[login],selection=dict(operation_id=login['operation_id']))
        def snapshot(label):
            if label=='timeout-failed-auth':self.assertTrue(subject.timeout_originals_preserved);self.assertIn('timed-out-snapshot-stop',timeline)
            return deepcopy(auth)
        subject.snapshot=Mock(side_effect=snapshot)
        sealed={'no_backup/sync-credentials/owned/owned.bin':dict(bytes=32,sha256='d'*64)}
        subject.observer=SimpleNamespace(identity=deepcopy(IDENTITY),observe_identity=Mock(return_value=deepcopy(IDENTITY)),ciphertext_fingerprints=Mock(return_value=sealed))
        auth_provider=dict(users=[dict(devices=[dict(id=DEVICE),dict(id='b'*32)],session_counts=[dict(device_id=DEVICE,sessions=1),dict(device_id='b'*32,sessions=1)]),dict(devices=[],session_counts=[])],requests=dict(counts={'login:200':2}))
        def observe(label):
            value=deepcopy(auth_provider)
            if mode=='auth-change' and label=='timeout-failed-read-auth':value['requests']['counts']['refresh:200']=1
            return value
        subject.observe=Mock(side_effect=observe)
        p0,p1,hold,marker,release,released=providers();p2=deepcopy(p1);p2['delivery']['counts']['changes:200']+=1
        def provider(label):
            value=deepcopy({'associated':p0,'timed-out':p1,'timed-out-read':p1,'reconciled':p2,'cold':p2,'timeout-failure':p1}[label])
            if mode=='read-write' and label=='timed-out-read':value['delivery']['counts']['revision:200']=2
            return value
        subject.sync_provider=Mock(side_effect=provider)
        subject.original_association=Mock(return_value=dict(operation_id=ASSOCIATION,preview_fingerprint='b'*64,remote_vault=VAULT))
        setup=Mock();setup.endpoint='https://owned.example/'
        setup.run.side_effect=lambda:timeline.append('independent-setup') or dict(vault=dict(vault_id=VAULT),device_id='b'*32,provider=p0)
        setup.session.return_value=dict(access_token='private-GET-token-never-device-or-artifact')
        subject.phases=[]
        def arm(owner,operation,kind,revision):
            cas.append(kind)
            if mode=='unknown-hold' and kind=='hold_revision':raise TimeoutError('private original control result')
            result=fixture.arm_control(owner,operation,kind,revision)
            if kind=='release':fixture.original.atomic_json(control/'fault-events'/(HOLD+'.released.json'),released,exclusive=True)
            if mode=='unknown-release' and kind=='release':raise TimeoutError('private original release result')
            return result
        def phase(name,label):
            nonlocal live_pid
            timeline.append(name);pid=4242+len(subject.phases)
            if name in runtime.PHASES:
                live_pid=pid;original_device.active=True;original_device.stopped=False
                if name==runtime.TIMEOUT:
                    holder=subject.timeout_control.held
                    fixture.original.atomic_json(control/'fault-events'/(HOLD+'.json'),marker,exclusive=True);holder.read_held()
                    if mode=='unknown-GUI':raise TimeoutError('private unknown GUI')
                    # Controlled original host output, with real once-only CAS
                    # and separate intent/attempt/receipt before phase return.
                    runtime.record(subject.output/(label+'-release-control-intent.json'),dict(operation_id=RELEASE,mode='release',expected_revision=1,held_operation_id=HOLD,automatic_retry=False,client_receipt_known=False))
                    runtime.record(subject.output/(label+'-release-control-attempt.json'),dict(operation_id=RELEASE,mode='release',expected_revision=1,held_operation_id=HOLD,automatic_retry=False,receipt_repair_allowed=False))
                    holder.release_attempted=True;holder.release_control=arm(subject.owner,RELEASE,'release',1)
                    # The fixture generator below writes these original two
                    # records too, so preserve them and generate the remainder.
                    entry=phase_files(subject,label,name,pid,hold,release,template)
                    if mode=='bad-host-clock':
                        path=subject.output/(label+'-failure-clock.json');value=json.loads(path.read_bytes());value['sample_start_monotonic_ms']=0;path.write_text(json.dumps(value),'utf-8')
                else:
                    self.assertTrue(subject.timeout_read_verified);self.assertTrue(subject.timeout_originals_preserved)
                    self.assertTrue((subject.output/'timeout-explicit-original-continue-intent.json').is_file())
                    entry=phase_files(subject,label,name,pid,hold,release,template)
                subject.phases.append(entry);return
            if mode=='same-read-PID' and label=='timeout-failed-read':pid=subject.phases[-1]['pid']
            original_device.active=False;original_device.stopped=True
            subject.phases.append(dict(phase=name,label=label,pid=pid,passed=True))
        subject.phase=Mock(side_effect=phase)
        def collect(value,label,*,stage):
            if label=='timed-out':self.assertEqual(timeline[-1],'timed-out-snapshot-stop')
            if label=='reconciled':self.assertEqual(timeline[-1],'reconciled-snapshot-stop')
            path=root/('fixture-'+stage);path.mkdir();raw,_=original_fixture(path,stage)
            before=retained(raw);originals.append((raw,before))
            original_device.files={ROOT+p.relative_to(raw).as_posix():p.read_bytes() for p in raw.rglob('*') if p.is_file() and p.name!='snapshot.json'}
            return policy.collect_original(value,label,stage=stage)
        with patch.object(runtime.ReleaseUpgrade,'phase',side_effect=lambda *args:timeline.append('one-new-GUI-Save')) as save, \
             patch.object(runtime,'Setup',return_value=setup),patch.object(runtime,'collect_original',side_effect=collect), \
             patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:HOLD if subject.timeout_control.operation is None else 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')), \
             patch.object(cleanup,'arm_control',side_effect=arm),patch.object(held_host,'arm_control',side_effect=arm), \
             patch.object(runtime,'read_original_object',return_value=b'wrong' if mode=='bad-S3' else runtime.BODY.encode()) as read_object, \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda value:original_device.active), \
             patch.dict(os.environ,dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA='c'*40)), \
             patch('android_runtime_probe.sys.platform','linux'):
            if mode=='complete':result=subject.run()
            else:
                with self.assertRaises((RuntimeError,TimeoutError,TimeoutExpired)):subject.run()
                result=None
        for raw,original in originals:self.assertEqual(retained(raw),original)
        self.assertEqual(timeline[0],'normal-zero-device-prepare');self.assertLess(timeline.index('login-a'),timeline.index('independent-setup'))
        save.assert_called_once_with(subject,'markdown','timeout-seed-note');subject.restore_test_ime.assert_called_once()
        subject.stop.assert_not_called();subject.failure_observations.assert_not_called()
        artifacts=b'\n'.join(p.read_bytes() for p in subject.output.rglob('*') if p.is_file())
        self.assertNotIn(b'private-GET-token',artifacts);self.assertNotIn(b'private unknown',artifacts);self.assertNotIn(b'private original',artifacts)
        if result:
            self.assertTrue(result['passed']);self.assertFalse(result['native_timeout_cause_independently_observed'])
            self.assertFalse(result['Native_RAM_invocation_id_observed']);self.assertFalse(result['whole_Sync_A4_complete'])
            self.assertEqual(result['original_Save_id'],SAVE);self.assertEqual(result['original_association_upload_id'],UPLOAD)
            self.assertEqual(cas,['hold_revision','release']);self.assertEqual(len(originals),6);read_object.assert_called_once()
            self.assertEqual(timeline.count('timed-out-snapshot-stop'),1);self.assertEqual(timeline.count('reconciled-snapshot-stop'),1)
            self.assertEqual([p['phase'] for p in subject.phases],['login-a','associate',runtime.TIMEOUT,'status',runtime.RESUME,'status'])
            self.assertEqual(len(list(subject.output.glob('*cipher-fingerprints.json'))),5)
        else:
            self.assertFalse((subject.output/'sync-timeout-runtime-proof.json').exists())
            self.assertFalse(json.loads((subject.output/'timeout-component-failure.json').read_bytes())['passed'])
            self.assertNotIn(runtime.RESUME,[p['phase'] for p in subject.phases])
        return subject,timeline,cas

    def test_actual_run_uses_two_explicit_snapshot_stops_and_same_failed_records_to_cold(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder))

    def test_unknown_GUI_releases_only_original_response_and_never_stops_or_opens_product(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,timeline,cas=self.exercise(Path(folder),'unknown-GUI')
            self.assertEqual(cas,['hold_revision','release']);self.assertNotIn('status',timeline)
            self.assertNotIn('timed-out-snapshot-stop',timeline)
            self.assertFalse(json.loads((subject.output/'timeout-failure-control-cleanup.json').read_bytes())['whole_timeout_component_passed'])

    def test_unknown_hold_or_release_never_reenters_CAS_or_repairs_receipt(self):
        for mode in ('unknown-hold','unknown-release'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                _,timeline,cas=self.exercise(Path(folder),mode)
                self.assertEqual(cas,['hold_revision'] if mode=='unknown-hold' else ['hold_revision','release'])
                self.assertNotIn('status',timeline);self.assertNotIn('timed-out-snapshot-stop',timeline)

    def test_bad_clock_or_S3_never_opens_product_for_another_cycle(self):
        for mode in ('bad-host-clock','bad-S3'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                _,timeline,cas=self.exercise(Path(folder),mode)
                self.assertNotIn('status',timeline);self.assertEqual(cas,['hold_revision','release'])

    def test_unknown_snapshot_stop_is_not_retried_by_failure_or_finally(self):
        with tempfile.TemporaryDirectory() as folder:
            _,timeline,_=self.exercise(Path(folder),'unknown-snapshot-stop')
            self.assertEqual(timeline.count('timed-out-snapshot-stop'),1);self.assertNotIn('status',timeline)

    def test_read_PID_Provider_write_or_auth_change_refuses_before_continue_intent(self):
        for mode in ('same-read-PID','read-write','auth-change'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,_,_=self.exercise(Path(folder),mode)
                self.assertFalse((subject.output/'timeout-explicit-original-continue-intent.json').exists())

    def test_adapter_only_accepts_two_exact_phases_and_control_only_for_TIMEOUT(self):
        subject=object.__new__(runtime.ReleaseSyncTimeout);subject.driver_entry='ReleaseSyncRuntimeProbe'
        original=object();subject.timeout_control=SimpleNamespace(held=original)
        def hosted(value,phase,label,*,held):
            self.assertIs(value,subject);self.assertEqual(value.driver_entry,runtime.ENTRY)
            self.assertIs(held,original if phase==runtime.TIMEOUT else None)
        with patch.object(runtime,'run_hosted_timeout_cycle',side_effect=hosted) as action:
            for phase in runtime.PHASES:subject.phase(phase,'timeout-bounded-label')
            self.assertEqual(action.call_count,2);subject.driver_entry='ReleaseSyncProcessRuntimeProbe'
            with self.assertRaises(RuntimeError):subject.phase(runtime.TIMEOUT,'timeout-refused')
            self.assertEqual(action.call_count,2)
        self.assertTrue(all(p not in runtime.ReleaseSync.allowed_phases for p in runtime.PHASES))


if __name__=='__main__':unittest.main()
