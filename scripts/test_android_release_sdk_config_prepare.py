"""Pure config preparation checks: no config copies, SDK, device or product execution."""
import hashlib
import json
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch

import android_release_sdk_config_prepare as subject
import android_release_sdk_ini as ini

CTX = dict(root=PurePosixPath('/owned/sdk-owned-123456-1'))
DATA = (b'image.sysdir.1=system-images/android-36/google_apis/x86_64/\n'
        b'abi.type=x86_64\ntag.id=google_apis\ndisk.dataPartition.size=800M\n' +
        b''.join(('Unknown' + str(i) + '=' + ('' if i == 1 else 'private-value-' + str(i)) + '\n').encode()
                 for i in range(133)))


class ConfigPreparation(unittest.TestCase):
    def test_only_known_capacity_changes_and_all_other_entries_including_empty_values_remain(self):
        output, result = subject.prepare(CTX, DATA)
        expected = DATA.replace(b'disk.dataPartition.size=800M', b'disk.dataPartition.size=6442450944')
        expected = b'\n'.join(line.replace(b'=', b' = ', 1) if line else line for line in expected.split(b'\n'))
        self.assertEqual(output, expected)
        delta = ini.fingerprint_delta(ini.fingerprints(CTX, DATA), ini.fingerprints(CTX, output))
        self.assertEqual((delta['before_keys'], delta['after_keys'], delta['unchanged_keys']), (137, 137, 136))
        self.assertEqual(delta['added_key_sha256'], []); self.assertEqual(delta['removed_key_sha256'], [])
        self.assertEqual(len(delta['changed']), 1); self.assertIn(b'Unknown1 = \n', output)
        self.assertEqual(result['before_partition_bytes'], 838860800)
        self.assertEqual(result['after_partition_bytes'], 6442450944)
        self.assertNotEqual(result['before_partition_bytes'], result['after_partition_bytes'])

    def test_other_capacities_or_missing_partition_are_refused_instead_of_normalized(self):
        for literal in [b'6G', b'6GB', b'6442450944', b'800m', b'800MB', b'0', b'-1', b'800Mprivate']:
            with self.subTest(literal=literal), self.assertRaises(subject.ConfigError):
                subject.prepare(CTX, DATA.replace(b'=800M', b'=' + literal))
        with self.assertRaises(subject.ConfigError): subject.prepare(CTX, DATA.replace(b'disk.dataPartition.size=', b'Other='))

    def test_duplicate_malformed_non_ascii_and_unknown_extra_or_missing_key_are_refused(self):
        cases = [DATA + b'Unknown0=duplicate\n', DATA + b'Extra=x\n', DATA.replace(b'Unknown0=private-value-0\n', b''),
                 DATA.replace(b'Unknown0=private-value-0', b'not-an-assignment'), DATA + b'\xff',
                 DATA.replace(b'private-value-0', b'bad\x00value')]
        for raw in cases:
            with self.subTest(raw_sha=hashlib.sha256(raw).hexdigest()), self.assertRaises(subject.ConfigError):
                subject.prepare(CTX, raw)

    def test_unrecognized_layout_or_image_identity_is_refused_without_heuristic_rewrite(self):
        cases = [DATA[:-1], DATA.replace(b'\n', b'\r\n'), DATA + b'\n', b'# comment\n' + DATA,
                 DATA.replace(b'Unknown0=', b'Unknown0 ='), DATA.replace(b'=800M', b'=800M '),
                 DATA.replace(b'tag.id=google_apis', b'tag.id=other'), DATA.replace(b'abi.type=x86_64', b'abi.type=arm64-v8a'),
                 DATA.replace(b'image.sysdir.1=system-images', b'image.sysdir.1=other-images')]
        for raw in cases:
            with self.subTest(raw_sha=hashlib.sha256(raw).hexdigest()), self.assertRaises(subject.ConfigError):
                subject.prepare(CTX, raw)

    def test_narrow_result_has_no_config_fields_or_private_values_and_does_not_claim_a_write(self):
        output, result = subject.prepare(CTX, DATA)
        self.assertEqual(result['before_raw_sha256'], hashlib.sha256(DATA).hexdigest())
        self.assertEqual(result['after_raw_sha256'], hashlib.sha256(output).hexdigest())
        self.assertEqual(result['keys'], 137); self.assertEqual(result['changed_keys'], 1)
        self.assertEqual(result['unchanged_keys'], 136)
        self.assertFalse(result['written']); self.assertFalse(result['runtime_verified'])
        self.assertNotIn('Unknown', json.dumps(result)); self.assertNotIn('private-value', json.dumps(result))

    def test_pure_preparation_never_opens_files_launches_processes_or_mutates_original(self):
        before = hashlib.sha256(DATA).hexdigest()
        with patch('builtins.open', side_effect=AssertionError('no file IO')), \
             patch('subprocess.run', side_effect=AssertionError('no process')):
            output, result = subject.prepare(CTX, DATA)
        self.assertEqual(hashlib.sha256(DATA).hexdigest(), before)
        self.assertIs(type(output), bytes); self.assertFalse(result['written'])


if __name__ == '__main__': unittest.main()
