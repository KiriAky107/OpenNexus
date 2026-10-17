from copy import deepcopy
import json
import unittest

from android_desktop_handoff import HandoffError, context_bytes, new_recipient, open_handoff, seal, unique_pairs


class OwnedHandoff(unittest.TestCase):
    def setUp(self):
        self.context = {'run_id': 123456, 'tools_commit': 'a' * 40,
                        'android_commit': 'b' * 40, 'desktop_commit': 'c' * 40}
        self.payload = {'endpoint': 'https://owned-pair-origin.trycloudflare.com/',
                        'username': 'android-owned-a-' + 'd' * 12, 'password': 'OWNED_SECRET_' + 'e' * 44,
                        'vault_id': 'f' * 32, 'vault_name': 'Owned Android Sync ' + 'f' * 32}
        self.private, self.public = new_recipient()

    def test_original_fields_roundtrip_without_plaintext_in_public_envelope(self):
        original = deepcopy(self.payload)
        envelope = seal(self.public, self.payload, self.context)
        self.assertEqual(open_handoff(self.private, envelope, self.context), original)
        self.assertEqual(self.payload, original)
        serialized = json.dumps(envelope)
        for secret in original.values():
            self.assertNotIn(secret, serialized)
        second = seal(self.public, original, self.context)
        self.assertNotEqual(envelope['ciphertext'], second['ciphertext'])
        self.assertNotEqual(envelope['ephemeral_public_key'], second['ephemeral_public_key'])

    def test_wrong_recipient_and_run_or_compiled_source_are_rejected(self):
        envelope = seal(self.public, self.payload, self.context)
        other, _ = new_recipient()
        with self.assertRaises(HandoffError):
            open_handoff(other, envelope, self.context)
        for key in self.context:
            wrong = dict(self.context, **{key: 654321 if key == 'run_id' else '0' * 40})
            with self.subTest(key=key), self.assertRaises(HandoffError):
                open_handoff(self.private, envelope, wrong)

    def test_tampering_and_splicing_cannot_recover_credentials(self):
        envelope = seal(self.public, self.payload, self.context)
        replacement = seal(self.public, self.payload, self.context)
        for key in ('ephemeral_public_key', 'nonce', 'ciphertext'):
            changed = dict(envelope, **{key: replacement[key]})
            with self.subTest(key=key), self.assertRaisesRegex(HandoffError, '^HANDOFF_AUTHENTICATION_FAILED$'):
                open_handoff(self.private, changed, self.context)
        changed = deepcopy(envelope)
        changed['context']['run_id'] += 1
        with self.assertRaises(HandoffError):
            open_handoff(self.private, changed, self.context)

    def test_real_accounts_paths_tokens_and_non_owned_servers_are_refused(self):
        for key, value in [('endpoint', 'http://127.0.0.1:19001/'), ('endpoint', 'https://example.com/'),
                           ('username', 'user-account'), ('password', 'x'), ('vault_id', '../../data')]:
            with self.subTest(key=key, value=value), self.assertRaisesRegex(HandoffError, '^HANDOFF_PAYLOAD_INVALID$'):
                seal(self.public, dict(self.payload, **{key: value}), self.context)
        with self.assertRaises(HandoffError):
            seal(self.public, dict(self.payload, access_token='never-upload'), self.context)

    def test_malformed_or_oversized_envelopes_and_unknown_fields_are_refused(self):
        envelope = seal(self.public, self.payload, self.context)
        for key, value in [('nonce', '!'), ('ciphertext', 'A' * 9000), ('schema', True),
                           ('ephemeral_public_key', 'AAAA'), ('ciphertext', 'AA==')]:
            with self.subTest(key=key), self.assertRaises(HandoffError):
                open_handoff(self.private, dict(envelope, **{key: value}), self.context)
        with self.assertRaises(HandoffError):
            open_handoff(self.private, dict(envelope, raw_password='never'), self.context)
        with self.assertRaises(HandoffError):
            context_bytes(dict(self.context, run_id=True))

    def test_duplicate_decrypted_keys_cannot_select_a_different_identity(self):
        with self.assertRaisesRegex(HandoffError, '^HANDOFF_PAYLOAD_INVALID$'):
            json.loads('{"username":"first","username":"second"}', object_pairs_hook=unique_pairs)


if __name__ == '__main__':
    unittest.main()
