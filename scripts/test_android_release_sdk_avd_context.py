"""Owned AVD command records plus actual vendor SDK-root lookup without an AVD."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle

ACTUAL_JAVA_HOME = os.environ.get('JAVA_HOME', 'E:/java21')
ACTUAL_ANDROID_HOME = os.environ.get('ANDROID_HOME', 'C:/Users/KiriAky/AppData/Local/Android/Sdk')
TEMPORARIES = []


class OwnedAvdContext(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command

    def prepare_probed(self):
        subject.prepare(self.ctx)
        with patch.object(subject.subprocess, 'run', side_effect=self.command):
            self.assertTrue(subject.install_image(self.ctx)['passed'])
            subject.select_launcher(self.ctx)
            self.assertTrue(subject.probe(self.ctx)['passed'])

    def create_command(self, argv, **kwargs):
        self.assertIn('com.android.sdklib.tool.AvdManagerCli', argv)
        descriptor = self.ctx['root'] / 'runtime/cmdline-tools/avd-root-context'
        self.assertEqual(argv[2], '-Dcom.android.sdkmanager.toolsdir=' + str(descriptor))
        self.assertTrue(descriptor.is_dir()); self.assertEqual(kwargs['input'], b'no\n')
        self.assertEqual(kwargs['env']['ANDROID_HOME'], str(self.ctx['root'] / 'runtime'))
        self.assertEqual(kwargs['env']['ANDROID_SDK_ROOT'], str(self.ctx['root'] / 'runtime'))
        self.assertEqual(kwargs['env']['ANDROID_AVD_HOME'], str(self.parent / 'avd'))
        self.assertEqual(argv[argv.index('-cp') + 1], str(self.ctx['sdk'] / 'cmdline-tools/latest/lib/avdmanager-classpath.jar'))
        name = 'opennexus-ci-123456-1'
        self.assertEqual(argv[-8:], ['create', 'avd', '--name', name, '--package', 'system-images;android-36;google_apis;x86_64', '--device', 'pixel_7'])
        directory = self.parent / 'avd' / (name + '.avd'); directory.mkdir()
        (directory / 'config.ini').write_text('image.sysdir.1=system-images/android-36/google_apis/x86_64/\nabi.type=x86_64\ntag.id=google_apis\n')
        directory.with_suffix('.ini').write_text('path=' + str(directory) + '\ntarget=android-36\n')
        return subprocess.CompletedProcess(argv, 0, b'private controlled vendor output', b'')

    def test_original_command_uses_private_vendor_root_and_binds_exact_image_once(self):
        self.prepare_probed()
        before = {str(p): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()}
        with patch.object(subject.subprocess, 'run', side_effect=self.create_command) as command:
            result = subject.create_avd(self.ctx)
        self.assertTrue(result['passed'], result.get('guard_code')); self.assertEqual(command.call_count, 1)
        self.assertEqual(result['command']['returncode'], 0); self.assertFalse(result['runtime_verified'])
        self.assertEqual(result['binding']['avd'], 'opennexus-ci-123456-1')
        self.assertNotIn('private controlled', json.dumps(result))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()})
        with patch.object(subject.subprocess, 'run') as again, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.create_avd(self.ctx)
        again.assert_not_called()

    def test_existing_original_AVD_is_preserved_without_a_command(self):
        self.prepare_probed(); directory = self.parent / 'avd/opennexus-ci-123456-1.avd'; directory.mkdir()
        original = directory / 'config.ini'; original.write_bytes(b'preserve original owned fixture')
        with patch.object(subject.subprocess, 'run') as command: value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_AVD_ALREADY_EXISTS')
        command.assert_not_called(); self.assertEqual(original.read_bytes(), b'preserve original owned fixture')

    def test_unknown_AVD_command_is_not_retried_or_implicitly_stopped(self):
        self.prepare_probed()
        with patch.object(subject.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed JVM', 120)) as command:
            value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['error_kind'], 'timeout'); self.assertEqual(command.call_count, 1)
        self.assertTrue(self.ctx['root'].is_dir()); self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())
        with patch.object(subject.subprocess, 'run') as again, self.assertRaises(subject.kernel.ArchiveError): subject.create_avd(self.ctx)
        again.assert_not_called()

    def test_owner_change_after_attempt_record_refuses_the_actual_JVM(self):
        self.prepare_probed(); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'avd-create-command-attempt': (ctx['root'] / 'owner.json').write_bytes(b'changed fixture owner')
            return path
        with patch.object(subject, 'emit', side_effect=emit), patch.object(subject.subprocess, 'run') as command:
            value = subject.create_avd(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertIsNone(value['command'])
        self.assertTrue((self.ctx['evidence'] / 'sdk-avd-create-command-attempt.json').exists())

    def test_registry_changed_by_command_refuses_before_binding(self):
        self.prepare_probed()
        def command(argv, **kwargs):
            value = self.create_command(argv, **kwargs)
            path = self.ctx['root'] / 'runtime/emulator/package.xml'; path.write_bytes(path.read_bytes() + b'changed')
            return value
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertIsNone(value['binding'])
        self.assertFalse((self.ctx['evidence'] / 'sdk-avd-binding.json').exists())

    def test_changed_locator_cannot_redirect_the_original_AVD(self):
        self.prepare_probed()
        def command(argv, **kwargs):
            value = self.create_command(argv, **kwargs)
            (self.parent / 'avd/opennexus-ci-123456-1.ini').write_text('path=/unowned-avd\n')
            return value
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_AVD_CONTEXT')
        self.assertIsNone(value['binding'])

    def test_unowned_image_in_generated_config_still_fails_original_binding(self):
        self.prepare_probed()
        def command(argv, **kwargs):
            value = self.create_command(argv, **kwargs)
            (self.parent / 'avd/opennexus-ci-123456-1.avd/config.ini').write_text('image.sysdir.1=/unowned-image\nabi.type=x86_64\ntag.id=google_apis\n')
            return value
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.create_avd(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_AVD_BINDING')
        self.assertIsNone(value['binding'])

    def test_new_action_accepts_no_path_package_name_or_additional_parameter(self):
        for args in (['create-avd', '--path', '/private'], ['create-avd', 'other'], ['create-anything']):
            with patch.object(subject, 'context') as context, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_UNKNOWN_ACTION'):
                subject.main(args)
            context.assert_not_called()


class VendorRootContract(unittest.TestCase):
    def test_actual_vendor_init_uses_tools_property_instead_of_ANDROID_HOME(self):
        root = Path(__file__).resolve().parents[1]
        source = root / 'scripts/android-sdk-tests/OwnedAvdRootCheck.java'
        sdk = Path(ACTUAL_ANDROID_HOME).resolve(); jdk = Path(ACTUAL_JAVA_HOME).resolve()
        suffix = '.exe' if os.name == 'nt' else ''
        javac, java = jdk / ('bin/javac' + suffix), jdk / ('bin/java' + suffix)
        cp = sdk / 'cmdline-tools/latest/lib/avdmanager-classpath.jar'
        inputs = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, javac, java, cp)}
        commands = []
        try:
            with tempfile.TemporaryDirectory(prefix='onx-sdk-avd-root-check-') as folder:
                TEMPORARIES.append(folder); temp = Path(folder).resolve(); classes = temp / 'classes'; classes.mkdir()
                private = temp / 'owned-sdk'; descriptor = private / 'cmdline-tools/avd-root-context'; descriptor.mkdir(parents=True)
                for name in ('user', 'avd', 'emulator-home'): (temp / name).mkdir()
                env = dict(os.environ, ANDROID_HOME=str(private), ANDROID_SDK_ROOT=str(private),
                           ANDROID_USER_HOME=str(temp / 'user'), ANDROID_AVD_HOME=str(temp / 'avd'), ANDROID_EMULATOR_HOME=str(temp / 'emulator-home'))
                for argv in ([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-cp', str(cp), '-d', str(classes), str(source)],
                             [str(java), '-Xmx64m', '-Duser.home=' + str(temp / 'user'), '-cp', str(classes) + os.pathsep + str(cp),
                              'com.android.sdklib.tool.OwnedAvdRootCheck', str(sdk), str(descriptor)]):
                    value = subprocess.run(argv, env=env, capture_output=True, timeout=40, check=False)
                    commands.append(dict(argv=argv, returncode=value.returncode,
                        stdout_base64=base64.b64encode(value.stdout).decode(), stderr_base64=base64.b64encode(value.stderr).decode()))
                    self.assertEqual(value.returncode, 0, value.stderr.decode('utf8', 'replace'))
                self.assertIn(b'SDK_AVD_ROOT_CONTRACT_OK metadata_only=1 AVD_created=0', value.stdout)
                self.assertFalse(list((temp / 'avd').iterdir()))
                for path, digest in inputs.items(): self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        finally:
            print('SDK_AVD_ROOT_VENDOR_PROOF ' + json.dumps(dict(inputs=inputs, commands=commands,
                actual_vendor_init_only=True, AVD_created_or_emulator_started=False,
                temporary_roots_remaining=[n for n in TEMPORARIES if Path(n).exists()])))


if __name__ == '__main__': unittest.main()
