"""Owned registry integration; small fixtures and one actual vendor JVM path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import yaml
import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle, REGISTRY_FIXTURE_XML, TEMPORARIES

ACTUAL_JAVA_HOME = os.environ.get('JAVA_HOME', 'E:/java21')
ACTUAL_ANDROID_HOME = os.environ.get('ANDROID_HOME', 'C:/Users/KiriAky/AppData/Local/Android/Sdk')


class RegistrationFlow(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command

    def assert_build_removed(self):
        self.assertFalse(list(self.parent.glob('sdk-registry-build-*')))

    def test_new_metadata_has_exclusive_intents_vendor_readback_and_immediate_build_cleanup(self):
        subject.prepare(self.ctx)
        with patch.object(subject.subprocess, 'run', side_effect=self.command): value = subject.register_owned(self.ctx)
        self.assertTrue(value['passed'], value.get('guard_code')); self.assertTrue(value['created_here'])
        self.assertTrue(value['build_removed']); self.assertFalse(value['SDKmanager_registered'])
        self.assertEqual((self.ctx['root'] / 'runtime/emulator/package.xml').read_bytes(), REGISTRY_FIXTURE_XML)
        self.assertEqual(subject.registry_ready(self.ctx), value); self.assert_build_removed()
        before = len(self.commands)
        with patch.object(subject.subprocess, 'run', side_effect=self.command), \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'):
            subject.register_owned(self.ctx)
        self.assertEqual(len(self.commands), before)

    def test_existing_metadata_is_readonly_validated_without_derive_or_replacement(self):
        subject.prepare(self.ctx); target = self.ctx['root'] / 'runtime/emulator/package.xml'
        original = REGISTRY_FIXTURE_XML.replace(b'><', b'>\n<'); self.accepted_registry_xml.add(original)
        target.write_bytes(original)
        with patch.object(subject.subprocess, 'run', side_effect=self.command): value = subject.register_owned(self.ctx)
        self.assertTrue(value['passed']); self.assertFalse(value['created_here']); self.assertEqual(len(value['commands']), 2)
        self.assertEqual(target.read_bytes(), original); self.assertEqual(subject.registry_ready(self.ctx), value)
        self.assertFalse((self.ctx['evidence'] / 'sdk-registry-write-intent.json').exists()); self.assert_build_removed()

    def test_wrong_properties_refuse_before_compiler_and_never_write_metadata(self):
        subject.prepare(self.ctx); directory = self.ctx['root'] / 'runtime/emulator'
        (directory / 'source.properties').write_bytes(subject.REGISTRY_PROPERTIES + b'\n')
        with patch.object(subject.subprocess, 'run') as command: value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_REGISTRY_SOURCE')
        self.assertFalse((directory / 'package.xml').exists()); self.assert_build_removed()

    def test_existing_alias_is_refused_before_compiler(self):
        subject.prepare(self.ctx); target = self.ctx['root'] / 'runtime/emulator/package.xml'; target.write_bytes(REGISTRY_FIXTURE_XML)
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda path: True if path == target else original(path)), \
             patch.object(subject.subprocess, 'run') as command: value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(target.read_bytes(), REGISTRY_FIXTURE_XML)
        self.assert_build_removed()

    def test_unknown_derive_cleans_build_and_never_calls_sdkmanager_or_reenters(self):
        subject.prepare(self.ctx)
        def command(argv, **kwargs):
            if argv[-1] == 'derive': raise subprocess.TimeoutExpired(argv, 40, output=b'private compiler detail')
            return self.command(argv, **kwargs)
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.install_image(self.ctx)
        self.assertFalse(value['passed']); self.assertFalse(value['commands'])
        path = self.ctx['evidence'] / 'sdk-registry-result.json'; original = path.read_bytes(); result = json.loads(original)
        self.assertFalse(result['passed']); self.assertTrue(result['build_removed']); self.assertEqual(result['error_kind'], 'TimeoutExpired')
        self.assertNotIn('private compiler', original.decode()); self.assertFalse(any(a[0].endswith('sdkmanager') for a in self.commands))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_ORIGINAL_RECORD_EXISTS'): subject.register_owned(self.ctx)
        self.assertEqual(path.read_bytes(), original); self.assert_build_removed()

    def test_unknown_validate_after_write_keeps_original_metadata_without_second_write(self):
        subject.prepare(self.ctx)
        def command(argv, **kwargs):
            if argv[-1] == 'validate': return subprocess.CompletedProcess(argv, 1, b'', b'private validator detail')
            return self.command(argv, **kwargs)
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.register_owned(self.ctx)
        self.assertFalse(value['passed']); self.assertTrue(value['created_here']); self.assertTrue(value['build_removed'])
        self.assertEqual((self.ctx['root'] / 'runtime/emulator/package.xml').read_bytes(), REGISTRY_FIXTURE_XML)
        self.assertNotIn('private validator', json.dumps(value)); self.assert_build_removed()

    def test_owner_change_after_command_intent_refuses_actual_command(self):
        subject.prepare(self.ctx); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'registry-command-1-intent': (ctx['root'] / 'owner.json').write_bytes(b'changed controlled owner')
            return path
        with patch.object(subject, 'emit', side_effect=emit), patch.object(subject.subprocess, 'run') as command:
            value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertTrue(value['build_removed'])
        self.assertFalse(value['commands']); self.assert_build_removed()

    def test_unknown_build_identity_refuses_blind_cleanup_and_cannot_report_success(self):
        subject.prepare(self.ctx); actual_stat = Path.stat; actual_remove = shutil.rmtree
        state = dict(path=None, changed=False)
        def command(argv, **kwargs):
            value = self.command(argv, **kwargs)
            if '-d' in argv: state['path'] = Path(argv[argv.index('-d') + 1])
            if argv[-1] == 'validate': state['changed'] = True
            return value
        def observed(path, *args, **kwargs):
            value = actual_stat(path, *args, **kwargs)
            if state['changed'] and path == state['path']:
                fields = list(value); fields[1] += 1; return os.stat_result(fields)
            return value
        with patch.object(Path, 'stat', observed), patch.object(subject.subprocess, 'run', side_effect=command), \
             patch.object(subject.shutil, 'rmtree', wraps=actual_remove) as remove:
            value = subject.register_owned(self.ctx)
        remove.assert_not_called(); self.assertFalse(value['passed']); self.assertFalse(value['build_removed'])
        self.assertTrue(value['build_cleanup_unknown']); self.assertEqual(value['guard_code'], 'SDK_REGISTRY_BUILD_CLEANUP')
        # Only the mock identity changed. After removing that mock, clean this test's known original output.
        path = state['path']; self.assertEqual(path.parent, self.parent); self.assertEqual(path.resolve(), path)
        actual_remove(path); self.assert_build_removed()

    def test_ready_rejects_changed_package_or_forged_record_before_launcher_selection(self):
        subject.prepare(self.ctx)
        with patch.object(subject.subprocess, 'run', side_effect=self.command): value = subject.install_image(self.ctx)
        self.assertTrue(value['passed'], value.get('guard_code'))
        target = self.ctx['root'] / 'runtime/emulator/package.xml'; original_xml = target.read_bytes()
        target.write_bytes(original_xml + b'private')
        with self.assertRaises(subject.kernel.ArchiveError): subject.select_launcher(self.ctx)
        target.write_bytes(original_xml)
        path = self.ctx['evidence'] / 'sdk-registry-result.json'; original = json.loads(path.read_bytes())
        for key, bad in (('stdin_bytes', False), ('returncode', True), ('stdout_sha256', '0' * 64)):
            changed = json.loads(json.dumps(original)); changed['commands'][0][key] = bad; path.write_text(json.dumps(changed))
            with self.assertRaises(subject.kernel.ArchiveError): subject.select_launcher(self.ctx)
        self.assertFalse((self.ctx['evidence'] / 'sdk-launch-selection-intent.json').exists()); self.assert_build_removed()

    def test_registry_change_during_catalogue_read_prevents_the_image_install(self):
        subject.prepare(self.ctx)
        def command(argv, **kwargs):
            value = self.command(argv, **kwargs)
            if argv[-1] == '--list_installed':
                path = self.ctx['root'] / 'runtime/emulator/package.xml'; path.write_bytes(path.read_bytes() + b'changed')
            return value
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.install_image(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_REGISTRY_CHANGED')
        self.assertEqual(len(value['commands']), 1)
        self.assertFalse(any(a[-1] == 'system-images;android-36;google_apis;x86_64' for a in self.commands))
        self.assert_build_removed()

    def test_actual_vendor_JVM_called_by_owned_flow_without_install_or_retained_classes(self):
        subject.prepare(self.ctx); original = subject.registry_tools
        def tools(_):
            with patch.dict(os.environ, JAVA_HOME=ACTUAL_JAVA_HOME):
                return original(dict(self.ctx, sdk=Path(ACTUAL_ANDROID_HOME)))
        before = tools(self.ctx)
        with patch.object(subject, 'registry_tools', side_effect=tools):
            value = subject.register_owned(self.ctx)
            self.assertTrue(value['passed'], value.get('guard_code')); self.assertEqual(subject.registry_ready(self.ctx), value)
        self.assertEqual(before, tools(self.ctx)); self.assertEqual(len(value['commands']), 3)
        self.assertFalse(value['SDKmanager_registered']); self.assert_build_removed()
        print('SDK_OWNED_REGISTRY_REAL_PROOF ' + json.dumps(value))

    def test_workflow_JDK_source_and_artifact_boundaries_support_closed_registration(self):
        root = Path(__file__).resolve().parents[1]
        flow = yaml.load((root / '.github/workflows/android-release-runtime.yml').read_text('utf8'), Loader=yaml.BaseLoader)
        steps = flow['jobs']['release-upgrade']['steps']; setup = next(s for s in steps if s.get('name') == 'Prepare only this hosted emulator')
        java = next(s for s in steps if s.get('uses') == 'actions/setup-java@v5')
        self.assertEqual(java['with']['java-version'], '21'); self.assertLess(steps.index(java), steps.index(setup))
        self.assertIn('android_release_sdk_lifecycle.py install-image', setup['run'])
        upload = next(s for s in steps if s.get('uses') == 'actions/upload-artifact@v7')['with']['path']
        self.assertIn('origin-proof/', upload); self.assertNotIn('sdk-registry-build-', upload); self.assertNotIn('package.xml', upload)
        self.assertNotIn('OwnedEmulatorRegistry.java', [p.name for p in (root / 'scripts/android-runtime').glob('*.java')])


if __name__ == '__main__': unittest.main()
