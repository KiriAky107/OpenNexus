"""Pure handoff provenance checks; synthetic API metadata is not hosted evidence."""
import copy
import hashlib
import io
import json
import stat
import unittest
import zipfile

from android_desktop_exchange import artifact_name, exchange_bytes, read_exchange
from android_desktop_handoff import HandoffError, encode, new_recipient, open_handoff, seal


class Exchange(unittest.TestCase):
    def setUp(self):
        self.context = {'run_id': 1234, 'tools_commit': 'a' * 40, 'android_commit': 'b' * 40,
                        'desktop_commit': 'c' * 40}
        self.run = {'id': 1234, 'head_sha': 'a' * 40, 'head_branch': 'feat/android-sync-foreground',
                    'path': '.github/workflows/android.yml', 'event': 'workflow_dispatch', 'run_attempt': 1,
                    'repository': {'id': 17, 'full_name': 'KiriAky107/OpenNexus'}}
        self.jobs = [self.job('desktop-pair', 'Publish owned desktop receiving key', 61),
                     self.job('android-desktop-pair', 'Publish sealed owned fixture', 62)]
        self.private, self.public = new_recipient()

    @staticmethod
    def job(name, step, job_id):
        return {'name': name, 'id': job_id, 'run_id': 1234, 'steps': [{
            'name': step, 'status': 'completed', 'conclusion': 'success',
            'started_at': '2026-10-17T12:00:00Z', 'completed_at': '2026-10-17T12:00:10Z'}]}

    def archive(self, stage, value, *, entries=None):
        result = io.BytesIO()
        with zipfile.ZipFile(result, 'w') as out:
            if entries is None:
                out.writestr('exchange.json', value)
            else:
                for entry, body in entries:
                    out.writestr(entry, body)
        raw = result.getvalue()
        metadata = {'id': 99, 'name': artifact_name(self.context, stage), 'expired': False,
                    'size_in_bytes': len(raw), 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                    'created_at': '2026-10-17T12:00:05Z', 'workflow_run': {
                        'id': 1234, 'head_sha': 'a' * 40, 'head_branch': 'feat/android-sync-foreground',
                        'repository_id': 17, 'head_repository_id': 17}}
        return metadata, raw

    def read(self, stage, metadata, raw, *, run=None, jobs=None):
        return read_exchange(self.context, stage, run or self.run, jobs or self.jobs, metadata, raw)

    def test_only_same_original_run_and_named_upload_can_deliver_the_sealed_fixture(self):
        payload = {'endpoint': 'https://owned-test.trycloudflare.com/', 'username': 'android-owned-a-' + 'd' * 12,
                   'password': 'P' * 48, 'vault_id': 'e' * 32, 'vault_name': 'Owned Android Sync ' + 'e' * 32}
        sealed = seal(self.public, payload, self.context)
        body = exchange_bytes(self.context, 'sealed', sealed)
        self.assertNotIn(payload['password'].encode(), body)
        metadata, raw = self.archive('sealed', body)
        received, proof = self.read('sealed', metadata, raw)
        self.assertEqual(open_handoff(self.private, received, self.context), payload)
        self.assertEqual(proof['original_job_id'], 62)
        self.assertFalse(proof['artifact_has_producer_job_id'])

    def test_same_sha_artifact_from_another_run_fork_or_rerun_is_rejected(self):
        metadata, raw = self.archive('recipient', exchange_bytes(self.context, 'recipient', {'public_key': encode(self.public)}))
        for field, value in [('id', 1235), ('run_attempt', 2), ('event', 'push')]:
            with self.subTest(field=field), self.assertRaises(HandoffError):
                run = copy.deepcopy(self.run); run[field] = value
                self.read('recipient', metadata, raw, run=run)
        changed = copy.deepcopy(metadata); changed['workflow_run']['head_repository_id'] = 18
        with self.assertRaises(HandoffError): self.read('recipient', changed, raw)

    def test_missing_failed_or_unrelated_original_upload_step_is_not_provenance(self):
        metadata, raw = self.archive('recipient', exchange_bytes(self.context, 'recipient', {'public_key': encode(self.public)}))
        for field, value in [('name', 'Some other upload'), ('status', 'in_progress'), ('conclusion', 'failure')]:
            jobs = copy.deepcopy(self.jobs); jobs[0]['steps'][0][field] = value
            with self.subTest(field=field), self.assertRaises(HandoffError): self.read('recipient', metadata, raw, jobs=jobs)
        metadata['created_at'] = '2026-10-17T12:01:00Z'
        with self.assertRaises(HandoffError): self.read('recipient', metadata, raw)

    def test_archive_digest_and_single_bounded_regular_filename_are_required(self):
        body = exchange_bytes(self.context, 'recipient', {'public_key': encode(self.public)})
        metadata, raw = self.archive('recipient', body)
        with self.assertRaises(HandoffError): self.read('recipient', metadata, raw + b'changed')
        link = zipfile.ZipInfo('exchange.json'); link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entries in [[('../exchange.json', body)], [('exchange.json', body), ('extra.json', b'{}')],
                        [(link, body)], [('exchange.json', b' ' * (16 * 1024 + 1))]]:
            with self.subTest(entries=len(entries)), self.assertRaises(HandoffError):
                metadata, raw = self.archive('recipient', b'', entries=entries)
                self.read('recipient', metadata, raw)

    def test_plaintext_credentials_extra_keys_and_duplicate_envelope_keys_are_rejected(self):
        with self.assertRaises(HandoffError):
            exchange_bytes(self.context, 'recipient', {'public_key': encode(self.public), 'password': 'PRIVATE'})
        with self.assertRaises(HandoffError):
            exchange_bytes(self.context, 'sealed', {'password': 'PRIVATE'})
        body = exchange_bytes(self.context, 'recipient', {'public_key': encode(self.public)})
        duplicate = body[:-1] + b',"stage":"recipient"}'
        metadata, raw = self.archive('recipient', duplicate)
        with self.assertRaises(HandoffError): self.read('recipient', metadata, raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
