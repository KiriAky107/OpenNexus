"""Pure typed raw3 bootstrap-to-cycle correlation; no live admission or IO."""
import hashlib
import json

import android_release_sync_quota_late_binding as primitive
import android_release_sync_quota_host_prefix as prefix
import android_release_sync_quota_records as records
import android_release_sync_quota_validation as quota


def need(value):
    if not value:
        raise ValueError('SIGNED_QUOTA_BOOTSTRAP_BINDING_UNAVAILABLE')


def manifest_value(raw):
    need(type(raw) is bytes and 0 < len(raw) <= primitive.MAX_MANIFEST)
    def pairs(items):
        value = dict(items); need(len(value) == len(items)); return value
    def constant(_): need(False)
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    records.closed(value, ('schema','package','identity','quota_association_capture_scope','quota_stage',
        'collection_id','admission_metadata_canonical_sha256','spool_source','files',
        'app_stopped_before_and_after','credential_store_copied','SQL_executed_on_device',
        'normal_phone_permissions_verified','binding_and_upload_ids_present_at_capture'))
    capture = quota.bootstrap_capture(value['quota_association_capture_scope'], value['collection_id'],
                                      value['admission_metadata_canonical_sha256'])
    records.owned_identity(value['identity'], capture)
    files = value['files']; records.closed(files, primitive.FILES)
    for name, metadata in files.items():
        if metadata is None:
            need(name.endswith(('-wal','-shm')))
        else:
            records.closed(metadata, ('bytes','sha256'))
            records.number(metadata['bytes'], maximum=primitive.MAX_FILE)
            need(quota._pattern(metadata['sha256'], r'[0-9a-f]{64}'))
    for name in (primitive.NOTE, primitive.SPOOL):
        records.exact(files[name], dict(bytes=56, sha256=quota.HASH))
    records.exact(value, dict(schema=3, package=records.APP, identity=value['identity'],
        quota_association_capture_scope=capture, quota_stage=quota.ASSOCIATED_BOOTSTRAP,
        collection_id=value['collection_id'], admission_metadata_canonical_sha256=value['admission_metadata_canonical_sha256'],
        spool_source=primitive.SPOOL_SOURCE, files=files, app_stopped_before_and_after=True,
        credential_store_copied=False, SQL_executed_on_device=False, normal_phone_permissions_verified=False,
        binding_and_upload_ids_present_at_capture=False))
    return value, capture


def bind_bootstrap_capture(manifest_raw, projection, evidence, note, spool, *, parent, ready, cycle_scope):
    try:
        manifest, capture = manifest_value(manifest_raw)
        need(type(note) is bytes and type(spool) is bytes and note == spool == quota.BODY)
        records.closed(projection, ('schema','capture_scope','derived_operation_scope','stage','draft','save','file',
            'binding','association','outbox','jobs','attempts','issuers','commits','heads','inbox','conflicts'))
        operation = quota.association_scope(projection['derived_operation_scope'])
        records.exact(operation, quota.derived_association_scope(capture, operation['binding_id'],
                       operation['upload_operation_id'], manifest['collection_id']))
        captured = quota.before_associated_bootstrap(projection, capture, operation)
        records.closed(evidence, ('original_manifest_raw_sha256','original_files','query_only_new_working_copies',
            'raw_DB_WAL_SHM_never_opened_by_SQLite','credentials_selected','completed_driver_and_stop_gate_verified',
            'runtime_verified','collection_id','admission_metadata_canonical_sha256','original_cycle_ids_present_at_capture',
            'later_cycle_binding_verified','binding_upload_ids_derived_after_capture',
            'raw_capture_scope_has_binding_upload_ids','derivation_envelope'))
        envelope = evidence['derivation_envelope']
        records.closed(envelope, ('schema','collection_id','original_manifest_raw_sha256',
            'capture_scope_typed_canonical_sha256','derived_operation_scope_typed_canonical_sha256',
            'projection_typed_canonical_sha256','binding_upload_ids_derived_after_capture',
            'raw_capture_scope_has_binding_upload_ids','independent_capture_before_quota_launch_verified',
            'Native_or_client_ACK','runtime'))
        hashes = ('original_manifest_raw_sha256','capture_scope_typed_canonical_sha256',
                  'derived_operation_scope_typed_canonical_sha256','projection_typed_canonical_sha256')
        need(all(quota._pattern(envelope[k], r'[0-9a-f]{64}') for k in hashes))
        records.exact(envelope, dict(schema=1, collection_id=manifest['collection_id'],
            **{k:envelope[k] for k in hashes}, binding_upload_ids_derived_after_capture=True,
            raw_capture_scope_has_binding_upload_ids=False, independent_capture_before_quota_launch_verified=False,
            Native_or_client_ACK=False, runtime=False))
        records.exact(evidence, dict(original_manifest_raw_sha256=envelope['original_manifest_raw_sha256'],
            original_files=manifest['files'], query_only_new_working_copies=True,
            raw_DB_WAL_SHM_never_opened_by_SQLite=True, credentials_selected=False,
            completed_driver_and_stop_gate_verified=False, runtime_verified=False, collection_id=manifest['collection_id'],
            admission_metadata_canonical_sha256=manifest['admission_metadata_canonical_sha256'],
            original_cycle_ids_present_at_capture=False, later_cycle_binding_verified=False,
            binding_upload_ids_derived_after_capture=True, raw_capture_scope_has_binding_upload_ids=False,
            derivation_envelope=envelope))
        scope = quota._scope(cycle_scope)
        records.exact({k:v for k,v in scope.items() if k not in ('parent_id','input_id','window')}, operation)
        parent = primitive.parent_value(parent)
        ready = prefix.ready_value(ready, parent, operation['uid'])
        records.exact(scope, dict(operation, parent_id=parent['parent_intent_id'],
                                 input_id=parent['input_intent_id'], window=ready['window']))
        known = {v for v in operation.values() if quota._uuid(v)} | {
            manifest['collection_id'], captured['draft']['id'], captured['file']['id']}
        need(parent['parent_intent_id'] not in known and parent['input_intent_id'] not in known)
        need(len(records.encoded(captured)) <= quota.MAX_JSON and len(records.encoded(evidence)) <= primitive.MAX_MANIFEST
             and len(records.encoded(parent)) <= 4096 and len(records.encoded(ready)) <= 4096)
        before = dict(captured, schema=1, scope=scope, stage='associated')
        del before['capture_scope']; del before['derived_operation_scope']
        before = quota._before(before, scope)
        # Entire supplied metadata is now closed and approved. No hash repairs
        # an unknown/private value or turns typed provenance into file evidence.
        raw_digest = hashlib.sha256(manifest_raw).hexdigest()
        records.exact(envelope['original_manifest_raw_sha256'], raw_digest)
        records.exact(envelope['capture_scope_typed_canonical_sha256'], records.digest(capture))
        records.exact(envelope['derived_operation_scope_typed_canonical_sha256'], records.digest(operation))
        records.exact(envelope['projection_typed_canonical_sha256'], records.digest(captured))
        binding = dict(schema=1, source_manifest_schema=3, collection_id=manifest['collection_id'],
            original_bootstrap_manifest_raw_sha256=raw_digest,
            bootstrap_derivation_metadata_canonical_sha256=records.digest(envelope),
            bootstrap_projection_metadata_canonical_sha256=records.digest(captured),
            quota_parent_metadata_canonical_sha256=records.digest(parent),
            quota_ready_metadata_canonical_sha256=records.digest(ready), full_cycle_scope=scope,
            binding_upload_ids_derived_after_capture=True, raw_capture_scope_has_binding_upload_ids=False,
            original_cycle_ids_present_at_capture=False, projected_scope_bound_after_ready=True,
            independent_raw_parent_ready_verified=False, Native_or_client_ACK=False, runtime_verified=False)
        result = dict(schema=1, before=before, binding=binding, typed_correlation_only=True,
            captured_before_quota_launch_independently_verified=False,
            original_files_independently_verified=False, runtime_verified=False)
        raw = records.encoded(result); need(len(raw) <= records.MAX_BUNDLE)
        return json.loads(raw)  # detach all repeated dictionaries and arrays
    except (ValueError,RuntimeError,KeyError,IndexError,UnicodeError,RecursionError):
        raise ValueError('SIGNED_QUOTA_BOOTSTRAP_BINDING_UNAVAILABLE') from None
