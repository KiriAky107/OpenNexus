import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import yaml
import android_release_sdk_lifecycle as subject

ROOT = Path(__file__).resolve().parents[1]
TEMPORARIES = []
BOOT = '12345678-1234-4234-8234-123456789abc'
BOOT_READER = subject.boot


class Response(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data); self.headers = {'Content-Length': str(len(data))}
    def geturl(self): return subject.kernel.FIXED['url']


class SDKLifecycle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='onx-sdk-lifecycle-check-')
        TEMPORARIES.append(temporary.name); self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve(); self.parent = self.workspace / '.build/android-release-runtime'
        for name in ('origin-proof', 'avd', 'emulator-home'): (self.parent / name).mkdir(parents=True)
        sdk = self.workspace / 'global-sdk'; (sdk / 'platform-tools').mkdir(parents=True)
        self.adb = sdk / 'platform-tools/adb'; self.adb.write_bytes(b'fixture, not executable')
        for name in ('platforms/android-36/android.jar', 'build-tools/36.0.0/aapt2', 'build-tools/36.0.0/apksigner',
                     'cmdline-tools/latest/bin/sdkmanager', 'cmdline-tools/latest/bin/avdmanager'):
            path = sdk / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture, not executable')
        (sdk / 'licenses').mkdir(); (sdk / 'licenses/android-sdk-license').write_bytes(b'\n' + b'a' * 40 + b'\n')
        self.addCleanup(patch.stopall)
        patch.dict(subject.os.environ, GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_OS='Linux',
                   RUNTIME_SCENE='sync-timeout', GITHUB_RUN_ID='123456', GITHUB_RUN_ATTEMPT='1', GITHUB_SHA='a' * 40,
                   GITHUB_WORKSPACE=str(self.workspace), ANDROID_HOME=str(sdk), ANDROID_AVD_HOME=str(self.parent / 'avd'),
                   ANDROID_EMULATOR_HOME=str(self.parent / 'emulator-home')).start()
        patch.object(subject.sys, 'platform', 'linux').start()
        patch.object(subject.os, 'getuid', return_value=self.workspace.stat().st_uid, create=True).start()
        patch.object(subject, 'boot', return_value=BOOT).start()
        self.ctx = subject.context(); self.network = []; self.commands = []
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            entry = zipfile.ZipInfo('emulator/emulator'); entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(entry, b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00')
            archive.writestr('emulator/source.properties', b'Pkg.Revision=37.2.12\n')
        self.archive = output.getvalue(); end = self.archive.rfind(b'PK\x05\x06')
        fields = struct.unpack_from('<4s4H2LH', self.archive, end)
        with zipfile.ZipFile(io.BytesIO(self.archive)) as bundle: expanded = sum(x.file_size for x in bundle.infolist())
        spec = dict(subject.kernel.FIXED, bytes=len(self.archive), sha1=hashlib.sha1(self.archive).hexdigest(), entries=2,
                    expanded_bytes=expanded, directory_sha256=hashlib.sha256(self.archive[fields[6]:fields[6] + fields[5]]).hexdigest())
        patch.object(subject.kernel, 'FIXED', spec).start()
        def open_once(request, **kwargs):
            self.network.append(request); self.assertEqual(request.full_url, spec['url']); return Response(self.archive)
        patch.object(subject.kernel.urllib.request, 'build_opener', return_value=SimpleNamespace(open=open_once)).start()

    def command(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[0].endswith('sdkmanager'):
            self.assertEqual(argv[1], '--sdk_root=' + str(self.ctx['root'] / 'runtime'))
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            if argv[-1] == '--list_installed':
                return subprocess.CompletedProcess(argv, 0, b'emulator | 37.2.12 | Android Emulator | emulator\n', b'private repository warning')
            self.assertEqual(argv[-2:], ['--install', 'system-images;android-36;google_apis;x86_64'])
            image = self.ctx['root'] / 'runtime/system-images/android-36/google_apis/x86_64'; image.mkdir(parents=True)
            (image / 'source.properties').write_bytes(b'AndroidVersion.ApiLevel=36\nSystemImage.Abi=x86_64\nSystemImage.TagId=google_apis\n')
            (image / 'system.img').write_bytes(b'fixture image, not bootable')
            return subprocess.CompletedProcess(argv, 0, b'installed image', b'')
        if argv == [str(self.adb), 'devices']:
            return subprocess.CompletedProcess(argv, 0, b'List of devices attached\n\n', b'')
        self.assertEqual(argv[0], str(self.ctx['launcher']))
        if argv[-1] == '-version':
            return subprocess.CompletedProcess(argv, 0,
                b'INFO         | Android emulator version 37.2.12.0 (build_id 16428233) (CL:N/A)\nOther unapproved stdout\n',
                b'private SDK diagnostic')
        self.assertEqual(argv[-1], '-accel-check')
        return subprocess.CompletedProcess(argv, 0, b'accel usable\n', b'')

    def prepared_image(self):
        self.assertTrue(subject.prepare(self.ctx)['passed'])
        with patch.object(subject.subprocess, 'run', side_effect=self.command):
            self.assertTrue(subject.install_image(self.ctx)['passed'])

    def ready(self, *, observe=True):
        self.prepared_image()
        self.assertEqual(subject.select_launcher(self.ctx), str(self.ctx['launcher']))
        with patch.object(subject.subprocess, 'run', side_effect=self.command):
            self.assertTrue(subject.probe(self.ctx)['passed'])
        if observe:
            avd = self.parent / 'avd/opennexus-ci-123456-1.avd'; avd.mkdir()
            (avd / 'config.ini').write_bytes(b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\nabi.type=x86_64\ntag.id=google_apis\n')
            subject.bind_avd(self.ctx)
            (self.parent / 'emulator.pid').write_bytes(b'321\n')
            identity = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=17, state='S', executable=str(self.ctx['launcher']))
            with patch.object(subject, 'proc', return_value=identity): subject.observe(self.ctx)

    def test_actual_kernel_and_closed_records_to_readonly_cleanup(self):
        self.ready(); self.assertEqual(len(self.network), 1)
        version = (self.parent / 'emulator-version.txt').read_bytes()
        self.assertEqual(version, b'Android emulator version 37.2.12.0 (build_id 16428233)\n')
        evidence = '\n'.join(p.read_text() for p in self.ctx['evidence'].glob('sdk-*.json'))
        self.assertNotIn('private SDK diagnostic', evidence); self.assertNotIn('Other unapproved stdout', evidence)
        with patch.object(subject, 'proc', return_value=None), patch.object(subject, 'sdk_processes', return_value=[]), \
             patch.object(subject.subprocess, 'run', side_effect=self.command):
            result = subject.cleanup(self.ctx)
        self.assertTrue(result['passed']); self.assertTrue(result['SDK_removed_here']); self.assertFalse(self.ctx['root'].exists())
        self.assertFalse(result['runtime_verified']); self.assertTrue(self.adb.exists())
        self.assertEqual([c for c in self.commands if c[0] == str(self.adb)], [[str(self.adb), 'devices']])
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'): subject.cleanup(self.ctx)

    def test_prepare_unknown_or_stale_originals_never_reenter_download(self):
        original = self.parent / 'emulator.log'; original.write_bytes(b'original log')
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'): subject.prepare(self.ctx)
        self.assertEqual(original.read_bytes(), b'original log'); self.assertFalse(self.network); original.unlink()
        subject.emit(self.ctx, 'prepare-intent', dict(owner=self.ctx['owner'], original=True))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'): subject.prepare(self.ctx)
        self.assertFalse(self.network)

    def test_failed_download_is_recorded_and_cleanup_does_not_make_a_runtime_pass(self):
        with patch.object(subject.kernel.urllib.request, 'build_opener',
                          return_value=SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(OSError('private')))):
            value = subject.prepare(self.ctx)
        self.assertFalse(value['passed']); self.assertFalse(self.ctx['root'].exists())
        result = subject.cleanup(self.ctx)
        self.assertTrue(result['passed']); self.assertFalse(result['SDK_removed_here']); self.assertFalse(result['runtime_verified'])

    def test_selected_launcher_tamper_and_unknown_version_never_accept_probe(self):
        self.prepared_image(); subject.select_launcher(self.ctx)
        def command(argv, **kwargs): return subprocess.CompletedProcess(argv, 0, b'private wrong version', b'private')
        with patch.object(subject.subprocess, 'run', side_effect=command): result = subject.probe(self.ctx)
        self.assertFalse(result['passed']); self.assertFalse((self.parent / 'emulator-version.txt').exists())
        self.assertNotIn('private wrong version', json.dumps(result))
        self.ctx['launcher'].write_bytes(b'changed payload' * 3)
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAUNCHER_CHANGED'): subject.prepared(self.ctx)

    def test_probe_wrong_result_or_owner_rejects_observation_before_PID_read(self):
        self.ready(observe=False)
        path = self.ctx['evidence'] / 'sdk-probe-result.json'; value = json.loads(path.read_bytes()); value['commands'] = []
        path.write_text(json.dumps(value))
        with patch.object(subject, 'proc') as process, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_PROBE_IDENTITY'):
            subject.observe(self.ctx)
        process.assert_not_called()

    def test_observe_exact_original_PID_record_and_no_duplicate_query(self):
        self.ready()
        with patch.object(subject, 'proc') as process, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.observe(self.ctx)
        process.assert_not_called()

    def test_cleanup_live_child_clock_or_boot_unknown_keeps_SDK_and_never_stops(self):
        self.ready()
        with patch.object(subject, 'proc', return_value=None), patch.object(subject, 'sdk_processes', return_value=[322]), \
             patch.object(subject.time, 'monotonic', side_effect=[0, 0, 11]), patch.object(subject.time, 'sleep'), \
             patch.object(subject.subprocess, 'run') as command:
            result = subject.cleanup(self.ctx)
        self.assertFalse(result['passed']); self.assertFalse(result['SDK_removed_here']); self.assertTrue(self.ctx['root'].exists())
        command.assert_not_called(); self.assertTrue(result['no_stop_or_kill'])

    def test_cleanup_PID_reuse_is_not_treated_as_original_exit(self):
        self.ready()
        changed = dict(pid=321, uid=self.ctx['owner']['uid'], start_ticks=999, state='S', executable='/another/process')
        with patch.object(subject, 'proc', return_value=changed), patch.object(subject, 'sdk_processes', return_value=[]):
            result = subject.cleanup(self.ctx)
        self.assertEqual(result['guard_code'], 'SDK_ORIGINAL_PID_REUSED'); self.assertTrue(self.ctx['root'].exists())

    def test_last_owner_observation_finishing_after_deadline_cannot_delete(self):
        self.ready()
        with patch.object(subject, 'proc', return_value=None), patch.object(subject, 'sdk_processes', return_value=[]), \
             patch.object(subject.subprocess, 'run', side_effect=self.command), \
             patch.object(subject.time, 'monotonic', side_effect=[0, 0, 11]):
            result = subject.cleanup(self.ctx)
        self.assertEqual(result['guard_code'], 'SDK_CLEANUP_DEADLINE'); self.assertTrue(self.ctx['root'].exists())

    def test_extra_JSON_duplicate_keys_and_owner_change_do_not_leak_or_delete(self):
        self.ready()
        path = self.ctx['evidence'] / 'sdk-prepare-result.json'; original = path.read_bytes()
        path.write_bytes(original[:-2] + b',"private_field":"secret"}\n')
        with self.assertRaises(subject.kernel.ArchiveError): subject.prepared(self.ctx)
        path.write_bytes(original.replace(b'"passed": true', b'"passed": true,"passed": true'))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_JSON'): subject.prepared(self.ctx)
        path.write_bytes(original); (self.ctx['root'] / 'owner.json').write_bytes(b'changed owner')
        with patch.object(subject, 'sdk_processes') as scan:
            result = subject.cleanup(self.ctx)
        self.assertFalse(result['passed']); self.assertTrue(self.ctx['root'].exists()); scan.assert_not_called()

    def test_ADB_connected_or_unknown_is_never_process_absence(self):
        self.ready()
        def present(argv, **kwargs): return subprocess.CompletedProcess(argv, 0, b'List of devices attached\nemulator-5562\tdevice\n', b'')
        with patch.object(subject, 'proc', return_value=None), patch.object(subject, 'sdk_processes', return_value=[]), \
             patch.object(subject.subprocess, 'run', side_effect=present): result = subject.cleanup(self.ctx)
        self.assertEqual(result['guard_code'], 'SDK_AVD_PRESENT'); self.assertTrue(self.ctx['root'].exists())

    def test_no_launch_observation_can_clean_only_with_two_actual_absence_guards(self):
        subject.prepare(self.ctx)
        with patch.object(subject, 'sdk_processes', return_value=[]) as scan, \
             patch.object(subject.subprocess, 'run', side_effect=self.command): result = subject.cleanup(self.ctx)
        self.assertTrue(result['passed']); self.assertEqual(scan.call_count, 2)
        self.assertFalse(result['original_host_PID_absent_or_exited']); self.assertFalse(self.ctx['root'].exists())

    def test_closed_CLI_rejects_unknown_actions_and_path_arguments_before_context(self):
        for argv in ([], ['prepare', '--url', 'private'], ['cleanup', '/private'], ['sync-stop'], ['observe-launch', '--pid', '1']):
            with patch.object(subject, 'context') as context, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_UNKNOWN_ACTION'):
                subject.main(argv)
            context.assert_not_called()
        with patch.dict(subject.os.environ, RUNTIME_SCENE='sync-stop'), self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_CONTEXT'):
            subject.context()

    def test_actual_proc_parser_reads_only_fixed_stat_and_executable_metadata(self):
        directory = self.workspace / 'proc'; path = directory / '321'; path.mkdir(parents=True)
        (path / 'stat').write_bytes(b'321 (qemu name) S ' + b'0 ' * 18 + b'17 ' + b'0 ' * 5)
        real_path = Path
        with patch.object(subject, 'Path', side_effect=lambda p: directory if p == '/proc' else real_path(p)), \
             patch.object(subject.os, 'readlink', return_value=str(self.ctx['launcher'])):
            value = subject.proc(321)
        self.assertEqual(value['start_ticks'], 17); self.assertEqual(value['pid'], 321)

    def test_missing_registered_emulator_never_attempts_a_second_emulator_download(self):
        subject.prepare(self.ctx); commands = []
        def absent(argv, **kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'Installed packages:\nPath | Version | Description | Location\n', b'')
        with patch.object(subject.subprocess, 'run', side_effect=absent): value = subject.install_image(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_REGISTERED_EMULATOR')
        self.assertEqual(len(commands), 1); self.assertEqual(commands[0][-1], '--list_installed')
        self.assertNotIn('--install', commands[0]); self.assertEqual(len(self.network), 1)

    def test_wrong_private_image_API_is_rejected_before_launcher_selection(self):
        subject.prepare(self.ctx)
        def wrong(argv, **kwargs):
            value = self.command(argv, **kwargs)
            if argv[-1] == 'system-images;android-36;google_apis;x86_64':
                props = self.ctx['root'] / 'runtime/system-images/android-36/google_apis/x86_64/source.properties'
                props.write_bytes(props.read_bytes().replace(b'ApiLevel=36', b'ApiLevel=35'))
            return value
        with patch.object(subject.subprocess, 'run', side_effect=wrong): value = subject.install_image(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_IMAGE_IDENTITY')
        with self.assertRaises(subject.kernel.ArchiveError): subject.select_launcher(self.ctx)
        self.assertFalse((self.ctx['evidence'] / 'sdk-launch-selection-intent.json').exists())

    def test_actual_AVD_config_must_select_private_image_and_cannot_change_after_binding(self):
        self.ready(observe=False)
        avd = self.parent / 'avd/opennexus-ci-123456-1.avd'; avd.mkdir()
        path = avd / 'config.ini'
        path.write_bytes(b'image.sysdir.1=../outside\nabi.type=x86_64\ntag.id=google_apis\n')
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'): subject.bind_avd(self.ctx)
        path.write_bytes(b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\nabi.type=x86_64\ntag.id=google_apis\n')
        subject.bind_avd(self.ctx); path.write_bytes(path.read_bytes() + b'changed=true\n')
        with patch.object(subject, 'proc') as process, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_AVD_BINDING'):
            subject.observe(self.ctx)
        process.assert_not_called()

    def test_bounded_proc_scan_keeps_only_own_SDK_executable_presence(self):
        directory = self.workspace / 'proc'; directory.mkdir()
        for pid in ('321', '322'): (directory / pid).mkdir()
        real_path = Path
        def executable(path):
            return str(self.ctx['launcher']) if path.parent.name == '321' else '/private/provider-process'
        with patch.object(subject, 'Path', side_effect=lambda p: directory if p == '/proc' else real_path(p)), \
             patch.object(subject.os, 'readlink', side_effect=executable):
            value = subject.sdk_processes(self.ctx)
        self.assertEqual(value, [321]); self.assertNotIn('private', json.dumps(value))
        self.assertFalse(subject.owns_executable(self.ctx, str(self.ctx['root'] / 'runtime/emulator/../foreign')))

    def test_exact_boot_bytes_reject_extra_or_private_data(self):
        for data in ((BOOT + '\n').encode(), (BOOT + '\nprivate').encode(), b'private'):
            with patch.object(subject, 'Path', return_value=SimpleNamespace(read_bytes=lambda: data)):
                if len(data) == 37: self.assertEqual(BOOT_READER(), BOOT)
                else:
                    with self.assertRaises(subject.kernel.ArchiveError): BOOT_READER()

    def test_failed_owned_SDK_content_cleans_after_absence_without_changing_global_tools(self):
        self.prepared_image()
        before = {str(p.relative_to(self.ctx['sdk'])): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()}
        self.ctx['launcher'].write_bytes(b'corrupt owned SDK payload')
        with patch.object(subject, 'sdk_processes', return_value=[]), \
             patch.object(subject.subprocess, 'run', side_effect=self.command): value = subject.cleanup(self.ctx)
        self.assertTrue(value['passed']); self.assertFalse(self.ctx['root'].exists())
        self.assertEqual(before, {str(p.relative_to(self.ctx['sdk'])): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()})

    def test_workflow_timeout_only_pinning_absence_cleanup_and_narrow_upload(self):
        flow = yaml.load((ROOT / '.github/workflows/android-release-runtime.yml').read_text('utf-8'), Loader=yaml.BaseLoader)
        steps = flow['jobs']['release-upgrade']['steps']; setup = next(s for s in steps if s.get('name') == 'Prepare only this hosted emulator')
        body = setup['run']; self.assertEqual(setup['env'], {'RUNTIME_SCENE': '${{ inputs.scene }}'})
        self.assertIn('if [[ "$RUNTIME_SCENE" == sync-timeout ]]', body)
        self.assertLess(body.index('android_release_sdk_lifecycle.py prepare'), body.index('select-launcher'))
        self.assertIn('probe-version-and-accel', body); self.assertIn('observe-launch', body)
        self.assertIn('android_release_sdk_lifecycle.py install-image', body)
        self.assertIn('android_release_sdk_lifecycle.py bind-avd', body)
        self.assertIn('env ANDROID_HOME="$sdk_root" ANDROID_SDK_ROOT="$sdk_root"', body)
        self.assertIn('for package in platforms/android-36 build-tools/36.0.0 platform-tools emulator system-images/android-36/google_apis/x86_64;', body)
        self.assertIn('-port 5562 -no-window -no-audio -no-snapshot -no-boot-anim -gpu swiftshader -accel on -memory 2048 -cores 2', body)
        cleanup = next(s for s in steps if s.get('name') == 'Remove only the verified timeout SDK after actual AVD absence')
        self.assertEqual(cleanup['if'], "always() && inputs.scene == 'sync-timeout'")
        self.assertGreater(steps.index(cleanup), next(i for i,s in enumerate(steps) if s.get('name') == "Stop only this job's emulator"))
        upload = next(s for s in steps if s.get('uses') == 'actions/upload-artifact@v7')['with']['path']
        self.assertNotIn('sdk-owned-', upload); self.assertNotIn('/runtime/emulator/', upload); self.assertIn('origin-proof/', upload)
        self.assertNotIn('continue-on-error', cleanup)


if __name__ == '__main__': unittest.main()
