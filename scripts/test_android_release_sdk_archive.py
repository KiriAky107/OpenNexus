import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import android_release_sdk_archive as subject

TEMPORARIES = []


class Response(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}
    def geturl(self): return subject.FIXED['url']


class FixedSDKArchive(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='onx-sdk-archive-check-')
        TEMPORARIES.append(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / '.build/android-release-runtime').mkdir(parents=True)
        env = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_OS='Linux',
                   RUNTIME_SCENE='sync-timeout', GITHUB_RUN_ID='123456', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_SHA='a' * 40, GITHUB_WORKSPACE=str(self.workspace))
        self.addCleanup(patch.stopall)
        patch.dict(subject.os.environ, env).start()
        patch.object(subject.sys, 'platform', 'linux').start()
        self.calls = []

    def bundle(self, *, first='emulator/emulator', mode=stat.S_IFREG | 0o755,
               version='37.2.12', header=None, stored=False):
        elf = header if header is not None else b'\x7fELF\x02\x01\x01' + bytes(11) + b'\x3e\x00'
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            info = zipfile.ZipInfo(first); info.create_system = 3; info.external_attr = mode << 16
            # Keep malicious raw names intact even when Windows ZipInfo normalizes them.
            info.filename = info.orig_filename = first
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            archive.writestr(info, elf)
            archive.writestr('emulator/source.properties', 'Pkg.Revision=' + version + '\n')
        data = output.getvalue()
        spec = self.spec(data)
        patch.object(subject, 'FIXED', spec).start()
        return data

    def spec(self, data):
        end = data.rfind(b'PK\x05\x06')
        values = struct.unpack_from('<4s4H2LH', data, end)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            expanded = sum(e.file_size for e in archive.infolist())
        return dict(subject.FIXED, bytes=len(data), sha1=hashlib.sha1(data).hexdigest(),
                    directory_sha256=hashlib.sha256(data[values[6]:values[6] + values[5]]).hexdigest(),
                    entries=values[4], expanded_bytes=expanded)

    def opener(self, data, mutate=None):
        def open_once(request, **kwargs):
            self.calls.append(request)
            self.assertEqual(request.full_url, subject.FIXED['url'])
            self.assertEqual(request.get_method(), 'GET')
            self.assertNotIn('Authorization', dict(request.header_items()))
            self.assertNotIn('Range', dict(request.header_items()))
            self.assertEqual(kwargs, {'timeout': 45})
            response = Response(data)
            if mutate: mutate(response)
            return response
        return open_once

    def root(self):
        return self.workspace / '.build/android-release-runtime/sdk-owned-123456-1'

    def test_real_download_crc_extract_owner_record_and_archive_removal(self):
        data = self.bundle()
        result = subject.prepare_owned(opener=self.opener(data))
        self.assertTrue(result['passed']); self.assertEqual(len(self.calls), 1)
        self.assertTrue(result['verification']['CRC_all_entries_verified'])
        self.assertEqual(result['verification']['archive_sha256'], hashlib.sha256(data).hexdigest())
        self.assertFalse((self.root() / 'emulator.zip').exists())
        self.assertEqual((self.root() / 'runtime/emulator/source.properties').read_bytes(), b'Pkg.Revision=37.2.12\n')
        self.assertEqual(json.loads((self.root() / 'owner.json').read_bytes())['GITHUB_RUN_ID'], '123456')
        self.assertFalse(result['Android_runtime_APK_or_timeout_verified'])

    def test_non_200_redirect_partial_or_encoded_response_never_extracts(self):
        data = self.bundle()
        cases = [lambda r: setattr(r, 'status', 206), lambda r: setattr(r, 'status', 'private value'),
                 lambda r: setattr(r, 'geturl', lambda: 'https://example.invalid/private'),
                 lambda r: r.headers.update({'Content-Length': 'private value'}),
                 lambda r: r.headers.update({'Content-Encoding': 'gzip'}),
                 lambda r: r.headers.update({'Content-Range': 'bytes 0-19/999'})]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                result = subject.prepare_owned(opener=self.opener(data, mutate))
                self.assertFalse(result['passed']); self.assertTrue(result['failed_staging_removed'])
                self.assertFalse(self.root().exists()); self.assertNotIn('private value', json.dumps(result))
        with self.assertRaisesRegex(subject.ArchiveError, 'SDK_REDIRECT_REFUSED'):
            subject.NoRedirect().redirect_request(None, None, 302, 'private value', {}, 'https://example.invalid')

    def test_truncated_overlong_unknown_and_late_reads_are_not_retried(self):
        data = self.bundle()
        for body in (data[:-4], data + b'extra'):
            result = subject.prepare_owned(opener=self.opener(body,
                lambda r: r.headers.update({'Content-Length': str(len(data))})))
            self.assertFalse(result['passed']); self.assertFalse(self.root().exists())
            self.assertFalse(result['failed_download_file']['accepted_as_complete'])
        before = len(self.calls)
        def unknown(request, **kwargs):
            self.calls.append(request); raise subject.ArchiveError('private error body')
        result = subject.prepare_owned(opener=unknown)
        self.assertEqual(len(self.calls), before + 1)
        self.assertNotIn('private error body', json.dumps(result)); self.assertFalse(self.root().exists())
        for clock in ([0, 0, 901], [1, 1, 0]):
            with patch.object(subject.time, 'monotonic', side_effect=clock):
                result = subject.prepare_owned(opener=self.opener(data))
            self.assertEqual(result['guard_code'], 'SDK_DOWNLOAD_DEADLINE')
            self.assertFalse(self.root().exists())

    def test_complete_length_wrong_checksum_and_directory_pin_are_rejected(self):
        data = self.bundle()
        for change, code in [({'sha1': '0' * 40}, 'SDK_ARCHIVE_CHECKSUM'),
                             ({'directory_sha256': '0' * 64}, 'SDK_ARCHIVE_DIRECTORY_HASH')]:
            with patch.dict(subject.FIXED, change):
                result = subject.prepare_owned(opener=self.opener(data))
            self.assertEqual(result['guard_code'], code); self.assertFalse(self.root().exists())

    def test_actual_corrupt_payload_fails_CRC_before_runtime_extraction(self):
        data = self.bundle(stored=True)
        filename_length, extra_length = struct.unpack_from('<2H', data, 26)
        offset = 30 + filename_length + extra_length
        broken = data[:offset] + bytes([data[offset] ^ 1]) + data[offset + 1:]
        with patch.object(subject, 'FIXED', self.spec(broken)):
            result = subject.prepare_owned(opener=self.opener(broken))
        self.assertFalse(result['passed']); self.assertEqual(result['guard_code'], 'SDK_ZIP_CRC')
        self.assertFalse(self.root().exists())

    def test_real_ZIP_paths_symlink_modes_and_special_bits_are_rejected(self):
        for name, mode in [('../emulator', stat.S_IFREG | 0o755), ('/emulator/x', stat.S_IFREG | 0o755),
                           ('emulator/../x', stat.S_IFREG | 0o755), ('emulator\\x', stat.S_IFREG | 0o755),
                           ('emulator/C:x', stat.S_IFREG | 0o755), ('emulator/./x', stat.S_IFREG | 0o755),
                           ('emulator/emulator\x00private', stat.S_IFREG | 0o755),
                           ('emulator/line\nname', stat.S_IFREG | 0o755),
                           ('emulator/emulator', stat.S_IFLNK | 0o777),
                           ('emulator/emulator', stat.S_IFREG | 0o4755)]:
            with self.subTest(name=name, mode=mode):
                data = self.bundle(first=name, mode=mode)
                result = subject.prepare_owned(opener=self.opener(data))
                self.assertIn(result['guard_code'], ('SDK_ZIP_PATH', 'SDK_ZIP_LINK_OR_MODE'))
                self.assertFalse(self.root().exists())

    def test_archive_count_total_per_file_and_duplicate_names_are_closed(self):
        data = self.bundle()
        for change, code in [({'entries': 3}, 'SDK_ZIP_COUNT'), ({'expanded_bytes': 99}, 'SDK_ZIP_EXPANDED_SIZE')]:
            with patch.dict(subject.FIXED, change): result = subject.prepare_owned(opener=self.opener(data))
            self.assertEqual(result['guard_code'], code); self.assertFalse(self.root().exists())
        with patch.object(subject, 'MAX_FILE', 4): result = subject.prepare_owned(opener=self.opener(data))
        self.assertEqual(result['guard_code'], 'SDK_ZIP_ENTRY'); self.assertFalse(self.root().exists())
        with patch.object(subject, 'MAX_EXPANDED', 4): result = subject.prepare_owned(opener=self.opener(data))
        self.assertEqual(result['guard_code'], 'SDK_ZIP_EXPANDED_LIMIT'); self.assertFalse(self.root().exists())
        with patch.object(subject, 'MAX_ENTRIES', 1): result = subject.prepare_owned(opener=self.opener(data))
        self.assertEqual(result['guard_code'], 'SDK_ZIP_DIRECTORY'); self.assertFalse(self.root().exists())
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('emulator/A', b'a'); archive.writestr('emulator/a', b'b')
        duplicate = output.getvalue()
        with patch.object(subject, 'FIXED', self.spec(duplicate)):
            result = subject.prepare_owned(opener=self.opener(duplicate))
        self.assertEqual(result['guard_code'], 'SDK_ZIP_DUPLICATE'); self.assertFalse(self.root().exists())

    def test_archive_change_after_CRC_with_restored_stat_is_still_rejected(self):
        data = self.bundle()
        original = subject.zipfile.ZipFile
        archive_path = self.root() / 'emulator.zip'
        class ChangedAfterCRC(original):
            def testzip(bundle):
                result = super().testzip()
                previous = archive_path.stat()
                changed = bytearray(archive_path.read_bytes())
                changed[10] ^= 1  # Local-header time, outside the entry payload CRC.
                archive_path.write_bytes(changed)
                os.utime(archive_path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                return result
        with patch.object(subject.zipfile, 'ZipFile', ChangedAfterCRC):
            result = subject.prepare_owned(opener=self.opener(data))
        self.assertEqual(result['guard_code'], 'SDK_ARCHIVE_CHANGED')
        self.assertFalse(self.root().exists())

    def test_wrong_launcher_or_revision_cannot_leave_an_accepted_runtime(self):
        for kwargs, code in [({'header': bytes(20)}, 'SDK_LAUNCHER_ABI'),
                             ({'version': '37.2.11'}, 'SDK_INSTALLED_REVISION')]:
            data = self.bundle(**kwargs)
            result = subject.prepare_owned(opener=self.opener(data))
            self.assertEqual(result['guard_code'], code); self.assertFalse(self.root().exists())

    def test_original_namespace_and_host_or_owner_alias_are_refused_before_network(self):
        data = self.bundle(); self.root().mkdir()
        original = self.root() / 'keep'; original.write_bytes(b'original')
        with self.assertRaisesRegex(subject.ArchiveError, 'SDK_ORIGINAL_NAMESPACE_EXISTS'):
            subject.prepare_owned(opener=self.opener(data))
        self.assertEqual(original.read_bytes(), b'original'); self.assertFalse(self.calls)
        original.unlink(); self.root().rmdir()
        for env, code in [({'GITHUB_ACTIONS': 'false'}, 'SDK_HOSTED_TIMEOUT_ONLY'),
                          ({'RUNTIME_SCENE': 'sync-stop'}, 'SDK_HOSTED_TIMEOUT_ONLY'),
                          ({'GITHUB_RUN_ID': '../1'}, 'SDK_OWNER_INVALID'),
                          ({'GITHUB_SHA': 'main'}, 'SDK_OWNER_INVALID'),
                          ({'GITHUB_WORKSPACE': str(self.workspace / '..')}, 'SDK_WORKSPACE_ALIAS')]:
            with patch.dict(subject.os.environ, env), self.assertRaisesRegex(subject.ArchiveError, code):
                subject.prepare_owned(opener=self.opener(data))
        self.assertFalse(self.calls)

    def test_failed_cleanup_refuses_a_changed_owner_without_retrying(self):
        data = self.bundle()
        def changed(request, **kwargs):
            self.calls.append(request)
            (self.root() / 'owner.json').write_bytes(b'changed owner')
            raise OSError('private failure')
        result = subject.prepare_owned(opener=changed)
        self.assertEqual(len(self.calls), 1); self.assertFalse(result['failed_staging_removed'])
        self.assertTrue(result['cleanup_unknown_no_retry'])
        self.assertEqual((self.root() / 'owner.json').read_bytes(), b'changed owner')
        self.assertNotIn('private failure', json.dumps(result))


if __name__ == '__main__': unittest.main()
