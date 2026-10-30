"""Real bounded package files/copies/CRC and unknown-owner cleanup; no SDK execution."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import android_release_sdk_layout as subject

TEMPORARIES = []


class OwnedSdkLayout(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='onx-sdk-layout-check-')
        TEMPORARIES.append(temporary.name); self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve(); parent = self.temp / '.build/android-release-runtime'
        evidence = parent / 'origin-proof'; evidence.mkdir(parents=True)
        root = parent / 'sdk-owned-123456-1'; (root / 'runtime').mkdir(parents=True)
        sdk = self.temp / 'global-sdk'; sdk.mkdir()
        owner = dict(GITHUB_RUN_ID='123456', GITHUB_RUN_ATTEMPT='1', GITHUB_SHA='a' * 40,
                     uid=root.stat().st_uid, host_boot_id='12345678-1234-4234-8234-123456789abc')
        (root / 'owner.json').write_text(json.dumps({k: owner[k] for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}, sort_keys=True))
        self.ctx = dict(root=root, parent=parent, evidence=evidence, sdk=sdk, owner=owner)
        tools = sdk / 'platform-tools'; tools.mkdir()
        self.adb = tools / 'adb'; self.adb.write_bytes(b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00fixture'); self.adb.chmod(0o755)
        (tools / 'NOTICE.txt').write_bytes(b'controlled public vendor notice')
        (tools / 'source.properties').write_bytes(b'Pkg.Revision=36.0.2\n')
        (tools / 'package.xml').write_bytes(b'<repository><localPackage path="platform-tools"/></repository>')
        platform = sdk / 'platforms/android-36'; platform.mkdir(parents=True)
        (platform / 'source.properties').write_bytes(b'Pkg.Revision=2\nAndroidVersion.ApiLevel=36\n')
        (platform / 'package.xml').write_bytes(b'<repository><localPackage path="platforms;android-36"><type-details><api-level>36</api-level></type-details></localPackage></repository>')
        with zipfile.ZipFile(platform / 'android.jar', 'w') as archive: archive.writestr('android/Fixture.class', b'controlled jar bytes')
        resources = platform / 'data/res/values'; resources.mkdir(parents=True)
        (resources / 'strings.xml').write_bytes(b'<resources/>')
        self.guard_calls = 0

    def guard(self):
        self.guard_calls += 1
        subject._owner_guard(self.ctx)

    def original(self):
        return {p.relative_to(self.ctx['sdk']).as_posix(): p.read_bytes() for p in self.ctx['sdk'].rglob('*') if p.is_file()}

    def record(self, name):
        return self.ctx['evidence'] / ('sdk-layout-' + name + '.json')

    def assert_targets_removed(self):
        for package in subject.PACKAGES: self.assertFalse((self.ctx['root'] / 'runtime' / package).exists())

    def test_real_full_copy_modes_metadata_CRC_and_fresh_ready_preserve_original_source(self):
        before = self.original(); value = subject.prepare(self.ctx, self.guard)
        self.assertTrue(value['passed'], value.get('guard_code')); self.assertEqual(value['files'], 8)
        self.assertFalse(value['SDKmanager_install_or_runtime_verified']); self.assertGreater(self.guard_calls, 2)
        self.assertEqual(subject.ready(self.ctx, self.guard), value); self.assertEqual(before, self.original())
        self.assertEqual(subject.inventory(self.ctx['sdk'])[0], subject.inventory(self.ctx['root'] / 'runtime')[0])
        manifest = json.loads(self.record('inventory').read_bytes())
        self.assertEqual(value['inventory_sha256'], hashlib.sha256(self.record('inventory').read_bytes()).hexdigest())
        self.assertEqual(set(manifest['packages']), set(subject.PACKAGES))

    def test_existing_target_is_never_overwritten_or_removed(self):
        target = self.ctx['root'] / 'runtime/platform-tools'; target.mkdir(); original = target / 'preserved'; original.write_bytes(b'original')
        value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_TARGET')
        self.assertEqual(original.read_bytes(), b'original')

    def test_original_intent_and_partial_result_cannot_resume_or_recopy(self):
        value = subject.prepare(self.ctx, self.guard); self.assertTrue(value['passed'])
        before = self.record('result').read_bytes()
        with patch.object(subject, '_copy_file') as copy, self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_ORIGINAL_RECORD_EXISTS'):
            subject.prepare(self.ctx, self.guard)
        copy.assert_not_called(); self.assertEqual(self.record('result').read_bytes(), before)

    def test_failed_copy_removes_only_created_packages_including_existing_empty_parent(self):
        (self.ctx['root'] / 'runtime/platforms').mkdir()
        before = self.original(); original = subject._copy_file
        def copy(source, target, *args):
            original(source, target, *args)
            if source.name == 'strings.xml': raise OSError('private controlled I/O failure')
        with patch.object(subject, '_copy_file', side_effect=copy): value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertTrue(value['failed_copy_removed']); self.assertFalse(value['cleanup_unknown'])
        self.assert_targets_removed(); self.assertEqual(before, self.original())
        self.assertTrue((self.ctx['root'] / 'runtime/platforms').is_dir())
        self.assertNotIn('private controlled', json.dumps(value))

    def test_owner_change_prevents_blind_cleanup_or_a_success_receipt(self):
        original = subject._copy_file
        def copy(source, target, *args):
            original(source, target, *args)
            (self.ctx['root'] / 'owner.json').write_bytes(b'changed owner fixture')
        with patch.object(subject, '_copy_file', side_effect=copy), patch.object(subject.shutil, 'rmtree') as remove:
            value = subject.prepare(self.ctx, self.guard)
        remove.assert_not_called(); self.assertFalse(value['passed']); self.assertTrue(value['cleanup_unknown'])
        self.assertFalse(value['failed_copy_removed']); self.assertTrue((self.ctx['root'] / 'runtime/platform-tools').exists())

    def test_changed_source_bytes_even_with_restored_mtime_refuse_and_clear_derived_copies(self):
        original = subject._copy_file; before = self.adb.stat(); raw = self.adb.read_bytes()
        def copy(source, target, *args):
            original(source, target, *args)
            if source.name == 'strings.xml':
                self.adb.write_bytes(raw[:-1] + b'X'); os.utime(self.adb, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch.object(subject, '_copy_file', side_effect=copy): value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_CHANGED')
        self.assertTrue(value['failed_copy_removed']); self.assert_targets_removed()

    def test_empty_or_wrong_ABI_package_cannot_be_a_marker_directory(self):
        self.adb.write_bytes(b'not an ELF SDK client')
        value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_ABI'); self.assert_targets_removed()

    def test_wrong_API_or_package_XML_is_rejected_before_any_copy(self):
        path = self.ctx['sdk'] / 'platforms/android-36/package.xml'
        path.write_bytes(path.read_bytes().replace(b'<api-level>36', b'<api-level>35'))
        with patch.object(subject, '_copy_file') as copy: value = subject.prepare(self.ctx, self.guard)
        copy.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_METADATA')
        self.assert_targets_removed()

    def test_bad_jar_CRC_or_non_ZIP_is_rejected_without_installer_or_copy(self):
        (self.ctx['sdk'] / 'platforms/android-36/android.jar').write_bytes(b'not a jar')
        value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['error_kind'], 'metadata'); self.assert_targets_removed()

    def test_actual_JAR_entry_CRC_mismatch_refuses_before_copy(self):
        path = self.ctx['sdk'] / 'platforms/android-36/android.jar'
        raw = bytearray(path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            entry = archive.infolist()[0]
            offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
        raw[offset] ^= 1; path.write_bytes(raw)
        with patch.object(subject, '_copy_file') as copy: value = subject.prepare(self.ctx, self.guard)
        copy.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_METADATA')
        self.assert_targets_removed()

    def test_replaced_target_parent_refuses_before_next_write_and_preserves_unknown_tree(self):
        original = subject._copy_file; changed = False
        def copy(source, target, *args):
            nonlocal changed
            if source.name == 'NOTICE.txt':
                target.parent.rename(self.temp / 'original-copy-parent')
                target.parent.mkdir(); changed = True
            return original(source, target, *args)
        with patch.object(subject, '_copy_file', side_effect=copy): value = subject.prepare(self.ctx, self.guard)
        self.assertTrue(changed); self.assertFalse(value['passed']); self.assertTrue(value['cleanup_unknown'])
        self.assertFalse((self.ctx['root'] / 'runtime/platform-tools/NOTICE.txt').exists())
        self.assertTrue((self.temp / 'original-copy-parent').is_dir())

    def test_DTD_or_external_entity_metadata_is_never_loaded_or_copied(self):
        (self.ctx['sdk'] / 'platform-tools/package.xml').write_bytes(
            b'<!DOCTYPE repository [<!ENTITY x SYSTEM "https://invalid.test/private">]>'
            b'<repository><localPackage path="platform-tools"/></repository>')
        with patch.object(subject, '_copy_file') as copy: value = subject.prepare(self.ctx, self.guard)
        copy.assert_not_called(); self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_METADATA')
        self.assert_targets_removed()

    def test_bounded_file_count_refuses_instead_of_selecting_a_partial_package(self):
        with patch.object(subject, 'MAX_FILES', 2): value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_LIMIT'); self.assert_targets_removed()

    def test_source_alias_flag_refuses_without_following_or_copying(self):
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda path: True if path == self.adb else original(path)):
            value = subject.prepare(self.ctx, self.guard)
        self.assertFalse(value['passed']); self.assertEqual(value['guard_code'], 'SDK_LAYOUT_PATH'); self.assert_targets_removed()

    def test_ready_rejects_late_target_change_and_never_reconstructs_a_receipt(self):
        self.assertTrue(subject.prepare(self.ctx, self.guard)['passed']); before = self.record('result').read_bytes()
        path = self.ctx['root'] / 'runtime/platform-tools/NOTICE.txt'; path.write_bytes(b'changed copy')
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_CHANGED'): subject.ready(self.ctx, self.guard)
        self.assertEqual(self.record('result').read_bytes(), before)

    def test_ready_rejects_duplicate_extra_and_boolean_fields_in_original_records(self):
        self.assertTrue(subject.prepare(self.ctx, self.guard)['passed'])
        path = self.record('inventory'); original = path.read_bytes()
        value = json.loads(original); value['packages']['platform-tools']['files']['NOTICE.txt']['bytes'] = False
        changed = subject._encoded(value); path.write_bytes(changed)
        result_path = self.record('result'); original_result = result_path.read_bytes()
        result = json.loads(original_result); result['inventory_sha256'] = hashlib.sha256(changed).hexdigest()
        result_path.write_bytes(subject._encoded(result))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_RECORD'): subject.ready(self.ctx, self.guard)
        path.write_bytes(original); result = json.loads(original_result); result['failed_copy_removed'] = True
        result_path.write_bytes(subject._encoded(result))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_RECORD'): subject.ready(self.ctx, self.guard)
        path.write_bytes(original[:-2] + b',"files":8}\n')
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_RECORD'): subject.ready(self.ctx, self.guard)
        path.write_bytes(original); result = json.loads(original_result); result['extra'] = 'controlled private fixture'
        result_path.write_bytes(subject._encoded(result))
        with self.assertRaisesRegex(subject.kernel.ArchiveError, 'SDK_LAYOUT_RECORD'): subject.ready(self.ctx, self.guard)


if __name__ == '__main__': unittest.main()
