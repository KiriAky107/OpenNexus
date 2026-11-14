"""RAM-only affected guards. Native Linux prepare is verified by its scoped CI job."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import unittest
from unittest.mock import patch
import zipfile

import android_release_platform_tools as subject
import android_release_preconfig_sdk as sdk_source


class PlatformToolsGuards(unittest.TestCase):
    def test_script_entry_uses_same_module_as_sdk_owner_type_check(self):
        code = compile(Path(subject.__file__).read_bytes(), subject.__file__, 'exec')

        class ReachedCanonicalOwner(Exception):
            pass

        def deny(*args, **kwargs):
            raise AssertionError('unexpected IO before canonical controller')

        with patch.object(subject, 'controlled_verify', side_effect=ReachedCanonicalOwner) as run, \
                patch.object(subject.sys, 'argv', [subject.__file__, 'verify']), \
                patch.object(subject.os, 'open', deny), patch.object(Path, 'open', deny), \
                patch.object(Path, 'resolve', deny), patch.object(Path, 'mkdir', deny), \
                patch.object(subject.time, 'monotonic_ns', deny), \
                patch.object(subject.urllib.request, 'build_opener', deny):
            with self.assertRaises(ReachedCanonicalOwner):
                exec(code, {'__name__': '__main__'})
        run.assert_called_once_with()

    def source(self):
        raw = (b'!strcmp(argv[0], "--version") || !strcmp(argv[0], "version")\n'
               b'fprintf(stdout, "%s", adb_version().c_str());\n        return 0;')
        raw = raw.ljust(84919, b' ')
        tag = b")]}'\n" + json.dumps({'commit': subject.SOURCE_REVISION, 'tree': subject.SOURCE_TREE}).encode()
        return raw, tag

    def bundle(self, change=None):
        adb = b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00\x00' + subject.BUILD.encode() + b'\x00'
        files = {'adb': adb, 'source.properties': b'Pkg.UserSrc=false\nPkg.Revision=35.0.2\n'}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo('platform-tools/' + name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o644 if name == 'source.properties' else 0o755)) << 16
                if change:
                    change(info)
                archive.writestr(info, data)
        raw = output.getvalue()
        return raw, files

    def accept(self, raw, files):
        return patch.multiple(subject, ARCHIVE_BYTES=len(raw),
            ARCHIVE_SHA256=hashlib.sha256(raw).hexdigest(),
            FILES={k: len(v) for k, v in files.items()},
            ADB_BYTES=len(files['adb']), ADB_SHA256=hashlib.sha256(files['adb']).hexdigest())

    def test_constructor_and_unbound_sdk_are_zero_io(self):
        def deny(*args, **kwargs):
            raise AssertionError('unexpected IO')
        with patch.object(subject, 'fetch', deny), patch.object(subject.os, 'open', deny), \
                patch.object(subject.time, 'monotonic_ns', deny):
            prepared = subject.PreparedPlatformTools()
            sdk = sdk_source.OriginalPreconfigSdk()
        self.assertEqual(prepared._state, 'NEW')
        self.assertIsNone(sdk._platform_tools_source)

    def test_actual_unsupported_host_rejects_before_environment_or_io(self):
        def deny(*args, **kwargs):
            raise AssertionError('unexpected IO')
        with patch.object(subject.sys, 'platform', 'win32'), patch.object(subject.os, 'getenv', deny), \
                patch.object(subject.os, 'open', deny), patch.object(subject, 'fetch', deny):
            with self.assertRaisesRegex(RuntimeError, subject.ERROR):
                subject.PreparedPlatformTools().prepare()
        self.assertFalse(subject._OWNERS)

    def test_release_tag_and_exact_commandline_source(self):
        raw, tag = self.source()
        with patch.object(subject, 'SOURCE_COMMANDLINE_SHA256', hashlib.sha256(raw).hexdigest()):
            result = subject.verify_source(tag, base64.b64encode(raw))
        self.assertEqual(result['revision'], subject.SOURCE_REVISION)
        self.assertFalse(result['binary_reproducible_build_or_full_transitive_manifest_verified'])

    def test_source_revision_and_duplicate_fields_fail_closed(self):
        raw, tag = self.source()
        bad = tag.replace(subject.SOURCE_REVISION.encode(), b'0' * 40)
        duplicate = b")]}'\n{" + b'"commit":"' + subject.SOURCE_REVISION.encode() + b'","commit":"' + subject.SOURCE_REVISION.encode() + b'"}'
        with patch.object(subject, 'SOURCE_COMMANDLINE_SHA256', hashlib.sha256(raw).hexdigest()):
            for value in (bad, duplicate):
                with self.assertRaises(RuntimeError):
                    subject.verify_source(value, base64.b64encode(raw))

    def test_commandline_source_bytes_not_only_label(self):
        raw, tag = self.source()
        with self.assertRaises(RuntimeError):
            subject.verify_source(tag, base64.b64encode(raw))

    def test_verified_same_archive_supplies_bounded_files(self):
        raw, files = self.bundle()
        with self.accept(raw, files):
            self.assertEqual(subject.verify_archive(raw), files)

    def test_archive_digest_before_zip_or_file_use(self):
        raw, files = self.bundle()
        with self.accept(raw, files), patch.object(subject.zipfile, 'ZipFile', side_effect=AssertionError('parsed bad bytes')):
            with self.assertRaises(RuntimeError):
                subject.verify_archive(raw[:-1] + bytes([raw[-1] ^ 1]))

    def test_archive_paths_and_link_modes_are_rejected(self):
        for change in (lambda i: setattr(i, 'filename', '../' + i.filename.split('/')[-1]),
                       lambda i: setattr(i, 'external_attr', (stat.S_IFLNK | 0o777) << 16)):
            raw, files = self.bundle(change)
            with self.accept(raw, files):
                with self.assertRaises(RuntimeError):
                    subject.verify_archive(raw)

    def test_unprepared_and_copied_prepare_cannot_bind(self):
        prepared = subject.PreparedPlatformTools()
        for value in (prepared, copy.copy(prepared), object.__new__(subject.PreparedPlatformTools)):
            with self.assertRaises(RuntimeError):
                value.bind_sdk(sdk_source.OriginalPreconfigSdk())
        self.assertFalse(subject._OWNERS)

    def test_no_redirect_fallback(self):
        with self.assertRaises(RuntimeError):
            subject._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com/')


if __name__ == '__main__':
    unittest.main()
