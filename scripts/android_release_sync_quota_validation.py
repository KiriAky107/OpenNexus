"""Pure correlation of closed signed-quota projections, never runtime admission.

Collectors must separately admit completed-driver/owned-stop and raw originals.
Reports supplied here cannot attest a device, SQL/S3 read, HTTP delivery or ACK.
The uploads request carries content_hash/size, not the Native operation_id.
"""
import json
import re

PRODUCT = 'd118b2ba63c922142f9fadee8772b6ec15791f48'
SYNC = '29e2e6d373ea752352981bdad05d34a7a0d9c3d9'
NOTE = 'release-owned-note.md'
BODY = '# 原始 Markdown 😀\n\n离线正文 / original content\n'.encode('utf-8')
HASH = '7662c0191d53f61020a020ca8b86238914ba39bdb017a48f9eb3dd709f5594d9'
QUOTA = 32
MAX_JSON = 32768
MAX_INT = 2**63 - 1


def _need(value):
    if not value:
        raise ValueError('SIGNED_QUOTA_ORIGINAL_CORRELATION_UNAVAILABLE')


def _closed(value, keys):
    return (type(value) is dict and len(value) == len(keys)
            and all(type(k) is str for k in value) and set(value) == set(keys))


def _same(value, expected):
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return _closed(value, expected) and all(_same(value[k], v) for k, v in expected.items())
    if type(expected) is list:
        return len(value) == len(expected) and all(_same(a, b) for a, b in zip(value, expected))
    return value == expected


def _number(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INT


def _pattern(value, pattern):
    return type(value) is str and len(value) <= 80 and re.fullmatch(pattern, value) is not None


def _uuid(value):
    return _pattern(value, r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def _scope(value):
    operations = ('login_operation_id', 'setup_operation_id', 'save_operation_id',
                  'association_operation_id', 'upload_operation_id')
    keys = {'schema', 'owner_run', 'avd', 'boot_id', 'api', 'uid', 'window', 'product', 'sync',
            'parent_id', 'input_id', 'remote_vault', 'binding_id', 'android_device', 'setup_device', *operations}
    _need(_closed(value, keys))
    _need(_pattern(value['owner_run'], r'[1-9][0-9]{0,19}-[1-9][0-9]{0,5}')
          and _same(value['avd'], 'opennexus-ci-' + value['owner_run'])
          and _same(value['schema'], 1) and _same(value['api'], 36)
          and _same(value['product'], PRODUCT) and _same(value['sync'], SYNC)
          and _number(value['uid'], 10000) and value['uid'] <= 2**31-1
          and _number(value['window'], 1) and value['window'] <= 2**31-1)
    _need(all(_uuid(value[k]) for k in ('boot_id', 'parent_id', 'input_id', 'binding_id', *operations)))
    _need(value['parent_id'] != value['input_id'] and len({value[k] for k in operations}) == len(operations))
    _need(all(_pattern(value[k], r'[0-9a-f]{32}') for k in ('remote_vault', 'android_device', 'setup_device'))
          and value['android_device'] != value['setup_device'])
    return dict(value)


def _before(value, scope):
    keys = {'schema', 'scope', 'stage', 'draft', 'save', 'file', 'binding', 'association',
            'outbox', 'jobs', 'attempts', 'issuers', 'commits', 'heads', 'inbox', 'conflicts'}
    _need(_closed(value, keys) and _same(value['schema'], 1) and _same(value['scope'], scope)
          and _same(value['stage'], 'associated'))
    draft, file, association = value['draft'], value['file'], value['association']
    _need(_closed(draft, ('id', 'file_id', 'path', 'base_hash', 'generation', 'saved_generation', 'content'))
          and _uuid(draft['id']) and _uuid(draft['file_id']) and _number(draft['generation'], 1))
    _need(_closed(file, ('id', 'path', 'hash', 'revision', 'deleted')) and _number(file['revision'], 1))
    expected_draft = dict(id=draft['id'], file_id=draft['file_id'], path=NOTE, base_hash=HASH,
                          generation=draft['generation'], saved_generation=draft['generation'], content=BODY.decode('utf-8'))
    expected_file = dict(id=draft['file_id'], path=NOTE, hash=HASH, revision=file['revision'], deleted=0)
    save = dict(operation_id=scope['save_operation_id'], draft_id=draft['id'], generation=draft['generation'],
                file_id=None, path=NOTE, expected='', content=BODY.decode('utf-8'), state='committed', error=None)
    association_keys = ('operation_id', 'auth_operation_id', 'remote_vault', 'state', 'attempt_revision',
                        'request_hash', 'preview_fingerprint', 'original_request_bytes_sha256', 'original_binding_bytes_sha256')
    _need(_closed(association, association_keys) and _number(association['attempt_revision'], 1)
          and all(_pattern(association[k], r'[0-9a-f]{64}') for k in association_keys[5:]))
    expected_association = dict(association, operation_id=scope['association_operation_id'],
                                auth_operation_id=scope['login_operation_id'], remote_vault=scope['remote_vault'], state='bound')
    outbox = dict(operation_id=scope['upload_operation_id'], file_id=draft['file_id'], revision=file['revision'],
                  path=NOTE, hash=HASH, operation='put', state='pending')
    expected = dict(schema=1, scope=scope, stage='associated', draft=expected_draft, save=save, file=expected_file,
                    binding=dict(id=scope['binding_id'], remote_vault=scope['remote_vault'], state='active', cursor=0),
                    association=expected_association,
                    outbox=[dict(outbox, operation_id=scope['save_operation_id'], state='archived'), outbox],
                    jobs=[], attempts=[], issuers=[], commits=[], heads=[], inbox=[], conflicts=[])
    _need(_same(value, expected))
    return expected


def _failed(before, scope):
    file_id, op = before['file']['id'], scope['upload_operation_id']
    return dict(before, stage='quota-failed', outbox=[dict(before['outbox'][0]), dict(before['outbox'][1], state='queued')],
                jobs=[dict(operation_id=op, file_id=file_id, path=NOTE, hash=HASH, size=len(BODY),
                           state='pending', remote_revision=None, error=None)],
                attempts=[dict(operation_id=op, attempts=1, outcome='failed', error='QUOTA_EXCEEDED')],
                issuers=[dict(operation_id=op, device_id=scope['android_device'])],
                commits=[dict(binding=scope['binding_id'], operation_id=op, file_id=file_id,
                              operation='put', base_revision=None, upload_id=None)])


def _progress(value, scope):
    _need(type(value) is list and len(value) <= 2)
    by_id = {}
    for row in value:
        _need(_closed(row, ('device_id', 'committed_sequence', 'committed_at', 'offered_sequence', 'offered_at'))
              and type(row['device_id']) is str and row['device_id'] in (scope['android_device'], scope['setup_device'])
              and row['device_id'] not in by_id and _number(row['offered_at'], 1))
        expected = dict(device_id=row['device_id'], committed_sequence=None, committed_at=None,
                        offered_sequence=0, offered_at=row['offered_at'])
        _need(_same(row, expected))
        by_id[row['device_id']] = expected
    _need([v['device_id'] for v in value] == sorted(by_id))
    return by_id


def _delivery_event(action, status):
    return dict(action=action, service_status=status, delivery_status=status, fault_operation=None)


def _provider_before(value, scope):
    keys = ('schema', 'owner_run', 'source', 'backend', 'remote_vault', 'quota', 'used', 'sequence',
            'reserved_bytes', 'rows', 'progress', 'delivery', 's3')
    _need(_closed(value, keys) and _closed(value['delivery'], ('counts', 'events')))
    events = value['delivery']['events']
    _need(type(events) is list and len(events) <= 16)
    _progress(value['progress'], scope)
    count = len(events)
    expected = dict(schema=1, owner_run=scope['owner_run'], source=SYNC, backend='PostgreSQL/S3',
                    remote_vault=scope['remote_vault'], quota=QUOTA, used=0, sequence=0, reserved_bytes=0,
                    rows=dict(revisions=[], files=[], objects=[], uploads=[]), progress=value['progress'],
                    delivery=dict(counts={'changes:200': count} if count else {},
                                  events=[_delivery_event('changes', 200) for _ in range(count)]),
                    s3=dict(owner_run=scope['owner_run'], remote_vault=scope['remote_vault'],
                            listing_complete_reported=True, object_count=0, continuation=None))
    _need(_same(value, expected))
    return expected


def _provider_after(value, before, scope):
    _need(_closed(value, before))
    prior, current = _progress(before['progress'], scope), _progress(value['progress'], scope)
    device = scope['android_device']
    _need(set(current) == set(prior) | {device})
    _need(all(_same(current[k], v) for k, v in prior.items() if k != device))
    _need(device not in prior or current[device]['offered_at'] >= prior[device]['offered_at'])
    count = len(before['delivery']['events'])
    expected = dict(before, progress=value['progress'],
                    delivery=dict(counts={'changes:200': count + 1, 'upload:413': 1},
                                  events=before['delivery']['events'] + [_delivery_event('changes', 200), _delivery_event('upload', 413)]))
    _need(_same(value, expected))


def validate_quota(value, original, observed, spool):
    """Validate supplied metadata/approved bytes, return fresh correlation facts.

    No collector, stopped gate, UI, Native, cold process or runtime is executed.
    No arbitrary source strings, exception text or private content are returned.
    """
    _need(all(type(v) is bytes and len(v) == 56 and v == BODY for v in (original, observed, spool)))
    _need(_closed(value, ('schema', 'scope', 'before', 'after', 'provider_before', 'provider_after', 'upload_response'))
          and _same(value['schema'], 1))
    scope = _scope(value['scope'])
    before = _before(value['before'], scope)
    _need(_same(value['after'], _failed(before, scope)))
    provider = _provider_before(value['provider_before'], scope)
    _provider_after(value['provider_after'], provider, scope)
    response = dict(owner_run=scope['owner_run'], parent_id=scope['parent_id'], input_id=scope['input_id'],
                    remote_vault=scope['remote_vault'], device_id=scope['android_device'], method='POST',
                    path='/sync/v1/vaults/' + scope['remote_vault'] + '/uploads',
                    request=dict(content_hash=HASH, size=56), service_status=413, delivery_status=413,
                    service_error_code='QUOTA_EXCEEDED', fault_operation=None,
                    asgi_send_returned_reported=True, client_receipt_verified=False)
    _need(_same(value['upload_response'], response))
    return dict(schema=1, scope=scope, declared_quota=QUOTA, approved_note_bytes=56,
                original_save_operation_id=scope['save_operation_id'], independent_upload_operation_id=scope['upload_operation_id'],
                local_originals_correlated=True, unique_pending_failed_attempt_correlated=True,
                provider_empty_metadata_correlated=True, owned_s3_empty_report_correlated=True,
                upload_413_metadata_correlated=True, operation_id_sent_in_upload_request=False,
                offered_sequence_is_not_committed_ACK=True, runtime_verified=False, Native_verified=False,
                UI_verified=False, desktop_verified=False, stopped_gate_verified=False, cold_verified=False,
                whole_A4_A6_verified=False)


def validate_json(raw, original, observed, spool):
    """Optional bounded duplicate-rejecting decoder for the same pure contract."""
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON)

    def pairs(items):
        value = dict(items)
        _need(len(value) == len(items))
        return value

    def constant(_):
        _need(False)

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('SIGNED_QUOTA_ORIGINAL_CORRELATION_UNAVAILABLE') from None
    return validate_quota(value, original, observed, spool)
