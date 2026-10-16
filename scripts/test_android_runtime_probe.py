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
from unittest.mock import patch

from android_runtime_probe import APP, DRIVER, IMAGE_BODY, RICH_IMAGE_TEXT, IMAGE_BYTES, Probe, digest, require_hosted_emulator, stop_owned_emulator, validate_binding, validate_build_job


class RuntimeBoundaries(unittest.TestCase):
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

    def test_an_old_package_proof_cannot_be_used_for_a_new_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source-provenance.json').write_text(json.dumps({'source_commit': 'old'}))
            (root / 'package-verification.json').write_text(json.dumps({'passed': True, 'source_clean': True, 'source_commit': 'old'}))
            with self.assertRaisesRegex(RuntimeError, 'source mismatch'):
                validate_binding(root, 'new')


if __name__ == '__main__':
    unittest.main()
