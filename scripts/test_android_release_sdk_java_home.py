"""Read-only tool references; output paths still require their original identity."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import android_release_sdk_lifecycle as subject
from test_android_release_sdk_lifecycle import SDKLifecycle


class JavaHomeReference(unittest.TestCase):
    setUp = SDKLifecycle.setUp
    command = SDKLifecycle.command

    def reference(self, state):
        # Simulated alias resolution only; no link is created or copied JDK used.
        alias = self.workspace / 'setup-java-reference'; alias.mkdir()
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            return state['target'] if path == alias else original(path, *args, **kwargs)
        return alias, resolve

    def test_existing_readonly_reference_resolves_exact_tools_and_keeps_output_guards(self):
        subject.prepare(self.ctx)
        state = dict(target=self.jdk); alias, resolve = self.reference(state)
        with patch.dict(os.environ, JAVA_HOME=str(alias)), patch.object(Path, 'resolve', resolve), \
             patch.object(subject.subprocess, 'run', side_effect=self.command):
            value = subject.register_owned(self.ctx)
            self.assertTrue(value['passed'], value.get('guard_code'))
            self.assertEqual(subject.registry_ready(self.ctx), value)
        self.assertEqual(value['tools']['java']['path'], str(self.jdk / ('bin/java' + ('.exe' if os.name == 'nt' else ''))))
        self.assertEqual(value['tools']['java_home']['reference'], str(alias))
        self.assertEqual(value['tools']['java_home']['resolved'], str(self.jdk))
        self.assertFalse(list(self.parent.glob('sdk-registry-build-*')))

    def test_reference_retarget_after_command_intent_refuses_before_actual_compiler(self):
        subject.prepare(self.ctx)
        other = self.workspace / 'different-jdk'; (other / 'bin').mkdir(parents=True)
        for p in (self.jdk / 'bin').iterdir(): (other / 'bin' / p.name).write_bytes(p.read_bytes())
        state = dict(target=self.jdk); alias, resolve = self.reference(state); original = subject.emit
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'registry-command-1-intent': state['target'] = other
            return path
        with patch.dict(os.environ, JAVA_HOME=str(alias)), patch.object(Path, 'resolve', resolve), \
             patch.object(subject, 'emit', side_effect=emit), patch.object(subject.subprocess, 'run') as command:
            value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertFalse(value['commands'])
        self.assertEqual(value['guard_code'], 'SDK_REGISTRY_CHANGED'); self.assertTrue(value['build_removed'])
        self.assertFalse((self.ctx['root'] / 'runtime/emulator/package.xml').exists())

    def test_same_target_reference_identity_replacement_is_not_hidden(self):
        subject.prepare(self.ctx)
        state = dict(target=self.jdk, changed=False); alias, resolve = self.reference(state)
        original_emit = subject.emit; original_lstat = Path.lstat
        def emit(ctx, name, value):
            path = original_emit(ctx, name, value)
            if name == 'registry-command-1-intent': state['changed'] = True
            return path
        def lstat(path):
            value = original_lstat(path)
            if path == alias and state['changed']:
                from types import SimpleNamespace
                return SimpleNamespace(**{key: getattr(value, key) + (1 if key == 'st_ino' else 0)
                    for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_mtime_ns')})
            return value
        with patch.dict(os.environ, JAVA_HOME=str(alias)), patch.object(Path, 'resolve', resolve), \
             patch.object(Path, 'lstat', lstat), patch.object(subject, 'emit', side_effect=emit), \
             patch.object(subject.subprocess, 'run') as command:
            value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertTrue(value['build_removed'])
        self.assertEqual(value['guard_code'], 'SDK_REGISTRY_CHANGED')

    def test_reference_changed_during_tool_hashing_is_refused_in_same_observation(self):
        state = dict(target=self.jdk); alias, resolve = self.reference(state); original = subject.fingerprint
        other = self.workspace / 'different-jdk'; other.mkdir()
        def fingerprint(path):
            value = original(path)
            if path.name == 'OwnedEmulatorRegistry.java': state['target'] = other
            return value
        with patch.dict(os.environ, JAVA_HOME=str(alias)), patch.object(Path, 'resolve', resolve), \
             patch.object(subject, 'fingerprint', side_effect=fingerprint), \
             self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_REGISTRY_CHANGED'):
            subject.registry_tools(self.ctx)

    def test_missing_relative_or_parent_reference_has_no_PATH_fallback(self):
        for home in ('', 'relative-jdk', str(self.workspace / 'missing'), str(self.workspace / '..' / 'jdk')):
            with self.subTest(home=home), patch.dict(os.environ, JAVA_HOME=home), \
                 patch.object(subject.subprocess, 'run') as command, self.assertRaises(subject.kernel.ArchiveError):
                subject.registry_tools(self.ctx)
            command.assert_not_called()

    def test_tool_bytes_changed_after_compile_prevent_derive_and_metadata_write(self):
        subject.prepare(self.ctx)
        def command(argv, **kwargs):
            result = self.command(argv, **kwargs)
            if '-d' in argv:
                path = self.jdk / ('bin/java' + ('.exe' if os.name == 'nt' else ''))
                path.write_bytes(b'changed read-only tool fixture')
            return result
        with patch.object(subject.subprocess, 'run', side_effect=command): value = subject.register_owned(self.ctx)
        self.assertFalse(value['passed']); self.assertEqual(len(value['commands']), 1)
        self.assertEqual(value['guard_code'], 'SDK_REGISTRY_CHANGED'); self.assertTrue(value['build_removed'])
        self.assertFalse((self.ctx['root'] / 'runtime/emulator/package.xml').exists())

    def test_reference_environment_switch_to_same_target_is_refused_before_compiler(self):
        subject.prepare(self.ctx); original = subject.emit
        state = dict(target=self.jdk); alias, resolve = self.reference(state)
        def emit(ctx, name, value):
            path = original(ctx, name, value)
            if name == 'registry-command-1-intent': os.environ['JAVA_HOME'] = str(self.jdk)
            return path
        with patch.dict(os.environ, JAVA_HOME=str(alias)), patch.object(Path, 'resolve', resolve), \
             patch.object(subject, 'emit', side_effect=emit), patch.object(subject.subprocess, 'run') as command:
            value = subject.register_owned(self.ctx)
        command.assert_not_called(); self.assertFalse(value['passed']); self.assertTrue(value['build_removed'])
        self.assertEqual(value['guard_code'], 'SDK_REGISTRY_CHANGED')


if __name__ == '__main__': unittest.main()
