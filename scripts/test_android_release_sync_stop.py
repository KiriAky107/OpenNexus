"""Real Stop run()/writers/SQLite and original controls, controlled GUI/PG/S3."""
from copy import deepcopy
from contextlib import chdir
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_release_sync_stop as runtime
import android_sync_stop_cleanup as cleanup
import android_sync_stop_host as host
import android_sync_fixture as fixture
import test_android_release_sync_stop_snapshot as base


class StopOrchestrator(unittest.TestCase):
    def exercise(self, root, mode='complete'):
        subject=object.__new__(runtime.ReleaseSyncStop)
        subject.owner=root/'owned';subject.owner.mkdir();control_root=fixture.initialize_control(subject.owner)
        fixture.freeze_target(subject.owner,base.VAULT)
        subject.output=root/'evidence';subject.output.mkdir();subject.tools=root/'driver-build';subject.tools.mkdir()
        if mode=='relative-evidence':subject.output=Path('evidence')
        subject.inputs=root/'inputs';subject.inputs.mkdir();(subject.inputs/'runtime-inputs.json').write_bytes(b'{}')
        subject.args=SimpleNamespace(serial='emulator-5562');subject.device=Mock()
        subject.identity=dict(api='36',boot='owned-boot');timeline=[];cas=[];copy_sources=[]
        subject.prepare=Mock(side_effect=lambda:timeline.append('normal-zero-device-prepare'))
        subject.enable_test_ime=Mock();subject.restore_test_ime=Mock();subject.stage_sync_input=Mock()
        subject.manifest=dict(successor=dict(source_commit='d118b2ba63c922142f9fadee8772b6ec15791f48'))
        subject.identities=dict(successor=dict(source_commit=subject.manifest['successor']['source_commit']))
        login=dict(operation_id=base.ASSOCIATION,phase='active',kind='login',device_id=base.DEVICE,
            endpoint='https://owned.example/',account='owned-a')
        auth=dict(intents=[login],selection=dict(operation_id=login['operation_id']))
        subject.snapshot=Mock(return_value=auth)
        sealed={'no_backup/sync-credentials/owned/owned.bin':dict(bytes=32,sha256='d'*64)}
        subject.observer=SimpleNamespace(identity=subject.identity,ciphertext_fingerprints=Mock(return_value=sealed))
        provider_auth=dict(users=[dict(devices=[dict(id=base.DEVICE),dict(id='b'*32)],
            session_counts=[dict(device_id=base.DEVICE,sessions=1),dict(device_id='b'*32,sessions=1)]),dict(devices=[],session_counts=[])],
            requests=dict(counts={'login:200':2}))
        def observe(label):
            value=deepcopy(provider_auth)
            if mode=='auth-change' and label=='stop-stopped-read-auth':value['requests']['counts']['refresh:200']=1
            return value
        subject.observe=Mock(side_effect=observe)
        p0,p1,_,marker,_,released=base.providers();p2=deepcopy(p1);p2['delivery']['counts']['changes:200']+=1
        def provider(label):
            value=deepcopy(dict(associated=p0,stopped=p1,**{'stopped-read':p1,'reconciled':p2,'cold':p2})[label])
            if mode=='read-write' and label=='stopped-read':value['delivery']['counts']['revision:200']=2
            return value
        subject.sync_provider=Mock(side_effect=provider)
        frozen=dict(operation_id=base.ASSOCIATION,preview_fingerprint='b'*64,remote_vault=base.VAULT)
        subject.original_association=Mock(return_value=frozen)
        setup=Mock();setup.endpoint='https://owned.example/'
        setup.run.side_effect=lambda:timeline.append('independent-setup') or dict(vault=dict(vault_id=base.VAULT),device_id='b'*32,provider=p0)
        setup.session.return_value=dict(access_token='controlled-private-session-never-record')
        phases=[];subject.phases=phases
        def release_arm(owner,operation,kind,revision):
            self.assertEqual(kind,'release');cas.append(('release',operation))
            if mode=='unknown-release-before':raise TimeoutError('private result unavailable')
            result=fixture.arm_control(owner,operation,kind,revision)
            fixture.original.atomic_json(control_root/'fault-events'/(base.HOLD+'.released.json'),dict(released,ASGI_send_returned=mode!='sender-failed'),exclusive=True)
            if mode=='unknown-release-missing-receipt':(control_root/'control-receipts'/(operation+'.json')).unlink()
            if mode in ('unknown-release-after','unknown-release-missing-receipt','cleanup-release-unknown'):raise TimeoutError('private result unavailable')
            return result
        def arm(owner,operation,kind,revision):
            self.assertEqual(kind,'hold_revision');cas.append(('hold',operation))
            if mode=='unknown-hold-before':raise TimeoutError('private result unavailable')
            result=fixture.arm_control(owner,operation,kind,revision)
            if mode=='unknown-hold-after':raise TimeoutError('private result unavailable')
            return result
        def phase(name,label):
            timeline.append(name)
            if name==runtime.STOP:
                held=subject.stop_control.held
                fixture.original.atomic_json(control_root/'fault-events'/(base.HOLD+'.json'),marker,exclusive=True)
                if mode in ('unknown-input','cleanup-release-unknown'):raise TimeoutError('private GUI result unavailable')
                self.assertEqual(held.read_held(),marker)
                held.begin_release(subject.output,cleanup.LABEL)
            pid=len(phases)+100
            if mode=='same-read-PID' and label=='stop-stopped-read':pid=phases[-1]['pid']
            phases.append(dict(phase=name,label=label,pid=pid,passed=True))
        subject.phase=Mock(side_effect=phase)
        stages={'seeded':'seeded','associated':'associated','stopped':'stopped','stopped-read':'stopped-read','reconciled':'reconciled','cold':'cold'}
        def observer(device,serial,output,paths):
            self.assertEqual(paths,[runtime.NOTE]);self.assertEqual(serial,'emulator-5562')
            stage=stages[output.name];source_root=root/('original-'+stage);source_root.mkdir()
            original,_=base.original_fixture(source_root,stage)
            original_bytes={f.relative_to(original).as_posix():f.read_bytes() for f in original.rglob('*') if f.is_file()}
            copy_sources.append((original,original_bytes))
            def copy_stopped(data_root,label):
                self.assertEqual(label,'snapshot');shutil.copytree(original,output/label)
            return SimpleNamespace(identity=None,observe_identity=lambda:subject.identity,discover_data_root=lambda:'owned-root',
                copy_stopped=copy_stopped,output=output)
        if mode=='duplicate-hold-intent':(subject.output/'stop-hold-control-intent.json').write_bytes(b'original-intent-retained')
        with patch.object(runtime.ReleaseUpgrade,'phase',side_effect=lambda *args:timeline.append('one-new-GUI-Save')) as save, \
             patch.object(runtime,'Setup',return_value=setup),patch.object(runtime,'StoppedReleaseObserver',side_effect=observer), \
             patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:base.HOLD)),patch.object(cleanup,'arm_control',side_effect=arm), \
             patch.object(host,'arm_control',side_effect=release_arm), \
             patch.object(runtime,'read_original_object',return_value=b'bad-bytes' if mode=='bad-S3' else runtime.BODY.encode()) as read_object, \
             patch.dict(runtime.os.environ,dict(GITHUB_SHA='a'*40,GITHUB_RUN_ID='1234')):
            if mode in ('complete','sender-failed','relative-evidence'):result=subject.run()
            else:
                with self.assertRaises((RuntimeError,TimeoutError,FileExistsError)):subject.run()
                result=None
        for original,raw in copy_sources:
            self.assertEqual(raw,{f.relative_to(original).as_posix():f.read_bytes() for f in original.rglob('*') if f.is_file()})
        self.assertEqual(timeline[0],'normal-zero-device-prepare')
        self.assertLess(timeline.index('login-a'),timeline.index('independent-setup'))
        save.assert_called_once_with(subject,'markdown','stop-seed-note')
        subject.restore_test_ime.assert_called_once()
        artifacts=b'\n'.join(f.read_bytes() for f in subject.output.rglob('*') if f.is_file())
        self.assertNotIn(b'controlled-private-session',artifacts);self.assertNotIn(b'private result unavailable',artifacts)
        self.assertFalse(any('auth.sqlite' in str(f) or 'finish' in f.name for f in subject.output.rglob('*') if f.is_file()))
        if result:
            self.assertTrue(result['passed']);self.assertFalse(result['Native_RAM_invocation_id_observed'])
            self.assertFalse(result['client_receipt_known']);self.assertFalse(result['whole_Sync_A4_complete'])
            self.assertEqual(result['original_Save_id'],base.SAVE);self.assertEqual(result['original_association_upload_id'],base.UPLOAD)
            self.assertEqual([p['phase'] for p in phases],['login-a','associate',runtime.STOP,'status',runtime.RESUME,'status'])
            self.assertEqual([v[0] for v in cas],['hold','release'])
            read_object.assert_called_once();self.assertEqual(len(copy_sources),6)
            self.assertEqual(len(list(subject.output.glob('*cipher-fingerprints.json'))),5)
            for path in ('stop-hold-control-intent.json','stop-hold-control-receipt.json','stop-original-held-response.json',
                         'stop-original-release-response.json','stop-explicit-original-continue-intent.json'):
                original=(subject.output/path).read_bytes()
                with self.assertRaises(FileExistsError):host.record(subject.output/path,json.loads(original))
                self.assertEqual((subject.output/path).read_bytes(),original)
        else:
            self.assertFalse((subject.output/'sync-stop-runtime-proof.json').exists())
            failure=json.loads((subject.output/'stop-component-failure.json').read_bytes());self.assertFalse(failure['passed'])
            self.assertNotIn(runtime.RESUME,[p['phase'] for p in phases])
            if mode in ('duplicate-hold-intent','unknown-hold-before','unknown-hold-after'):
                self.assertNotIn(runtime.STOP,timeline);read_object.assert_not_called()
            if mode=='duplicate-hold-intent':
                self.assertEqual(cas,[]);self.assertEqual((subject.output/'stop-hold-control-intent.json').read_bytes(),b'original-intent-retained')
            if mode=='unknown-hold-before':
                self.assertEqual([v[0] for v in cas],['hold']);self.assertEqual(fixture.original.read_json(control_root/'control.json')['revision'],0)
            if mode=='unknown-hold-after':
                self.assertEqual([v[0] for v in cas],['hold']);self.assertEqual(fixture.original.read_json(control_root/'control.json')['revision'],1)
                self.assertFalse((subject.output/'stop-hold-control-receipt.json').exists())
            if mode in ('unknown-input','cleanup-release-unknown'):
                value=json.loads((subject.output/'stop-failure-control-cleanup.json').read_bytes())
                self.assertFalse(value['whole_Stop_component_passed']);self.assertTrue(value['original_response_release_observed'])
                self.assertEqual(value['state'],'one_explicit_cleanup_release' if mode=='unknown-input' else 'cleanup_release_readonly_reconciled')
                self.assertTrue((subject.output/'stop-failure-cleanup-release-control-intent.json').is_file());read_object.assert_not_called()
            if mode.startswith('unknown-release'):
                value=json.loads((subject.output/'stop-failure-control-cleanup.json').read_bytes())
                self.assertEqual(value['state'],'original_release_unconfirmed_no_retry' if mode=='unknown-release-before' else 'original_release_readonly_reconciled')
                self.assertFalse((subject.output/'stop-failure-cleanup-release-control-intent.json').exists());read_object.assert_not_called()
            if mode=='unknown-release-missing-receipt':
                readonly=json.loads((subject.output/'stop-failure-original-release-readonly.json').read_bytes())
                self.assertFalse(readonly['original_control_receipt_observed'])
                self.assertFalse((control_root/'control-receipts'/(cas[-1][1]+'.json')).exists())
            if mode not in ('duplicate-hold-intent','unknown-hold-before','unknown-hold-after'):
                self.assertEqual([v[0] for v in cas],['hold','release'])
        return subject,cas

    def test_complete_actual_run_recorders_and_six_SQLite_stages_bind_original_Stop_read_and_cold(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder))

    def test_actual_sender_failure_never_becomes_client_receipt_or_another_revision(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'sender-failed')

    def test_real_workflow_relative_evidence_directory_is_bound_to_its_canonical_original(self):
        with tempfile.TemporaryDirectory() as folder,chdir(folder):self.exercise(Path(folder),'relative-evidence')

    def test_existing_original_hold_intent_refuses_before_CAS_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'duplicate-hold-intent')

    def test_unknown_original_hold_before_or_after_write_has_no_GUI_or_retry_or_receipt_repair(self):
        for mode in ('unknown-hold-before','unknown-hold-after'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),mode)

    def test_unknown_GUI_input_keeps_wholefalse_then_one_explicit_original_response_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'unknown-input')

    def test_unknown_original_release_is_readonly_reconciled_without_reentry_or_new_control(self):
        for mode in ('unknown-release-before','unknown-release-after','unknown-release-missing-receipt'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),mode)

    def test_unknown_cleanup_release_is_readonly_reconciled_and_never_retried(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),'cleanup-release-unknown')

    def test_bad_original_S3_stops_before_read_or_reconcile_and_keeps_actual_stopped_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,_=self.exercise(Path(folder),'bad-S3')
            self.assertTrue((subject.output/'content-originals/stopped/snapshot/vault'/runtime.NOTE).is_file())
            self.assertFalse((subject.output/'stop-explicit-original-continue-intent.json').exists())

    def test_new_PID_Provider_write_and_auth_change_are_rejected_before_continue_intent(self):
        for mode in ('same-read-PID','read-write','auth-change'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,_=self.exercise(Path(folder),mode)
                self.assertFalse((subject.output/'stop-explicit-original-continue-intent.json').exists())

    def test_adapter_binds_only_new_exact_entry_two_phases_and_control_only_for_Stop(self):
        subject=object.__new__(runtime.ReleaseSyncStop);subject.driver_entry='ReleaseSyncRuntimeProbe'
        original=object();subject.stop_control=SimpleNamespace(held=original)
        def hosted(value,phase,label,*,held):
            self.assertIs(value,subject);self.assertEqual(value.driver_entry,host.ENTRY)
            self.assertIs(held,original if phase==host.STOP else None)
        with patch.object(runtime,'run_hosted_stop_cycle',side_effect=hosted) as action:
            for phase in (host.STOP,host.RESUME):subject.phase(phase,'stop-bounded-label')
            self.assertEqual(action.call_count,2)
            subject.driver_entry='ReleaseSyncRecoveryRuntimeProbe'
            with self.assertRaises(RuntimeError):subject.phase(host.STOP,'stop-refused')
            self.assertEqual(action.call_count,2)
        self.assertNotIn(host.STOP,runtime.ReleaseSync.allowed_phases)
        self.assertNotIn(host.RESUME,runtime.ReleaseSync.allowed_phases)


class StopCleanupBoundaries(unittest.TestCase):
    def test_cleanup_requires_original_failure_and_refuses_repeat_foreign_vault_alias_or_control(self):
        for mode in ('no-failure','repeat','target','owner-alias','foreign-control'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);owner=root/'owned';owner.mkdir();control=fixture.initialize_control(owner);fixture.freeze_target(owner,base.VAULT)
                output=root/'evidence';output.mkdir()
                if mode=='owner-alias':
                    with self.assertRaises(RuntimeError):cleanup.OriginalStopControl(owner/'..'/'owned',output,base.VAULT)
                    continue
                original=cleanup.OriginalStopControl(owner,output,base.VAULT)
                with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:base.HOLD)):original.arm()
                if mode!='no-failure':host.record(output/'stop-component-failure.json',dict(passed=False,automatic_retry=False))
                if mode=='repeat':original.cleanup_attempted=True
                if mode=='target':fixture.original.atomic_json(control/'target.json',dict(schema=1,vault_id='f'*32))
                if mode=='foreign-control':fixture.original.atomic_json(control/'control.json',dict(revision=2,operation_id=base.RELEASE,mode='release'))
                before={str(p.relative_to(control)):p.read_bytes() for p in control.rglob('*') if p.is_file()}
                with patch.object(host,'arm_control') as arm:
                    with self.assertRaises(RuntimeError):original.cleanup()
                    arm.assert_not_called()
                self.assertEqual(before,{str(p.relative_to(control)):p.read_bytes() for p in control.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
