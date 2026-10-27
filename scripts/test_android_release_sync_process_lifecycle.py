"""Real readonly observer/copies/SQLite and CLI policy; controlled device reads."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_release_auth as ordinary
import android_release_sync_process_lifecycle as lifecycle
from android_release_observation import DATABASES
from test_android_release_observation import Device,ROOT
from test_android_release_sync_process_snapshot import original_fixture,IDENTITY,retained


ENV=dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1')


def subject(root,stage='killed',*,outcome='running'):
    original_root=root/'fixture';original_root.mkdir();raw,writers=original_fixture(original_root,stage,outcome=outcome,wal=True)
    device=Device();device.rooted=True;device.identity=deepcopy(IDENTITY)
    device.files={ROOT+p.relative_to(raw).as_posix():p.read_bytes() for p in raw.rglob('*') if p.is_file() and p.name!='snapshot.json'}
    output=root/'evidence';output.mkdir();tools=root/'driver-build';tools.mkdir()
    obj=SimpleNamespace(device=device,args=SimpleNamespace(serial='emulator-5562'),identity=deepcopy(IDENTITY),output=output,tools=tools,
        process_stage='original-process-loss-GUI',process_control=None,observer=SimpleNamespace(identity=deepcopy(IDENTITY),ciphertext_fingerprints=Mock(return_value={})),
        snapshot=Mock(),observe=Mock(),sync_provider=Mock(),stop=Mock(side_effect=AssertionError('second product force-stop forbidden')),
        phase=Mock(side_effect=AssertionError('product open forbidden')),failure_observations=Mock(side_effect=AssertionError('ordinary failure hook forbidden')))
    return obj,raw,writers


class ProcessOriginals(unittest.TestCase):
    def test_all_six_collectors_retain_exact_DB_WAL_SHM_and_project_only_new_readonly_copies(self):
        for stage in lifecycle.STAGES:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
                obj,raw,writers=subject(Path(folder),stage);before=retained(raw)
                try:
                    value,body=lifecycle.collect_original(obj,stage,stage=stage)
                    self.assertEqual(value['process_stage'],stage);self.assertEqual(value['owned_snapshot_identity'],IDENTITY)
                    self.assertEqual(body,(raw/'vault'/lifecycle.NOTE).read_bytes());self.assertEqual(retained(raw),before)
                    public=obj.output/'content-originals'/stage/'snapshot'
                    for name in value['raw_sha256']:self.assertEqual((public/name).read_bytes(),(raw/name).read_bytes())
                    self.assertTrue(all((public/(db+s)).is_file() for db in DATABASES for s in ('-wal','-shm')))
                    self.assertTrue((obj.tools/('process-original-observer-'+stage)/'stopped-check-0001-processes.txt').is_file())
                    self.assertFalse(any(p.name.endswith(('-processes.txt','-package.txt')) or p.suffix in ('.stdout','.stderr') for p in obj.output.rglob('*')))
                    obj.stop.assert_not_called();obj.phase.assert_not_called();obj.snapshot.assert_not_called()
                    self.assertFalse(any('force-stop' in str(call) or 'am start' in str(call) or call==('root',) for call in obj.device.calls))
                    if stage=='killed':self.assertTrue(obj.process_raw_killed_preserved)
                finally:
                    for writer in writers:writer.close()

    def test_active_product_unstopped_user_root_or_changed_boot_refuses_before_any_export_or_auth(self):
        for mode in ('running','not-user-stopped','root','boot'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
                obj,raw,writers=subject(Path(folder))
                try:
                    if mode=='running':obj.device.active=True
                    if mode=='not-user-stopped':obj.device.stopped=False
                    if mode=='root':obj.device.rooted=False
                    if mode=='boot':obj.device.identity['boot_id']='a'*8+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*12
                    with self.assertRaises(RuntimeError):lifecycle.collect_original(obj,'killed',stage='killed')
                    self.assertFalse((obj.output/'content-originals').exists());obj.stop.assert_not_called();obj.snapshot.assert_not_called()
                    self.assertFalse(any('force-stop' in str(call) for call in obj.device.calls))
                finally:
                    for writer in writers:writer.close()

    def test_publisher_rejects_foreign_private_extra_absent_WAL_or_duplicate_manifest_before_export(self):
        for mode in ('auth','extra','wrong-hash','missing-WAL','duplicate','boot','alias'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);fixture=root/'fixture';fixture.mkdir();raw,writers=original_fixture(fixture,'killed')
                self.assertFalse(writers);manifest=json.loads((raw/'snapshot.json').read_bytes());output=root/'public'
                if mode=='auth':manifest['files']['mobile-sync-auth.sqlite3']=None
                if mode=='extra':(raw/'extra-private').write_bytes(b'must-not-export')
                if mode=='wrong-hash':manifest['files']['vault/'+lifecycle.NOTE]['sha256']='f'*64
                if mode=='missing-WAL':(raw/(DATABASES[0]+'-wal')).write_bytes(b'must-be-absent')
                if mode=='boot':manifest['identity']['boot_id']='b'*8+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*12
                if mode=='alias':output=root/'other'/'..'/'public'
                encoded=json.dumps(manifest)
                if mode=='duplicate':encoded=encoded[:-1]+',"package":"foreign"}'
                (raw/'snapshot.json').write_text(encoded,'utf-8')
                with self.assertRaises(RuntimeError):lifecycle.publish_original(raw,output,IDENTITY,include_note=True)
                self.assertFalse(output.exists())

    def test_existing_public_original_private_directory_overlap_and_unapproved_stage_refused(self):
        for mode in ('existing','overlap','stage'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
                obj,raw,writers=subject(Path(folder))
                try:
                    if mode=='existing':
                        public=obj.output/'content-originals/killed/snapshot';public.mkdir(parents=True);(public/'original').write_bytes(b'original')
                    if mode=='overlap':obj.tools=obj.output/'driver-build';obj.tools.mkdir()
                    with self.assertRaises(RuntimeError):lifecycle.collect_original(obj,'killed' if mode!='stage' else 'arbitrary',stage='killed')
                    if mode=='existing':self.assertEqual((public/'original').read_bytes(),b'original')
                    obj.stop.assert_not_called();obj.snapshot.assert_not_called()
                finally:
                    for writer in writers:writer.close()


class ProcessFailurePolicy(unittest.TestCase):
    def test_actual_guarded_action_failure_freezes_false_before_cleanup_then_DB_only_before_auth(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
            obj,raw,writers=subject(Path(folder));events=[]
            def cleanup():
                failure=json.loads((obj.output/'process-component-failure.json').read_bytes());self.assertFalse(failure['passed'])
                self.assertFalse((obj.output/'content-originals/failure').exists());events.append('cleanup')
            obj.process_control=SimpleNamespace(operation=None,cleanup=Mock(side_effect=cleanup))
            def auth(label):
                manifest=json.loads((obj.output/'content-originals/failure/snapshot/snapshot.json').read_bytes())
                self.assertNotIn('vault/'+lifecycle.NOTE,manifest['files']);events.append('auth-after-raw-DB')
            obj.snapshot.side_effect=auth
            try:
                with self.assertRaises(TimeoutError):lifecycle.run_guarded(obj,lambda:(_ for _ in ()).throw(TimeoutError('private original result')))
                self.assertEqual(events,['cleanup','auth-after-raw-DB']);obj.stop.assert_not_called();obj.phase.assert_not_called()
                obj.failure_observations.assert_not_called();obj.process_control.cleanup.assert_called_once()
                artifacts=b'\n'.join(p.read_bytes() for p in obj.output.rglob('*') if p.is_file())
                self.assertNotIn(b'private original result',artifacts);self.assertFalse(any('stop-component' in p.name for p in obj.output.rglob('*')))
                self.assertFalse((obj.output/'content-originals/failure/snapshot/vault'/lifecycle.NOTE).exists())
                self.assertTrue((obj.output/'content-originals/failure/snapshot'/DATABASES[1]).is_file())
                before=retained(obj.output)
                with self.assertRaises(RuntimeError):lifecycle.handle_failure(obj,RuntimeError())
                self.assertEqual(retained(obj.output),before)
            finally:
                for writer in writers:writer.close()

    def test_unknown_live_process_failure_is_unavailable_without_stop_auth_or_open(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
            obj,raw,writers=subject(Path(folder));obj.device.active=True
            try:
                lifecycle.handle_failure(obj,TimeoutError('private unknown kill result'))
                failure=json.loads((obj.output/'process-component-failure.json').read_bytes());self.assertFalse(failure['passed'])
                self.assertTrue((obj.output/'process-failure-original-unavailable.json').exists())
                obj.stop.assert_not_called();obj.phase.assert_not_called();obj.snapshot.assert_not_called()
                obj.observer.ciphertext_fingerprints.assert_not_called();obj.observe.assert_not_called();obj.sync_provider.assert_not_called()
                self.assertFalse(any('force-stop' in str(c) or 'am start' in str(c) for c in obj.device.calls))
            finally:
                for writer in writers:writer.close()

    def test_cleanup_and_private_auth_failures_preserve_wholefalse_and_do_not_call_ordinary_hooks(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
            obj,raw,writers=subject(Path(folder))
            obj.process_control=SimpleNamespace(operation=None,cleanup=Mock(side_effect=TimeoutError('private cleanup')))
            obj.snapshot.side_effect=TimeoutError('private auth')
            try:
                lifecycle.handle_failure(obj,RuntimeError('private original'))
                self.assertTrue((obj.output/'process-failure-control-cleanup-unavailable.json').exists())
                self.assertTrue((obj.output/'process-failure-auth-unavailable.json').exists())
                obj.observe.assert_called_once();obj.sync_provider.assert_called_once();obj.stop.assert_not_called();obj.failure_observations.assert_not_called()
            finally:
                for writer in writers:writer.close()

    def test_CLI_success_failure_existing_unknown_record_and_constructor_failure_never_final_stop(self):
        for mode in ('success','failure','guarded-failure','existing-record','constructor-failure'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,ENV),patch('android_runtime_probe.sys.platform','linux'):
                root=Path(folder);obj,raw,writers=subject(root);obj.device.active=True
                obj.run=Mock(return_value=dict(kind='controlled-process-source-fixture',passed=True,abi='x86_64',physical_phone_verified=False,published=False))
                if mode=='failure':obj.run.side_effect=TimeoutError('private failure')
                if mode=='guarded-failure':obj.run.side_effect=lambda:lifecycle.run_guarded(obj,lambda:(_ for _ in ()).throw(RuntimeError('private failure')))
                if mode=='existing-record':
                    obj.run.side_effect=TimeoutError();(obj.output/'process-component-failure.json').write_bytes(b'original-unknown')
                constructor=Mock(return_value=obj)
                if mode=='constructor-failure':constructor.side_effect=RuntimeError('private constructor')
                args=[v for key in ('inputs','sdk','java','output') for v in ('--'+key,str(obj.output if key=='output' else root/key))]
                stream=io.StringIO()
                try:
                    with patch.object(ordinary,'main',side_effect=AssertionError('ordinary CLI forbidden')),redirect_stdout(stream):
                        code=lifecycle.main(constructor,args)
                    self.assertEqual(code,0 if mode=='success' else 1);self.assertNotIn('private',stream.getvalue())
                    obj.stop.assert_not_called();obj.phase.assert_not_called();obj.failure_observations.assert_not_called()
                    if mode=='existing-record':self.assertEqual((obj.output/'process-component-failure.json').read_bytes(),b'original-unknown')
                    if mode=='success':self.assertFalse((obj.output/'process-component-failure.json').exists())
                finally:
                    for writer in writers:writer.close()

    def test_failure_kind_and_stage_are_closed_without_exception_messages_or_arbitrary_names(self):
        with tempfile.TemporaryDirectory() as folder:
            obj=SimpleNamespace(output=Path(folder),process_stage='private arbitrary stage',process_control=None)
            error=type('PrivateProviderSecret',(Exception,),{})('private message')
            failure=lifecycle.freeze_failure(obj,error)
            self.assertEqual(failure['stage'],'unclassified');self.assertEqual(failure['error_type'],'Exception')
            self.assertNotIn('private',json.dumps(failure))

    def test_changed_retained_private_original_during_export_is_rejected_even_after_valid_public_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture=root/'fixture';fixture.mkdir();raw,_=original_fixture(fixture,'killed')
            public=root/'public';writer=lifecycle.exclusive
            def changed(target,data):
                writer(target,data)
                if target.name==lifecycle.NOTE:(raw/'vault'/lifecycle.NOTE).write_bytes(b'changed after original export')
            with patch.object(lifecycle,'exclusive',side_effect=changed):
                with self.assertRaises(RuntimeError):lifecycle.publish_original(raw,public,IDENTITY,include_note=True)


if __name__=='__main__':unittest.main()
