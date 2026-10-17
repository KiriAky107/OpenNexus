import json
from contextlib import closing
import os
from pathlib import Path
import tempfile
import subprocess
import sqlite3
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from android_runtime_probe import APP, DRIVER, IMAGE_BODY, RICH_IMAGE_TEXT, IMAGE_BYTES, Probe, digest, require_hosted_emulator, stop_owned_emulator, validate_binding, validate_build_job, validate_tablet_layout, validate_image_save_observation, validate_image_save_result


class RuntimeBoundaries(unittest.TestCase):
    def image_save_fixture(self):
        asset=f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
        content=RICH_IMAGE_TEXT+'![]('+asset+')\n'
        report={'drafts':[{'id':'original-draft','path':'runtime-rich-image.md','content':content,'generation':3,'saved_generation':0}],
            'saves':[], 'images':[{'operation_id':'original-image','draft_id':'original-draft','generation':2,
                'asset_path':asset,'hash':digest(IMAGE_BYTES),'size':len(IMAGE_BYTES),'state':'committed','content_after':content}]}
        rendered={'available':True,'styles':{'imageSave':{'richImages':[{'complete':True,'width':2,'height':2}],
            'save':[{'enabled':True,'visible':True,'hit':True}],'busy':False}}}
        return report,rendered

    def test_original_image_save_readiness_refuses_unknown_save_or_unacknowledged_import(self):
        report,rendered=self.image_save_fixture()
        validate_image_save_observation(report,rendered,'rich-image')
        for changed in ({'saves':[{'state':'prepared'}]}, {'images':[]}, {'images':report['images']*2},
                {'drafts':[dict(report['drafts'][0],generation=4)]},
                {'images':[dict(report['images'][0],state='prepared')]},
                {'images':[dict(report['images'][0],content_after='unrelated content')]}):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                validate_image_save_observation(dict(report,**changed),rendered,'rich-image')

    def test_original_image_save_readiness_requires_loaded_2px_image_and_uncovered_action(self):
        report,rendered=self.image_save_fixture();original=rendered['styles']['imageSave']
        variants=[{'busy':True},{'richImages':[]},{'richImages':[{'complete':False,'width':2,'height':2}]},
            {'richImages':[{'complete':True,'width':0,'height':0}]},{'richImages':[{'complete':True,'width':2,'height':2,'error':True}]},
            {'save':[]},{'save':original['save']*2}]
        variants += [{'save':[dict(original['save'][0],**{key:False})]} for key in ('enabled','visible','hit')]
        for changed in variants:
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                validate_image_save_observation(report,{'available':True,'styles':{'imageSave':dict(original,**changed)}},'rich-image')
        with self.assertRaises(RuntimeError):validate_image_save_observation(report,{'available':False},'rich-image')

    def test_saved_ui_requires_one_same_generation_committed_receipt_and_original_image(self):
        before,_=self.image_save_fixture()
        after=dict(before,drafts=[dict(before['drafts'][0],saved_generation=3)],
            saves=[{'operation_id':'original-save','draft_id':'original-draft','generation':3,'state':'committed','error':None}])
        validate_image_save_result(before,after)
        for changed in ({'saves':[]},{'saves':after['saves']*2},{'images':[]},
                {'saves':[dict(after['saves'][0],state='prepared')]},
                {'drafts':[dict(after['drafts'][0],content='later text')]},
                {'drafts':[dict(after['drafts'][0],saved_generation=0)]}):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                validate_image_save_result(before,dict(after,**changed))

    def test_image_save_snapshot_filters_exact_owned_path_and_never_stops_or_writes_app(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.root='./owned/';probe.args=SimpleNamespace(serial='emulator-5562')
            probe.wait_owned_connection=Mock();probe.force_stop=Mock(side_effect=AssertionError('Read-only observation must not stop the live driver'))
            calls=[]
            def rows(query,parameters):
                calls.append((query,parameters));return []
            probe.database_rows=rows
            with patch('android_runtime_probe.require_hosted_emulator'):
                report=probe.image_save_rows('rich-image','before-save')
                self.assertEqual(len(calls),3)
                self.assertTrue(all(parameters==('runtime-rich-image.md',) and 'WHERE path=? ORDER BY rowid LIMIT 3' in query for query,parameters in calls))
                self.assertEqual(report['saves'],[])
                with self.assertRaises(RuntimeError):probe.image_save_rows('user-file','before-save')
            self.assertEqual(len(calls),3)

    def test_image_save_handshake_releases_one_save_only_after_same_receipt_and_rendered_readiness(self):
        for changed in (False,True):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as directory:
                probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562');probe.adb=Path('/owned/adb')
                report,rendered=self.image_save_fixture()
                probe.wait_owned_connection=Mock();probe.capture=Mock();probe.capture_failed_editor_state=Mock()
                probe.image_save_rows=Mock(side_effect=[report,dict(report,path='changed') if changed else report])
                probe.read_native_styles=Mock(return_value=rendered)
                probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'ready\n'))
                probe.device=Mock();process=Mock();process.poll.return_value=None
                process.communicate.return_value=(b'OPENNEXUS_RUNTIME_PHASE_OK rich-image\n',b'');process.returncode=0
                with patch('android_runtime_probe.require_hosted_emulator'),patch('android_runtime_probe.subprocess.Popen',return_value=process):
                    if changed:
                        with self.assertRaisesRegex(RuntimeError,'changed during observation'):probe.image_save_instrumentation('rich-image')
                        probe.device.assert_not_called();process.terminate.assert_called_once();probe.capture_failed_editor_state.assert_called_once_with('rich-image')
                    else:
                        result=probe.image_save_instrumentation('rich-image')
                        self.assertEqual(result.returncode,0);probe.device.assert_called_once()
                        self.assertEqual(probe.device.call_args.kwargs['input'],b'proceed\n')
                        self.assertIn('test ! -e no_backup/owned-rich-image-save.proceed',probe.device.call_args.args[2])
                        process.terminate.assert_not_called();probe.capture_failed_editor_state.assert_not_called()
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(serial='foreign-phone')
        with patch('android_runtime_probe.require_hosted_emulator',side_effect=RuntimeError('Not owned')),patch('android_runtime_probe.subprocess.Popen') as launch:
            with self.assertRaises(RuntimeError):probe.image_save_instrumentation('rich-image')
            launch.assert_not_called()

    def test_attachment_scope_does_not_repeat_the_fixed_apks_keystore_cases(self):
        for scope in ('attachments','attachments-remaining'):
            probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope=scope)
            probe.binding={'native_tests':{'expected_tests':5}};probe.device=Mock();probe.shell=Mock()
            probe.native_keystore_tests()
            probe.device.assert_not_called();probe.shell.assert_not_called()

    def test_remaining_image_scope_requires_only_its_two_original_targets_and_exact_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory)
            asset=f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
            reference=('![]('+asset+')').encode()
            originals={asset:IMAGE_BYTES,'runtime-rich-image.md':(RICH_IMAGE_TEXT+'\n').encode()+reference,
                'runtime-image.canvas':json.dumps({'nodes':[{'type':'file','file':asset}]}).encode()}
            rows=[(str(i),path,asset,digest(IMAGE_BYTES),len(IMAGE_BYTES),'committed')
                for i,path in enumerate(('runtime-rich-image.md','runtime-image.canvas'))]
            probe.original=originals.__getitem__;probe.database_rows=lambda _:rows
            self.assertEqual(probe.verify_original_images(include_markdown=False),originals)
            probe.database_rows=lambda _:rows+rows[:1]
            with self.assertRaises(RuntimeError):probe.verify_original_images(include_markdown=False)

    def test_auth_read_acceptance_requires_the_original_empty_index_and_cannot_claim_https(self):
        for rows in (0,1):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);database=root/'original.sqlite3'
                with closing(sqlite3.connect(database)) as db:
                    for name in ('auth_scopes','auth_intents','auth_profile_choices','auth_selection'):
                        db.execute('CREATE TABLE '+name+'(id TEXT)')
                    if rows:db.execute("INSERT INTO auth_intents VALUES ('owned-original')")
                    db.commit()
                original=database.read_bytes();probe=Probe.__new__(Probe);probe.tools=root/'tools';probe.tools.mkdir()
                probe.output=root/'output';probe.output.mkdir();probe.root='files/owned/'
                probe.args=SimpleNamespace(commit='fixed',tools_commit='tools');probe.binding={'sha256':'a'*64}
                probe.private=Mock(side_effect=lambda path,check=False:subprocess.CompletedProcess([],0,original,b'')
                    if path.endswith('mobile-sync-auth.sqlite3') else subprocess.CompletedProcess([],1,b'',b''))
                if rows:
                    with self.assertRaisesRegex(Exception,'created an authentication operation'):probe.check_empty_native_auth_index()
                    self.assertFalse((probe.output/'native-auth-read-proof.json').exists())
                else:
                    probe.check_empty_native_auth_index();proof=json.loads((probe.output/'native-auth-read-proof.json').read_text())
                    self.assertTrue(proof['actual_native_current_and_profile_UI_read'])
                    self.assertFalse(proof['login_or_HTTPS_verified']);self.assertFalse(proof['selected_credential_decryption_verified'])
                    self.assertEqual(sum(proof['original_metadata_counts'].values()),0)
                self.assertEqual(database.read_bytes(),original)

    def test_ime_wait_observes_exact_registration_before_any_mutating_command(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562')
            expected=DRIVER+'/.CompositionIme'
            probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b''),
                subprocess.CompletedProcess([],0,(expected+'Other\n').encode()),subprocess.CompletedProcess([],0,(expected+'\n').encode())])
            with patch('android_runtime_probe.require_hosted_emulator'),patch('android_runtime_probe.time.sleep'):
                probe.wait_test_ime()
            self.assertEqual(probe.shell.call_count,3)
            for call in probe.shell.call_args_list:self.assertEqual(call.args,('ime','list','-a','-s'))
            proof=json.loads((probe.output/'ime-registration.json').read_text())
            self.assertEqual([x['registered'] for x in proof['observations']],[False,False,True])

    def test_ime_registration_timeout_is_bounded_and_cannot_enable_an_unknown_service(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'other.package/.IME\n'))
            with patch('android_runtime_probe.require_hosted_emulator'),patch('android_runtime_probe.time.monotonic',side_effect=[0,0,16]),patch('android_runtime_probe.time.sleep'):
                with self.assertRaisesRegex(RuntimeError,'no enable/set attempted'):probe.wait_test_ime()
            self.assertEqual(probe.shell.call_count,2)
            for call in probe.shell.call_args_list:self.assertEqual(call.args,('ime','list','-a','-s'))

    def test_unavailable_webview_diagnostic_does_not_skip_original_failed_phase_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.root=None;probe.phases=[];probe.activity=APP+'/.MainActivity'
            probe.force_stop=Mock();probe.native_tls_startup=Mock();probe.capture=Mock();probe.capture_failed_editor_state=Mock()
            probe.read_native_styles=Mock(side_effect=RuntimeError('diagnostic unavailable'))
            probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b''),subprocess.CompletedProcess([],0,b'OPENNEXUS_RUNTIME_FAILED\n')])
            with self.assertRaisesRegex(Exception,'Actual Android UI phase failed'):probe.phase('owned-failure')
            probe.capture_failed_editor_state.assert_called_once_with('owned-failure')
            self.assertEqual(json.loads((probe.output/'owned-failure-failed-diagnostic-error.json').read_text())['error'],'RuntimeError')

    def test_first_phase_failure_can_locate_only_the_owned_relative_private_database(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.root=None;probe.args=SimpleNamespace(serial='emulator-5562')
            probe.force_stop=Mock();probe.wait_owned_connection=Mock();probe.original=Mock(return_value=b'owned synthetic original');probe.draft_rows=Mock(return_value=[])
            probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'./files/mobile-drafts.sqlite3\n'))
            with patch('android_runtime_probe.require_hosted_emulator'):
                probe.capture_failed_editor_state('first-phase')
            self.assertEqual(probe.root,'./files/')
            report=json.loads((probe.output/'first-phase-failed-native-state.json').read_text())
            self.assertEqual(report['draft_rows'],[]);self.assertNotIn('observation_error',report)

    def test_private_database_discovery_refuses_ambiguous_absolute_or_parent_paths(self):
        for output in (b'/files/mobile-drafts.sqlite3\n',b'./../mobile-drafts.sqlite3\n',
                b'./files/mobile-drafts.sqlite3\n./other/mobile-drafts.sqlite3\n',b'./files\\mobile-drafts.sqlite3\n'):
            probe=Probe.__new__(Probe);probe.root=None;probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,output))
            with self.assertRaisesRegex(Exception,'origin is ambiguous'):probe.discover_private_root()
            self.assertIsNone(probe.root)
    def test_tablet_geometry_requires_actual_viewport_columns_and_bounded_floating_panel(self):
        layout={'width':800,'compact':True,'rootWidth':800,'rootScrollWidth':800,
            'toolbar':{'visible':True,'left':148,'right':788,'width':640,'clientWidth':638,'scrollWidth':638},
            'editor':{'visible':True},'main':{'visible':True},'files':{'visible':False},'floating':{'position':'absolute'}}
        report={'available':True,'styles':{'layout':layout}}
        self.assertEqual(validate_tablet_layout(report,True),layout)
        for invalid in [dict(layout,width=390),dict(layout,compact=False),dict(layout,rootScrollWidth=801.5),
                dict(layout,files={'visible':True}),dict(layout,toolbar={**layout['toolbar'],'right':810}),
                dict(layout,toolbar={**layout['toolbar'],'scrollWidth':690})]:
            with self.assertRaises(RuntimeError):validate_tablet_layout({'available':True,'styles':{'layout':invalid}},True)
        with self.assertRaisesRegex(Exception,'not observed'):validate_tablet_layout({'available':False},True)
        target={'visible':True,'width':44,'height':44}
        targets={'rows':[target], 'header':[target,target], 'search':[target,target]}
        landscape={**layout,'width':1280,'rootWidth':1280,'rootScrollWidth':1280,'compact':False,'files':{'visible':True},'fileTargets':targets}
        validate_tablet_layout({'available':True,'styles':{'layout':landscape}},False)
        for group in ('rows','header','search'):
            for bad in ([],[dict(target,height=28)],[dict(target,width=43.99)],[dict(target,visible=False)]):
                with self.subTest(group=group,bad=bad), self.assertRaises(RuntimeError):
                    validate_tablet_layout({'available':True,'styles':{'layout':dict(landscape,fileTargets=dict(targets,**{group:bad}))}},False)
        with self.assertRaisesRegex(RuntimeError,'not observed'):
            validate_tablet_layout({'available':True,'styles':{'layout':dict(landscape,fileTargets={})}},False)

    def test_owned_tablet_display_is_verified_before_mutation_and_restored_after_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.wait_owned_connection=Mock()
            probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'Physical size: 1080x2400\n'),
                subprocess.CompletedProcess([],0,b'Physical density: 420\n'),RuntimeError('size mutation outcome unknown')])
            with patch('android_runtime_probe.require_hosted_emulator'):
                with self.assertRaisesRegex(Exception,'outcome unknown'):probe.tablet_display(1600,2400)
                self.assertTrue(probe.tablet_display_owned)
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b''),subprocess.CompletedProcess([],0,b''),
                    subprocess.CompletedProcess([],0,b'Physical size: 1080x2400\n'),subprocess.CompletedProcess([],0,b'Physical density: 420\n')])
                probe.restore_tablet_display()
                self.assertEqual(probe.shell.call_args_list[0].args,('wm','size','reset'))
                self.assertEqual(probe.shell.call_args_list[1].args,('wm','density','reset'))
                self.assertFalse(probe.tablet_display_owned)
                probe.shell.reset_mock();probe.restore_tablet_display();probe.shell.assert_not_called()

    def test_tablet_display_refuses_existing_overrides_or_foreign_host_before_writes(self):
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(serial='emulator-5562');probe.wait_owned_connection=Mock()
        with patch('android_runtime_probe.require_hosted_emulator',side_effect=RuntimeError('foreign host')):
            probe.shell=Mock()
            with self.assertRaisesRegex(Exception,'foreign host'):probe.tablet_display(1600,2400)
            probe.shell.assert_not_called()
        with patch('android_runtime_probe.require_hosted_emulator'):
            probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'Physical size: 1080x2400\nOverride size: 600x800\n'),
                subprocess.CompletedProcess([],0,b'Physical density: 420\n')])
            with self.assertRaisesRegex(Exception,'pre-existing display overrides'):probe.tablet_display(1600,2400)
            self.assertEqual(probe.shell.call_count,2);self.assertFalse(getattr(probe,'tablet_display_owned',False))

    def test_real_keystore_runner_keeps_output_and_never_replaces_existing_test_data(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562',bundle=Path('/owned/bundle'))
            probe.binding={'native_tests':{'test_class':'cc.kronecker.opennexus.sync.SyncCredentialStoreTest','runner':'androidx.test.runner.AndroidJUnitRunner','sha256':'1'*64,'expected_tests':4}}
            probe.device=Mock();probe.force_stop=Mock()
            with patch('android_runtime_probe.require_hosted_emulator'):
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,b'OK (4 tests)\n',b'')])
                probe.native_keystore_tests()
                probe.device.assert_called_once_with('install','-t','--no-streaming',Path('/owned/bundle/OpenNexus-android-tests.apk'))
                self.assertTrue(probe.keystore_test_result['passed']);self.assertFalse(probe.keystore_test_result['hardware_backing_claimed'])
                probe.binding['native_tests']['expected_tests']=5
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,b'OK (4 tests)\n',b'')])
                with self.assertRaisesRegex(Exception,'did not all pass'):probe.native_keystore_tests()
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,b'OK (5 tests)\n',b'')])
                probe.native_keystore_tests();self.assertTrue(probe.keystore_test_result['Rust_worker_verified'])
                probe.device.reset_mock()
                probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,('package:'+APP+'.test\n').encode(),b''))
                with self.assertRaisesRegex(Exception,'refusing to replace'):probe.native_keystore_tests()
                probe.device.assert_not_called()
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,b'FAILURES!!!\n',b'original stderr')])
                with self.assertRaisesRegex(Exception,'did not all pass'):probe.native_keystore_tests()
                self.assertEqual((probe.output/'keystore-device-tests.txt').read_bytes(),b'FAILURES!!!\n')
                self.assertEqual((probe.output/'keystore-device-tests.stderr.txt').read_bytes(),b'original stderr')

    def test_native_tls_startup_observes_only_the_owned_process_and_requires_ready_without_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.binding={'native_tls':{'coordinate':'org.rustls:rustls-platform-verifier:0.2.0'}}
            probe.args=SimpleNamespace(serial='emulator-5562');probe.tls_initializations=[]
            with patch('android_runtime_probe.require_hosted_emulator'):
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'1234\n'),subprocess.CompletedProcess([],0,b'I/OpenNexusSync( 1234): SYNC_TLS_READY\n')])
                probe.native_tls_startup('markdown')
                self.assertEqual(probe.shell.call_args.args,('logcat','--pid=1234','-d','-v','brief','OpenNexusSync:I','*:S'))
                self.assertEqual(probe.tls_initializations,[{'phase':'markdown','owned_pid':1234,'native_context_initialized':True,'network_tested':False}])
                probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b'1234\n'),subprocess.CompletedProcess([],0,b'W/OpenNexusSync( 1234): SYNC_TLS_UNAVAILABLE\n')])
                with self.assertRaisesRegex(Exception,'initialization failed'):probe.native_tls_startup('failed')
                probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'1234 4321\n'))
                with self.assertRaisesRegex(Exception,'exact owned app process'):probe.native_tls_startup('wrong-process')
                self.assertEqual(probe.shell.call_count,1)
            probe.binding={};probe.shell=Mock(side_effect=AssertionError('No observation against an old APK'))
            probe.native_tls_startup('legacy')

    def test_native_startup_hook_does_not_skip_the_original_phase_receipt_stop_or_database_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.root=None;probe.phases=[]
            probe.activity=APP+'/.MainActivity';probe.force_stop=Mock();probe.capture=Mock();probe.native_tls_startup=Mock()
            probe.shell=Mock(side_effect=[subprocess.CompletedProcess([],0,b''),subprocess.CompletedProcess([],0,b'OPENNEXUS_RUNTIME_PHASE_OK markdown\n'),subprocess.CompletedProcess([],0,b'./owned/mobile-drafts.sqlite3\n')])
            probe.phase('markdown')
            self.assertEqual(probe.root,'./owned/');self.assertEqual(probe.phases[0]['phase'],'markdown')
            self.assertTrue(probe.phases[0]['passed']);self.assertEqual(probe.force_stop.call_count,2)
            probe.native_tls_startup.assert_called_once_with('markdown');probe.capture.assert_called_once_with('markdown')

    def test_private_read_keeps_binary_bytes_and_remote_errors_distinct(self):
        probe = Probe.__new__(Probe); probe.adb = Path('/owned/adb'); probe.args = SimpleNamespace(serial='emulator-5562')
        raw = b'\x00original\r\n\r\n\xff'
        replies = [subprocess.CompletedProcess([], 0, raw, b''),
            subprocess.CompletedProcess([], 1, b'', b'No such file'),
            subprocess.CompletedProcess([], 1, b'', b'No such file')]
        with patch('android_runtime_probe.subprocess.run', side_effect=replies) as run:
            self.assertEqual(probe.private('./owned/vault/original.png').stdout, raw)
            self.assertEqual(probe.private('./owned/missing-wal', check=False).returncode, 1)
            with self.assertRaisesRegex(RuntimeError, 'Command failed'):
                probe.private('./owned/missing')
        self.assertEqual(run.call_args_list[0].args[0],
            [str(Path('/owned/adb')), '-s', 'emulator-5562', 'shell', '-T',
                'run-as ' + APP + ' cat ./owned/vault/original.png'])

    def test_failed_editor_capture_keeps_empty_original_and_dirty_draft_without_replaying_input(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Probe.__new__(Probe); probe.output = Path(directory); probe.root = './owned/'
            probe.args = SimpleNamespace(serial='emulator-5562'); calls = []
            probe.wait_owned_connection = lambda: calls.append('revalidate owned AVD')
            probe.force_stop = lambda: calls.append('stop own package')
            def private(path):
                if path == './owned/vault/runtime-rich.md': return subprocess.CompletedProcess([], 0, b'', b'')
                raise RuntimeError('Synthetic target does not exist')
            probe.private = private
            probe.draft_rows = lambda: [('runtime-rich.md', 'original\r\n\n草稿 😀\n', 2, 1)]
            with patch('android_runtime_probe.require_hosted_emulator'):
                probe.capture_failed_editor_state('rich-markdown')
            report = json.loads((probe.output/'rich-markdown-failed-native-state.json').read_text('utf-8'))
            self.assertEqual(calls, ['revalidate owned AVD', 'stop own package'])
            self.assertEqual((probe.output/'original-observations/001/runtime-rich.md').read_bytes(), b'')
            self.assertEqual(report['originals']['runtime-rich.md'], {'size': 0, 'sha256': digest(b'')})
            self.assertEqual(report['draft_rows'][0][1], 'original\r\n\n草稿 😀\n')

    def test_failed_capture_rejects_foreign_origin_before_reading_or_stopping_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Probe.__new__(Probe); probe.output = Path(directory)
            probe.args = SimpleNamespace(serial='unowned-phone')
            probe.wait_owned_connection = lambda: self.fail('Foreign target must not be observed')
            probe.force_stop = lambda: self.fail('Foreign target must not be stopped')
            with patch('android_runtime_probe.require_hosted_emulator', side_effect=RuntimeError('Not owned')):
                probe.capture_failed_editor_state('owned-phase')
            report = json.loads((probe.output/'owned-phase-failed-native-state.json').read_text('utf-8'))
            self.assertEqual(report['observation_error'], 'RuntimeError')
            self.assertEqual(report['originals'], {})

    def test_style_observation_refuses_ambiguous_app_process_before_forwarding(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.wait_owned_connection=lambda:None
            probe.shell=lambda *args:subprocess.CompletedProcess([],0,b'1234 5678\n',b'')
            probe.device=lambda *args,**kwargs:self.fail('Ambiguous app must not be forwarded')
            with patch('android_runtime_probe.require_hosted_emulator'):probe.read_native_styles('owned')
            report=json.loads((probe.output/'owned-computed-styles.json').read_text())
            self.assertFalse(report['available']);self.assertNotIn('styles',report)

    def test_style_observation_rejects_foreign_websocket_and_removes_only_own_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=Probe.__new__(Probe);probe.output=Path(directory);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.wait_owned_connection=lambda:None
            probe.shell=lambda *args:subprocess.CompletedProcess([],0,b'1234\n',b'')
            calls=[]
            def device(*args,**kwargs):
                calls.append(args)
                return subprocess.CompletedProcess([],0,b'45678\n' if args[1]=='tcp:0' else b'',b'')
            probe.device=device;probe.command=lambda *args,**kwargs:self.fail('Foreign endpoint must never reach Node')
            pages=[{'type':'page','title':'OpenNexus','webSocketDebuggerUrl':'ws://unrelated.example:45678/devtools/page/owned'}]
            response=SimpleNamespace(status=200,read=lambda limit:json.dumps(pages).encode())
            connection=SimpleNamespace(request=lambda *args:None,getresponse=lambda:response,close=lambda:None)
            with patch('android_runtime_probe.require_hosted_emulator'),patch('android_runtime_probe.http.client.HTTPConnection',return_value=connection):
                probe.read_native_styles('owned')
            report=json.loads((probe.output/'owned-computed-styles.json').read_text())
            self.assertFalse(report['available']);self.assertTrue(report['own_forward_removed'])
            self.assertEqual(calls,[('forward','tcp:0','localabstract:webview_devtools_remote_1234'),('forward','--remove','tcp:45678')])

    def test_original_observations_retain_each_exact_read_before_any_later_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Probe.__new__(Probe); probe.output = Path(directory); probe.root = './owned/'
            data = iter([b'first\r\n\r\noriginal\n', b'later\n'])
            probe.private = lambda path: subprocess.CompletedProcess([], 0, next(data), b'')
            self.assertEqual(probe.original('runtime-note.md'), b'first\r\n\r\noriginal\n')
            self.assertEqual(probe.original('runtime-note.md'), b'later\n')
            self.assertEqual((probe.output/'original-observations/001/runtime-note.md').read_bytes(), b'first\r\n\r\noriginal\n')
            self.assertEqual((probe.output/'original-observations/002/runtime-note.md').read_bytes(), b'later\n')
            for name in ('../foreign', '/foreign', 'a//b', 'C:/foreign', 'a\\b'):
                with self.assertRaisesRegex(RuntimeError, 'Unsafe original'): probe.original(name)

    def test_original_picker_fixture_is_a_complete_decodable_rgba_png(self):
        self.assertTrue(IMAGE_BYTES.startswith(b'\x89PNG\r\n\x1a\n'))
        position = 8; image_data = b''; kinds = []
        while position < len(IMAGE_BYTES):
            size = struct.unpack('>I', IMAGE_BYTES[position:position + 4])[0]
            kind = IMAGE_BYTES[position + 4:position + 8]
            data = IMAGE_BYTES[position + 8:position + 8 + size]
            crc = struct.unpack('>I', IMAGE_BYTES[position + 8 + size:position + 12 + size])[0]
            self.assertEqual(zlib.crc32(kind + data) & 0xffffffff, crc)
            if kind == b'IHDR': self.assertEqual(struct.unpack('>IIBBBBB', data), (2, 2, 8, 6, 0, 0, 0))
            if kind == b'IDAT': image_data += data
            kinds.append(kind); position += size + 12
        self.assertEqual(kinds, [b'IHDR', b'IDAT', b'IEND'])
        self.assertEqual(position, len(IMAGE_BYTES))
        self.assertEqual(zlib.decompress(image_data), bytes([0, 226, 64, 98, 255, 34, 167, 134, 255, 0, 55, 103, 211, 255, 247, 198, 72, 255]))

    def test_picker_setup_refuses_an_existing_destination_before_pushing_any_bytes(self):
        probe = Probe.__new__(Probe); probe.args = SimpleNamespace(serial='emulator-5562')
        probe.shell = lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b'', b'')
        probe.device = lambda *args, **kwargs: self.fail('An existing picker file must not be replaced')
        with patch('android_runtime_probe.require_hosted_emulator'):
            with self.assertRaisesRegex(RuntimeError, 'refusing to replace'): probe.stage_original_image()

    def test_image_acceptance_rejects_reencoded_bytes_duplicate_receipts_or_changed_note(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Probe.__new__(Probe); probe.output = Path(directory)
            asset = f'attachments/{digest(IMAGE_BYTES)[:2]}/{digest(IMAGE_BYTES)}.png'
            reference = ('![](' + asset + ')').encode()
            originals = {asset: IMAGE_BYTES, 'runtime-image.md': IMAGE_BODY.encode() + reference, 'runtime-rich-image.md': (RICH_IMAGE_TEXT+'\n').encode()+reference,
                'runtime-image.canvas': json.dumps({'nodes': [{'type': 'file', 'file': asset}]}).encode()}
            rows = [(str(i), path, asset, digest(IMAGE_BYTES), len(IMAGE_BYTES), 'committed')
                for i, path in enumerate(('runtime-image.md', 'runtime-rich-image.md', 'runtime-image.canvas'))]
            probe.original = originals.__getitem__; probe.database_rows = lambda _: rows
            self.assertEqual(probe.verify_original_images(), {key: originals[key] for key in ('runtime-image.md', 'runtime-rich-image.md', 'runtime-image.canvas', asset)})
            cases = [({asset: b'reencoded PNG'}, rows), ({}, rows + rows[:1]),
                ({'runtime-image.md': b'changed note' + reference}, rows)]
            for changed, observed in cases:
                probe.original = {**originals, **changed}.__getitem__; probe.database_rows = lambda _: observed
                with self.assertRaises(RuntimeError): probe.verify_original_images()

    def installed_probe(self, directory, copied, hashes=None, avd='opennexus-ci-73-1', states=None):
        root=Path(directory);probe=Probe.__new__(Probe)
        probe.output=root/'evidence';probe.output.mkdir();probe.tools=root/'private-read-copies';probe.tools.mkdir()
        probe.apk=root/'original.apk';probe.apk.write_bytes(b'original immutable APK bytes')
        probe.binding={'sha256':digest(probe.apk.read_bytes())};probe.args=SimpleNamespace(serial='emulator-5562')
        installed='/data/app/owned/base.apk';calls=[];copies=iter(copied);hashes=iter(hashes) if hashes else None
        states=iter(states) if states else None
        def device(*args,**kwargs):
            calls.append(args)
            if args==('get-state',):return subprocess.CompletedProcess([],0,next(states) if states else b'device\n',b'')
            if args==('emu','avd','name'):return subprocess.CompletedProcess([],0,(avd+'\nOK\n').encode(),b'')
            if args[0]=='pull':
                Path(args[2]).write_bytes(next(copies));return subprocess.CompletedProcess([],0,b'one file pulled',b'')
            raise AssertionError('Unexpected device action: '+str(args))
        def shell(*args,**kwargs):
            calls.append(('shell',*args))
            if args==('pm','path',APP):return subprocess.CompletedProcess([],0,('package:'+installed+'\n').encode(),b'')
            if args==('sha256sum',installed):
                value=next(hashes) if hashes else probe.binding['sha256']
                return subprocess.CompletedProcess([],0 if value else 1,((value+'  '+installed+'\n').encode() if value else b''),b'')
            raise AssertionError('Unexpected shell write: '+str(args))
        probe.device=device;probe.shell=shell
        return probe,installed,calls

    def test_installed_byte_reads_preserve_a_partial_original_and_confirm_only_a_full_copy(self):
        original=b'original immutable APK bytes'
        with tempfile.TemporaryDirectory() as directory:
            probe,path,calls=self.installed_probe(directory,[b'partial',original])
            with patch('android_runtime_probe.require_hosted_emulator'),patch.dict(os.environ,{'GITHUB_RUN_ID':'73','GITHUB_RUN_ATTEMPT':'1'}):
                probe.verify_installed(path)
            proof=json.loads((probe.output/'installed-apk.json').read_text('utf-8'))
            self.assertEqual([entry['verified'] for entry in proof['attempts']],[False,True])
            self.assertEqual((probe.tools/'installed-observation-1.apk').read_bytes(),b'partial')
            self.assertEqual((probe.tools/'installed-observation-2.apk').read_bytes(),original)
            self.assertEqual(probe.apk.read_bytes(),original)
            self.assertEqual(sum(call[0]=='pull' for call in calls),2)

    def test_device_hash_disagreement_rejects_before_copy_or_reinstall(self):
        with tempfile.TemporaryDirectory() as directory:
            probe,path,calls=self.installed_probe(directory,[],hashes=[digest(b'changed installed APK')])
            with patch('android_runtime_probe.require_hosted_emulator'),patch.dict(os.environ,{'GITHUB_RUN_ID':'73','GITHUB_RUN_ATTEMPT':'1'}):
                with self.assertRaisesRegex(RuntimeError,'device APK hash differs'):probe.verify_installed(path)
            self.assertFalse(any(call[0] in ('pull','install') for call in calls))

    def test_offline_read_reobserves_the_same_avd_without_restarting_or_reinstalling(self):
        original=b'original immutable APK bytes';sha=digest(original)
        with tempfile.TemporaryDirectory() as directory:
            probe,path,calls=self.installed_probe(directory,[original],hashes=[None,sha,sha],states=[b'device\n',b'offline\n',b'device\n'])
            with patch('android_runtime_probe.require_hosted_emulator'),patch.dict(os.environ,{'GITHUB_RUN_ID':'73','GITHUB_RUN_ATTEMPT':'1'}),patch('android_runtime_probe.time.sleep'):
                probe.verify_installed(path)
            proof=json.loads((probe.output/'installed-apk.json').read_text('utf-8'))
            self.assertIsNone(proof['attempts'][0]['device_sha256_before']);self.assertTrue(proof['attempts'][1]['verified'])
            self.assertFalse(any(call[0] in ('install','reboot','kill-server','start-server') for call in calls))

    def test_read_reconnection_rejects_a_different_avd_before_accessing_the_package(self):
        with tempfile.TemporaryDirectory() as directory:
            probe,path,calls=self.installed_probe(directory,[],avd='unrelated-avd')
            with patch('android_runtime_probe.require_hosted_emulator'),patch.dict(os.environ,{'GITHUB_RUN_ID':'73','GITHUB_RUN_ATTEMPT':'1'}):
                with self.assertRaisesRegex(RuntimeError,'ownership mismatch'):probe.verify_installed(path)
            self.assertEqual([call[0] for call in calls],['get-state','emu'])

    def test_theme_diagnostic_capture_keeps_the_actual_selected_card_screenshot(self):
        png = b'\x89PNG\r\n\x1a\nowned screenshot'
        for phase, choice in [('theme-diagnostic-dark', 'Dark'), ('theme-diagnostic-reset', 'System')]:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                probe = Probe.__new__(Probe); probe.output = Path(directory); calls = []
                def device(*args, **kwargs):
                    calls.append(args)
                    data = b'<?xml version="1.0"?><hierarchy />' if args[0:2] == ('exec-out', 'cat') else png
                    return subprocess.CompletedProcess([], 0, data, b'')
                probe.device = device
                probe.shell = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b'owned window', b'')
                probe.capture(phase)
                self.assertEqual((probe.output / f'{phase}.png').read_bytes(), png)
                self.assertEqual((probe.output / f'theme-cards-{choice}.png').read_bytes(), png)
                self.assertIn(('exec-out', 'run-as', DRIVER, 'cat', f'files/theme-cards-{choice}.png'), calls)
                self.assertEqual((probe.output / f'{phase}-window.txt').read_bytes(), b'owned window')

    def test_test_ime_rejects_unknown_original_selection_before_any_setting_change(self):
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(serial='emulator-5562')
        probe.original_ime=None;probe.original_hardware_ime=None
        for original in (b'null\n', b'not a component\n', (DRIVER+'/.CompositionIme\n').encode()):
            calls=[]
            probe.shell=lambda *args,**kwargs:(calls.append(args) or subprocess.CompletedProcess([],0,original,b''))
            with patch('android_runtime_probe.require_hosted_emulator'):
                with self.assertRaises(RuntimeError):probe.enable_test_ime()
            self.assertEqual(len(calls),1);self.assertIsNone(probe.original_ime)

    def test_test_ime_restoration_changes_only_the_exact_owned_selection(self):
        for current in (DRIVER+'/.CompositionIme','another.ime/.Editor'):
            probe=Probe.__new__(Probe);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.original_ime='original.ime/.Keyboard';probe.original_hardware_ime='null'
            calls=[]
            probe.shell=lambda *args,**kwargs:(calls.append(args) or subprocess.CompletedProcess([],0,(current+'\n').encode(),b''))
            with patch('android_runtime_probe.require_hosted_emulator'):probe.restore_test_ime()
            expected=[('settings','get','secure','default_input_method')]
            if current==DRIVER+'/.CompositionIme':expected.extend([
                ('ime','set','original.ime/.Keyboard'),('ime','disable',DRIVER+'/.CompositionIme'),
                ('settings','delete','secure','show_ime_with_hard_keyboard')])
            self.assertEqual(calls,expected);self.assertIsNone(probe.original_ime)

    def test_runtime_reuse_requires_the_exact_successful_build_job_not_an_aggregate_green_run(self):
        run={'id':42,'head_sha':'fixed-source','head_branch':'main','path':'.github/workflows/android.yml','conclusion':'failure'}
        job={'id':99,'name':'debug-apk','status':'completed','conclusion':'success'}
        # A subsequent runner failure does not invalidate an already verified APK.
        self.assertEqual(validate_build_job(run,[job],'fixed-source')['job_id'],99)
        with self.assertRaisesRegex(RuntimeError,'source mismatch'):validate_build_job(run,[job],'other-source')
        with self.assertRaisesRegex(RuntimeError,'no successful terminal'):validate_build_job(run,[{**job,'conclusion':'failure'}],'fixed-source')
        with self.assertRaisesRegex(RuntimeError,'no successful terminal'):validate_build_job(run,[job,job],'fixed-source')

    def test_database_observations_remain_independent_when_a_later_snapshot_has_no_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE drafts(path TEXT,content TEXT,generation INTEGER,saved_generation INTEGER)')
                db.execute("INSERT INTO drafts VALUES('owned.md','first original',2,1)")
                db.commit()
            probe=Probe.__new__(Probe);probe.tools=root/'tools';probe.tools.mkdir();probe.root='./'
            def original(path, **_):
                return subprocess.CompletedProcess([],0,source.read_bytes(),b'') if path.endswith('mobile-drafts.sqlite3') else subprocess.CompletedProcess([],1,b'',b'No such file')
            probe.private=original
            self.assertEqual(probe.draft_rows(),[('owned.md','first original',2,1)])
            before={path:path.read_bytes() for path in probe.tools.rglob('mobile-drafts.sqlite3')}
            self.assertEqual(len(before),1)
            with closing(sqlite3.connect(source)) as db:
                db.execute("UPDATE drafts SET content='later confirmed',generation=3,saved_generation=3");db.commit()
            self.assertEqual(probe.draft_rows(),[('owned.md','later confirmed',3,3)])
            for path,data in before.items():self.assertEqual(path.read_bytes(),data,'A new observation replaced earlier evidence')

    def test_cleanup_does_not_fail_or_kill_when_no_emulator_ever_started(self):
        absent = subprocess.CompletedProcess([], 0, b'List of devices attached\n\n', b'')
        with patch('android_runtime_probe.require_hosted_emulator'), patch('android_runtime_probe.subprocess.run', return_value=absent) as run:
            result = stop_owned_emulator(Path('/owned/adb'), 'emulator-5562', 'owned-ci-avd')
        self.assertEqual(result, {'state': 'not_connected', 'stop_requested': False})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], [str(Path('/owned/adb')), 'devices'])

    def test_cleanup_rejects_other_avds_and_does_not_guess_an_offline_process(self):
        inventories = [subprocess.CompletedProcess([], 0, b'List of devices attached\nemulator-5562\tdevice\n', b''),
            subprocess.CompletedProcess([], 0, b'List of devices attached\nemulator-5562\toffline\n', b'')]
        for index, inventory in enumerate(inventories):
            replies = [inventory, subprocess.CompletedProcess([], 0, b'other-avd\nOK\n', b'')]
            with patch('android_runtime_probe.require_hosted_emulator'), patch('android_runtime_probe.subprocess.run', side_effect=replies) as run:
                with self.assertRaises(RuntimeError):
                    stop_owned_emulator(Path('/owned/adb'), 'emulator-5562', 'owned-ci-avd')
            self.assertEqual(run.call_count, 2 if index == 0 else 1)

    def test_cleanup_sends_stop_only_after_matching_the_exact_owned_avd(self):
        replies = [subprocess.CompletedProcess([], 0, b'List of devices attached\nemulator-5562\tdevice\n', b''),
            subprocess.CompletedProcess([], 0, b'owned-ci-avd\nOK\n', b''), subprocess.CompletedProcess([], 0, b'OK\n', b'')]
        with patch('android_runtime_probe.require_hosted_emulator'), patch('android_runtime_probe.subprocess.run', side_effect=replies) as run:
            result = stop_owned_emulator(Path('/owned/adb'), 'emulator-5562', 'owned-ci-avd')
        self.assertTrue(result['stop_requested'])
        self.assertEqual(run.call_args.args[0], [str(Path('/owned/adb')), '-s', 'emulator-5562', 'emu', 'kill'])

    def test_developer_machine_and_phone_are_rejected_before_adb(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                require_hosted_emulator('emulator-5562')
        with patch('android_runtime_probe.sys.platform', 'linux'), patch.dict(os.environ,
            {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            with self.assertRaisesRegex(RuntimeError, 'unowned emulator or physical'):
                require_hosted_emulator('owned-phone-serial')

    def test_changed_apk_is_rejected_even_when_filename_and_commit_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'OpenNexus-android-debug.apk').write_bytes(b'changed apk')
            (root / 'source-provenance.json').write_text(json.dumps({'source_commit': 'fixed'}))
            (root / 'package-verification.json').write_text(json.dumps({'passed': True, 'source_clean': True,
                'source_commit': 'fixed', 'package': APP, 'debug_signing': True, 'sha256': digest(b'original apk')}))
            with self.assertRaisesRegex(RuntimeError, 'APK bytes changed'):
                validate_binding(root, 'fixed')

    def test_native_test_apk_is_bound_to_exact_bytes_signer_and_owned_target_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=b'owned-original-app';test=b'owned-original-test'
            (root/'OpenNexus-android-debug.apk').write_bytes(app)
            target=root/'OpenNexus-android-tests.apk';target.write_bytes(test)
            (root/'source-provenance.json').write_text(json.dumps({'source_commit':'fixed'}))
            proof={'passed':True,'source_clean':True,'source_commit':'fixed','package':APP,'debug_signing':True,
                'sha256':digest(app),'signer_certificate_sha256':'a'*64,
                'native_tests':{'apk':target.name,'package':APP+'.test','target_package':APP,
                    'runner':'androidx.test.runner.AndroidJUnitRunner','test_class':'cc.kronecker.opennexus.sync.SyncCredentialStoreTest',
                    'expected_tests':4,'signer_certificate_sha256':'a'*64,'bytes':len(test),'sha256':digest(test)}}
            metadata=root/'package-verification.json';metadata.write_text(json.dumps(proof))
            validate_binding(root,'fixed')
            proof['native_tests']['expected_tests']=5;metadata.write_text(json.dumps(proof))
            with self.assertRaisesRegex(Exception,'Native Rust worker binding mismatch'):validate_binding(root,'fixed')
            symbol='Java_cc_kronecker_opennexus_sync_NativeCredentialProbe_roundTrip'
            proof['native_secret_broker']={'debug_test_entry_only':True,'WebView_read_command':False,
                'native_symbols':{'arm64-v8a':symbol,'x86_64':symbol}}
            metadata.write_text(json.dumps(proof));validate_binding(root,'fixed')
            proof['native_secret_broker']['WebView_read_command']=True;metadata.write_text(json.dumps(proof))
            with self.assertRaisesRegex(Exception,'Native Rust worker binding mismatch'):validate_binding(root,'fixed')
            proof['native_tests']['expected_tests']=4
            target.write_bytes(b'changed-test')
            metadata.write_text(json.dumps(proof))
            with self.assertRaisesRegex(Exception,'Native test APK bytes changed'):validate_binding(root,'fixed')
            target.write_bytes(test);proof['native_tests']['target_package']='some.other.app';metadata.write_text(json.dumps(proof))
            with self.assertRaisesRegex(Exception,'Native test binding mismatch'):validate_binding(root,'fixed')
            proof['native_tests']['target_package']=APP;proof['native_tests']['signer_certificate_sha256']='b'*64;metadata.write_text(json.dumps(proof))
            with self.assertRaisesRegex(Exception,'Native test binding mismatch'):validate_binding(root,'fixed')

    def test_an_old_package_proof_cannot_be_used_for_a_new_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source-provenance.json').write_text(json.dumps({'source_commit': 'old'}))
            (root / 'package-verification.json').write_text(json.dumps({'passed': True, 'source_clean': True, 'source_commit': 'old'}))
            with self.assertRaisesRegex(RuntimeError, 'source mismatch'):
                validate_binding(root, 'new')


if __name__ == '__main__':
    unittest.main()
