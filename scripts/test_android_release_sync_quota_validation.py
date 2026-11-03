"""Synthetic metadata only: no service, Android, SQL, originals or runtime."""
from copy import deepcopy
import hashlib
import json
import unittest

import android_release_sync_quota_validation as quota


def fixture():
    def uuid(n):
        return '00000000-0000-0000-0000-' + format(n, '012x')
    scope = dict(schema=1, owner_run='12345-1', avd='opennexus-ci-12345-1', boot_id=uuid(1),
                 api=36, uid=10216, window=41, product=quota.PRODUCT, sync=quota.SYNC,
                 parent_id=uuid(2), input_id=uuid(3), remote_vault='1'*32, binding_id=uuid(4),
                 android_device='2'*32, setup_device='3'*32, login_operation_id=uuid(5),
                 setup_operation_id=uuid(6), save_operation_id=uuid(7), association_operation_id=uuid(8), upload_operation_id=uuid(9))
    draft = dict(id=uuid(10), file_id=uuid(11), path=quota.NOTE, base_hash=quota.HASH,
                 generation=2, saved_generation=2, content=quota.BODY.decode())
    save = dict(operation_id=uuid(7), draft_id=uuid(10), generation=2, file_id=None, path=quota.NOTE,
                expected='', content=quota.BODY.decode(), state='committed', error=None)
    file = dict(id=uuid(11), path=quota.NOTE, hash=quota.HASH, revision=1, deleted=0)
    association = dict(operation_id=uuid(8), auth_operation_id=uuid(5), remote_vault='1'*32,
                       state='bound', attempt_revision=1, request_hash='4'*64, preview_fingerprint='5'*64,
                       original_request_bytes_sha256='6'*64, original_binding_bytes_sha256='7'*64)
    archived = dict(operation_id=uuid(7), file_id=uuid(11), revision=1, path=quota.NOTE,
                    hash=quota.HASH, operation='put', state='archived')
    before = dict(schema=1, scope=deepcopy(scope), stage='associated', draft=draft, save=save, file=file,
                  binding=dict(id=uuid(4), remote_vault='1'*32, state='active', cursor=0), association=association,
                  outbox=[archived, dict(archived, operation_id=uuid(9), state='pending')],
                  jobs=[], attempts=[], issuers=[], commits=[], heads=[], inbox=[], conflicts=[])
    after = deepcopy(before)
    after.update(stage='quota-failed', jobs=[dict(operation_id=uuid(9), file_id=uuid(11), path=quota.NOTE,
        hash=quota.HASH, size=56, state='pending', remote_revision=None, error=None)],
        attempts=[dict(operation_id=uuid(9), attempts=1, outcome='failed', error='QUOTA_EXCEEDED')],
        issuers=[dict(operation_id=uuid(9), device_id='2'*32)],
        commits=[dict(binding=uuid(4), operation_id=uuid(9), file_id=uuid(11), operation='put', base_revision=None, upload_id=None)])
    after['outbox'][1]['state'] = 'queued'
    provider = dict(schema=1, owner_run='12345-1', source=quota.SYNC, backend='PostgreSQL/S3',
                    remote_vault='1'*32, quota=32, used=0, sequence=0, reserved_bytes=0,
                    rows=dict(revisions=[], files=[], objects=[], uploads=[]), progress=[],
                    delivery=dict(counts={}, events=[]), s3=dict(owner_run='12345-1', remote_vault='1'*32,
                        listing_complete_reported=True, object_count=0, continuation=None))
    remote = deepcopy(provider)
    remote.update(progress=[dict(device_id='2'*32, committed_sequence=None, committed_at=None,
                                 offered_sequence=0, offered_at=1)],
                  delivery=dict(counts={'changes:200': 1, 'upload:413': 1}, events=[
                      dict(action='changes', service_status=200, delivery_status=200, fault_operation=None),
                      dict(action='upload', service_status=413, delivery_status=413, fault_operation=None)]))
    response = dict(owner_run='12345-1', parent_id=uuid(2), input_id=uuid(3), remote_vault='1'*32,
                    device_id='2'*32, method='POST', path='/sync/v1/vaults/' + '1'*32 + '/uploads',
                    request=dict(content_hash=quota.HASH, size=56), service_status=413, delivery_status=413,
                    service_error_code='QUOTA_EXCEEDED', fault_operation=None,
                    asgi_send_returned_reported=True, client_receipt_verified=False)
    return dict(schema=1, scope=scope, before=before, after=after,
                provider_before=provider, provider_after=remote, upload_response=response)


class QuotaValidation(unittest.TestCase):
    def accept(self, value):
        return quota.validate_quota(value, quota.BODY, quota.BODY, quota.BODY)

    def reject(self, value):
        with self.assertRaisesRegex(ValueError, '^SIGNED_QUOTA_ORIGINAL_CORRELATION_UNAVAILABLE$'):
            self.accept(value)

    def test_exact_originals_and_narrow_facts_do_not_claim_runtime_or_future_components(self):
        value = fixture()
        original = deepcopy(value)
        result = self.accept(value)
        self.assertEqual(value, original)
        self.assertEqual(len(quota.BODY), 56)
        self.assertEqual(hashlib.sha256(quota.BODY).hexdigest(), quota.HASH)
        self.assertLess(result['declared_quota'], result['approved_note_bytes'])
        self.assertNotEqual(result['original_save_operation_id'], result['independent_upload_operation_id'])
        self.assertFalse(result['operation_id_sent_in_upload_request'])
        self.assertTrue(result['offered_sequence_is_not_committed_ACK'])
        for key in ('runtime_verified', 'Native_verified', 'UI_verified', 'desktop_verified',
                    'stopped_gate_verified', 'cold_verified', 'whole_A4_A6_verified'):
            self.assertIs(result[key], False)
        result['scope']['uid'] = 1
        self.assertEqual(value, original)
        self.assertEqual(self.accept(value)['scope']['uid'], 10216)

    def test_owner_operation_and_distinct_device_lineage_rejects_mixing(self):
        for block, key, change in (
            ('scope', 'product', '0'*40), ('scope', 'sync', '0'*40), ('scope', 'owner_run', '0123-1'),
            ('scope', 'avd', 'foreign'), ('scope', 'boot_id', 'not-a-uuid'), ('scope', 'uid', True),
            ('scope', 'window', 0), ('scope', 'api', 35), ('scope', 'setup_device', '2'*32),
            ('scope', 'upload_operation_id', fixture()['scope']['save_operation_id']),
            ('scope', 'association_operation_id', fixture()['scope']['login_operation_id']),
            ('upload_response', 'input_id', fixture()['scope']['parent_id']),
            ('upload_response', 'device_id', '3'*32), ('provider_after', 'owner_run', '54321-1')):
            value = fixture(); value[block][key] = change
            with self.subTest(block=block, key=key): self.reject(value)
        value = fixture(); value['after']['scope']['boot_id'] = value['scope']['parent_id']; self.reject(value)

    def test_original_save_draft_bytes_and_independent_archive_cannot_be_replaced(self):
        for block, key, change in (
            ('save', 'file_id', fixture()['before']['file']['id']), ('save', 'state', 'pending'),
            ('draft', 'saved_generation', 3), ('file', 'revision', True), ('file', 'deleted', 1),
            ('draft', 'content', 'private extra'), ('association', 'auth_operation_id', fixture()['scope']['setup_operation_id'])):
            value = fixture(); value['after'][block][key] = change
            with self.subTest(block=block, key=key): self.reject(value)
        value = fixture(); value['after']['outbox'][0]['state'] = 'acked'; self.reject(value)
        value = fixture(); value['after']['outbox'].append(deepcopy(value['after']['outbox'][1])); self.reject(value)
        for position in range(3):
            for bad in (b'private', quota.BODY + b' ', quota.BODY.replace(b'original', b'changed!'), bytearray(quota.BODY), None):
                args = [quota.BODY]*3; args[position] = bad
                with self.subTest(position=position, bad_type=type(bad).__name__), self.assertRaises(ValueError):
                    quota.validate_quota(fixture(), *args)

    def test_unique_pending_job_failed_attempt_issuer_and_null_commit_boundary(self):
        for table, key, change in (
            ('jobs', 'state', 'uploading'), ('jobs', 'remote_revision', 1), ('jobs', 'error', 'QUOTA_EXCEEDED'),
            ('attempts', 'attempts', 2), ('attempts', 'attempts', True), ('attempts', 'outcome', 'succeeded'),
            ('attempts', 'error', 'SYNC_NETWORK_ERROR'), ('issuers', 'device_id', '3'*32),
            ('commits', 'base_revision', 0), ('commits', 'upload_id', 'new-upload')):
            value = fixture(); value['after'][table][0][key] = change
            with self.subTest(table=table, key=key): self.reject(value)
        for table in ('jobs', 'attempts', 'issuers', 'commits'):
            value = fixture(); value['after'][table].append(deepcopy(value['after'][table][0])); self.reject(value)
        for table in ('heads', 'inbox', 'conflicts'):
            value = fixture(); value['after'][table] = [{}]; self.reject(value)

    def test_exact_capacity_remote_empty_content_and_independent_s3_report(self):
        for side in ('provider_before', 'provider_after'):
            for key, change in (('quota', 64), ('quota', 56), ('quota', True), ('used', 1),
                                ('sequence', 1), ('reserved_bytes', 1), ('backend', 'SQLite')):
                value = fixture(); value[side][key] = change
                with self.subTest(side=side, key=key): self.reject(value)
            for table in ('revisions', 'files', 'objects', 'uploads'):
                value = fixture(); value[side]['rows'][table] = [{}]; self.reject(value)
            for key, change in (('object_count', 1), ('listing_complete_reported', False),
                                ('listing_complete_reported', 1), ('continuation', 'partial'), ('remote_vault', '9'*32)):
                value = fixture(); value[side]['s3'][key] = change; self.reject(value)

    def test_empty_offer_can_change_but_never_committed_ack_or_another_device(self):
        value = fixture()
        setup = dict(device_id='3'*32, committed_sequence=None, committed_at=None, offered_sequence=0, offered_at=2)
        android = dict(setup, device_id='2'*32, offered_at=1)
        value['provider_before']['progress'] = [deepcopy(android), deepcopy(setup)]
        value['provider_after']['progress'] = [dict(android, offered_at=3), deepcopy(setup)]
        self.accept(value)
        for side, index, key, change in (
            ('provider_after', 1, 'offered_at', 3), ('provider_after', 0, 'offered_at', 0),
            ('provider_after', 0, 'committed_sequence', 0), ('provider_after', 0, 'committed_at', 3),
            ('provider_after', 0, 'offered_sequence', True), ('provider_before', 0, 'offered_at', True)):
            bad = deepcopy(value); bad[side]['progress'][index][key] = change; self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['progress'][0]['offered_at'] = 0; self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['progress'].reverse(); self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['progress'] = [android, android]; self.reject(bad)

    def test_actual_upload_metadata_and_delivery_delta_reject_fake_413_retry_or_body_echo(self):
        value = fixture()
        event = dict(action='changes', service_status=200, delivery_status=200, fault_operation=None)
        value['provider_before']['delivery'] = dict(counts={'changes:200': 2}, events=[event, event])
        value['provider_after']['delivery']['counts']['changes:200'] = 3
        value['provider_after']['delivery']['events'] = [event, event] + value['provider_after']['delivery']['events']
        self.accept(value)
        for key, change in (('service_status', 200), ('delivery_status', None), ('service_error_code', 'SYNC_NETWORK_ERROR'),
                            ('fault_operation', value['scope']['input_id']), ('asgi_send_returned_reported', None),
                            ('client_receipt_verified', True), ('method', 'GET')):
            bad = deepcopy(value); bad['upload_response'][key] = change; self.reject(bad)
        bad = deepcopy(value); bad['upload_response']['request']['operation_id'] = value['scope']['upload_operation_id']; self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['delivery']['counts']['upload:413'] = 2; self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['delivery']['events'].append(event); self.reject(bad)
        bad = deepcopy(value); bad['provider_after']['delivery']['counts']['revision:200'] = 1; self.reject(bad)
        bad = deepcopy(value); bad['provider_before']['delivery']['events'] = [event]*17; self.reject(bad)

    def test_closed_schema_at_each_boundary_rejects_partial_private_and_type_confusion(self):
        paths = [(), ('scope',), ('before',), ('after',), ('before','draft'), ('after','save'),
                 ('after','association'), ('provider_before',), ('provider_after','s3'),
                 ('provider_after','delivery'), ('upload_response',), ('upload_response','request')]
        for path in paths:
            for extra in ('password', 'raw_UI', 'auth_DB', 'private_exception'):
                bad = fixture(); target = bad
                for step in path: target = target[step]
                target[extra] = 'must never be retained'
                with self.subTest(path=path, extra=extra): self.reject(bad)
            bad = fixture(); target = bad
            for step in path: target = target[step]
            del target[next(iter(target))]
            self.reject(bad)
        for value in (True, 1.0, None, '1'):
            bad = fixture(); bad['schema'] = value; self.reject(bad)

    def test_unknown_objects_are_not_formatted_coerced_compared_or_hashed(self):
        class Unknown:
            def __str__(self): raise AssertionError('str')
            def __repr__(self): raise AssertionError('repr')
            def __bool__(self): raise AssertionError('bool')
            def __eq__(self, other): raise AssertionError('eq')
            def __hash__(self): raise AssertionError('hash')
        for path in (('scope','uid'), ('scope','owner_run'), ('before','draft','id'),
                     ('provider_before','progress'), ('provider_after','progress'), ('upload_response','service_status')):
            bad = fixture(); target = bad
            for step in path[:-1]: target = target[step]
            target[path[-1]] = Unknown()
            self.reject(bad)
        self.reject(Unknown())
        with self.assertRaises(ValueError): quota.validate_quota(fixture(), Unknown(), quota.BODY, quota.BODY)

    def test_bounded_strict_json_rejects_duplicate_utf8_partial_nonfinite_and_oversize(self):
        raw = json.dumps(fixture(), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertEqual(quota.validate_json(raw, quota.BODY, quota.BODY, quota.BODY), self.accept(fixture()))
        padded = raw + b' '*(quota.MAX_JSON-len(raw))
        quota.validate_json(padded, quota.BODY, quota.BODY, quota.BODY)
        bad = [padded+b' ', raw[:-1], b'\xff', raw.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
               raw.replace(b'"uid":10216', b'"uid":NaN', 1), raw.replace(b'"quota":32', b'"quota":32.0', 1),
               b'['*1000 + b']'*1000, raw.decode('utf-8'), None]
        for value in bad:
            with self.assertRaises(ValueError): quota.validate_json(value, quota.BODY, quota.BODY, quota.BODY)

    def test_integer_extremes_and_positive_regressing_offer_are_not_coerced(self):
        value = fixture()
        for side in ('before', 'after'):
            value[side]['draft']['generation'] = value[side]['draft']['saved_generation'] = quota.MAX_INT
            value[side]['save']['generation'] = quota.MAX_INT
        self.accept(value)
        for change in (quota.MAX_INT+1, -1, True, 2.0):
            bad = deepcopy(value)
            for side in ('before', 'after'):
                bad[side]['draft']['generation'] = bad[side]['draft']['saved_generation'] = change
                bad[side]['save']['generation'] = change
            self.reject(bad)
        for key, change in (('uid', 2**31), ('window', 2**31), ('window', True)):
            bad = fixture(); bad['scope'][key] = change; self.reject(bad)
        value = fixture()
        prior = dict(device_id='2'*32, committed_sequence=None, committed_at=None, offered_sequence=0, offered_at=10)
        value['provider_before']['progress'] = [prior]
        value['provider_after']['progress'] = [dict(prior, offered_at=9)]
        self.reject(value)
        value['provider_after']['progress'][0]['offered_at'] = quota.MAX_INT
        self.accept(value)
        value['provider_after']['progress'][0]['offered_at'] = quota.MAX_INT+1
        self.reject(value)


if __name__ == '__main__':
    unittest.main()
