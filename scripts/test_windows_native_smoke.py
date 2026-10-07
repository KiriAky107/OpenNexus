"""Native QA must never replace an existing application's storage pointer."""
import os
from pathlib import Path
import tempfile
import unittest

from windows_native_smoke import FreshProfile, preserve_owned_profile


class FreshProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='opennexus-smoke-profile-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.appdata = self.root / 'appdata'
        self.data = self.root / 'private-data'
        self.appdata.mkdir()
        self.data.mkdir()
        self.identifier = 'cc.opennexus.qa-test'

    def claim(self, identifier=None):
        return FreshProfile(self.appdata, identifier or self.identifier, self.data)

    def test_existing_pointer_and_profile_are_untouched(self):
        profile = self.appdata / self.identifier
        profile.mkdir()
        pointer = profile / 'storage-location.json'
        original = b'{"data_root":"user-data","unknown":"preserve"}\r\n'
        pointer.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'left untouched'):
            self.claim()
        self.assertEqual(pointer.read_bytes(), original)
        self.assertEqual(list(profile.iterdir()), [pointer])

    def test_existing_profile_without_pointer_is_also_rejected(self):
        profile = self.appdata / self.identifier
        profile.mkdir()
        (profile / 'host-state.sqlite3').write_bytes(b'user-state')
        with self.assertRaisesRegex(RuntimeError, 'left untouched'):
            self.claim()
        self.assertEqual((profile / 'host-state.sqlite3').read_bytes(), b'user-state')
        self.assertFalse((profile / 'storage-location.json').exists())

    def test_fresh_claim_removes_only_its_own_pointer(self):
        profile = self.claim()
        self.assertEqual(profile.pointer.read_bytes(), profile.temporary)
        evidence = profile.path / 'host-state.sqlite3'
        evidence.write_bytes(b'test-state')
        profile.remove_pointer()
        profile.remove_pointer()
        self.assertFalse(profile.pointer.exists())
        self.assertEqual(evidence.read_bytes(), b'test-state')
        self.assertEqual(profile.marker.read_bytes(), profile.owner)

    def test_second_claim_cannot_reuse_an_owned_directory(self):
        first = self.claim()
        with self.assertRaisesRegex(RuntimeError, 'left untouched'):
            self.claim()
        self.assertEqual(first.pointer.read_bytes(), first.temporary)
        self.assertEqual(first.marker.read_bytes(), first.owner)

    def test_concurrent_pointer_change_is_preserved(self):
        profile = self.claim()
        profile.pointer.write_bytes(b'concurrent change')
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            profile.remove_pointer()
        self.assertEqual(profile.pointer.read_bytes(), b'concurrent change')

    def test_changed_owner_cannot_remove_pointer(self):
        profile = self.claim()
        profile.marker.write_bytes(b'other owner')
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            profile.remove_pointer()
        self.assertEqual(profile.pointer.read_bytes(), profile.temporary)

    def test_identifier_cannot_escape_profile_root(self):
        for identifier in ('../user-data', 'cc.test/other', 'C:\\user-data',
                'cc..test', 'cc.test.', 'cc.test.con', 'cc.test.lpt1', 'cc.test.x:stream'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.claim(identifier)
        self.assertEqual(list(self.appdata.iterdir()), [])

    def test_linked_pointer_is_preserved(self):
        profile = self.claim()
        other = self.root / 'other-pointer'
        os.link(profile.pointer, other)
        with self.assertRaisesRegex(RuntimeError, 'hard link'):
            profile.remove_pointer()
        self.assertEqual(other.read_bytes(), profile.temporary)
        self.assertTrue(profile.pointer.exists())

    def test_archive_cannot_move_an_owned_profile_outside_its_parent(self):
        profile = self.claim()
        evidence = profile.path/'evidence.txt'
        evidence.write_bytes(b'preserve diagnostics')
        with self.assertRaisesRegex(RuntimeError, 'identity or path'):
            preserve_owned_profile(profile, self.root/(self.identifier+'.native-smoke-escape'))
        self.assertEqual(evidence.read_bytes(), b'preserve diagnostics')
        self.assertTrue(profile.path.is_dir())

    def test_archive_does_not_replace_a_preexisting_destination(self):
        profile = self.claim()
        target = self.appdata/(self.identifier+'.native-smoke-existing')
        target.mkdir()
        (target/'user-state').write_bytes(b'keep')
        with self.assertRaisesRegex(RuntimeError, 'identity or path'):
            preserve_owned_profile(profile, target)
        self.assertEqual((target/'user-state').read_bytes(), b'keep')
        self.assertEqual(profile.marker.read_bytes(), profile.owner)

    def test_archive_rejects_a_changed_marker_after_pointer_removal(self):
        profile = self.claim()
        profile.remove_pointer()
        profile.marker.write_bytes(b'other owner')
        with self.assertRaisesRegex(RuntimeError, 'identity or path'):
            preserve_owned_profile(profile, self.appdata/(self.identifier+'.native-smoke-changed'))
        self.assertEqual(profile.marker.read_bytes(), b'other owner')
        self.assertTrue(profile.path.is_dir())

    @unittest.skipUnless(os.name == 'nt', 'Uses the native Windows directory move')
    def test_archive_preserves_owned_directory_identity_and_diagnostics(self):
        profile = self.claim()
        (profile.path/'evidence.txt').write_bytes(b'owned diagnostics\r\n')
        target = self.appdata/(self.identifier+'.native-smoke-completed')
        result = preserve_owned_profile(profile, target)
        self.assertTrue(result['preserved'])
        self.assertFalse(profile.path.exists())
        self.assertEqual(FreshProfile.identity(target, directory=True), profile.profile_identity)
        self.assertEqual((target/'evidence.txt').read_bytes(), b'owned diagnostics\r\n')
        self.assertEqual((target/profile.marker.name).read_bytes(), profile.owner)
        self.assertFalse((target/'storage-location.json').exists())


if __name__ == '__main__':
    unittest.main()
