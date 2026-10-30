"""In-memory SDK-format fixtures; no SDK, filesystem copies or vendor execution."""
import json
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch

import android_release_sdk_ini as subject

CTX = dict(root=PurePosixPath('/owned/sdk-owned-123456-1'))
ORIGINAL = (b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\n'
            b'abi.type=x86_64\ntag.id=google_apis\nOther.private=never-export-this-fixture\nEmpty=\n')


class IniDiagnosticProjection(unittest.TestCase):
    def test_vendor_documented_spacing_comments_CRLF_and_order_preserve_full_map_hash(self):
        changed = (b'; controlled comment\r\n\t Other.private \t= never-export-this-fixture \t\r\n'
                   b'\t tag.id = google_apis\r\n abi.type = x86_64 \t\r\n'
                   b'image.sysdir.1 = system-images/android-36/google_apis/x86_64/\r\nEmpty = \r\n')
        first, second = subject.project(CTX, ORIGINAL), subject.project(CTX, changed)
        self.assertTrue(first['available']); self.assertTrue(second['available'])
        self.assertEqual(first['canonical_sha256'], second['canonical_sha256'])
        self.assertNotEqual(first['source_sha256'], second['source_sha256']); self.assertEqual(first['key_count'], 5)
        self.assertTrue(all(second[k] for k in ('image_literal_matches', 'abi_x86_64', 'tag_google_apis')))
        self.assertFalse(second['runtime_verified']); self.assertNotIn('never-export', json.dumps(second))
        self.assertNotIn('Other.private', json.dumps(second)); self.assertNotIn('/owned', json.dumps(second))

    def test_every_unknown_key_value_and_empty_entry_participates_without_whitelisting_it_away(self):
        original = subject.project(CTX, ORIGINAL)
        changes = [ORIGINAL + b'New=1\n', ORIGINAL.replace(b'never-export-this-fixture', b'other-private-fixture'),
                   ORIGINAL.replace(b'Empty=\n', b''), ORIGINAL.replace(b'Empty=\n', b'Empty=x\n'),
                   ORIGINAL.replace(b'google_apis', b'other_tag'), ORIGINAL.replace(b'x86_64', b'arm64-v8a')]
        for raw in changes:
            result = subject.project(CTX, raw)
            self.assertTrue(result['available']); self.assertNotEqual(result['canonical_sha256'], original['canonical_sha256'])
            self.assertNotIn('private-fixture', json.dumps(result))

    def test_duplicates_bad_lines_non_ASCII_and_controls_become_unavailable_instead_of_dropped(self):
        cases = [(ORIGINAL + b'abi.type=x86_64\n', 'duplicate'), (ORIGINAL + b'bad line\n', 'assignment'),
                 (ORIGINAL + b'9invalid=value\n', 'key'), (ORIGINAL + b'bad key=value\n', 'key'),
                 (ORIGINAL + b'x=first\rsecond\n', 'value_remainder'), (ORIGINAL + b'\xff', 'non_ascii'),
                 (ORIGINAL + b'x=\x00\n', 'control_byte'), (ORIGINAL + b'x=\x0b\n', 'control_byte'),
                 (b'# only comment\n', 'empty')]
        for raw, reason in cases:
            result = subject.project(CTX, raw)
            self.assertFalse(result['available']); self.assertEqual(result['unavailable_reason'], reason)
            self.assertIsNone(result['canonical_sha256']); self.assertIsNone(result['key_count'])
            self.assertIsNone(result['abi_x86_64']); self.assertNotIn('bad key', json.dumps(result))

    def test_bounds_reject_whole_input_and_never_select_a_partial_map(self):
        for raw, reason in [(None, 'input_type'), (b'x=' + b'a' * subject.MAX_BYTES, 'input_limit'),
                            (b'\n' * subject.MAX_LINES, 'line_limit'),
                            (b''.join(('Key'+str(i)+'=x\n').encode() for i in range(subject.MAX_KEYS + 1)), 'key_limit')]:
            result = subject.project(CTX, raw)
            self.assertFalse(result['available']); self.assertEqual(result['unavailable_reason'], reason)
            self.assertIsNone(result['canonical_sha256'])

    def test_path_values_and_escapes_are_never_resolved_decoded_or_normalized(self):
        expected = subject.project(CTX, ORIGINAL)
        for path in [b'../unowned', b'/other/image', b'system-images//android-36/google_apis/x86_64/',
                     b'system-images\\android-36\\google_apis\\x86_64\\']:
            result = subject.project(CTX, ORIGINAL.replace(b'system-images/android-36/google_apis/x86_64/', path))
            self.assertTrue(result['available']); self.assertFalse(result['image_literal_matches'])
            self.assertNotEqual(result['canonical_sha256'], expected['canonical_sha256'])
        escaped = subject.project(CTX, b'Key=literal\\nnot-a-line\nOther=a=b\n')
        self.assertTrue(escaped['available']); self.assertEqual(escaped['key_count'], 2)

    def test_projection_has_no_file_process_network_or_admission_side_effect(self):
        with patch('builtins.open', side_effect=AssertionError('no file IO')):
            result = subject.project(CTX, ORIGINAL)
        self.assertEqual(set(result), {'kind', 'source_bytes', 'source_sha256', 'available', 'canonical_sha256',
            'key_count', 'image_literal_matches', 'abi_x86_64', 'tag_google_apis', 'unavailable_reason', 'runtime_verified'})
        self.assertEqual(result['kind'], subject.KIND); self.assertFalse(result['runtime_verified'])


if __name__ == '__main__': unittest.main()
