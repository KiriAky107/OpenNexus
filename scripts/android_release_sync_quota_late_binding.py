"""Correlate already-provided pre-cycle metadata to a later quota ready, in RAM.

No file, SQL, device, clock, callback or action. Typed correlation cannot attest
original files, actual capture/launch order, independent admission or an ACK.
"""
import hashlib
import json

import android_release_sync_quota_host_prefix as prefix
import android_release_sync_quota_records as records
import android_release_sync_quota_validation as quota

DATABASES = ('mobile-drafts.sqlite3','vault/.ainote/host.sqlite3')
NOTE = 'vault/'+quota.NOTE
SPOOL = 'release-owned-quota-spool.md'
SPOOL_SOURCE = 'vault/.ainote/sync-spool/'+quota.HASH
FILES = frozenset((NOTE,SPOOL)) | frozenset(db+s for db in DATABASES for s in ('','-wal','-shm'))
MAX_MANIFEST = 16384
MAX_FILE = 32*1024*1024


def need(value):
    if not value:
        raise ValueError('SIGNED_QUOTA_LATE_BINDING_UNAVAILABLE')


def manifest_value(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_MANIFEST)
    def pairs(items):
        value = dict(items);need(len(value)==len(items));return value
    def constant(_):need(False)
    value = json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    records.closed(value,('schema','package','identity','quota_association_scope','quota_stage',
        'collection_id','admission_metadata_canonical_sha256','spool_source','files',
        'app_stopped_before_and_after','credential_store_copied','SQL_executed_on_device',
        'normal_phone_permissions_verified'))
    base = quota.association_capture(value['quota_association_scope'],value['collection_id'],
                                     value['admission_metadata_canonical_sha256'])
    records.owned_identity(value['identity'],base)
    files = value['files'];records.closed(files,FILES)
    for name,metadata in files.items():
        if metadata is None:
            need(name.endswith(('-wal','-shm')))
        else:
            records.closed(metadata,('bytes','sha256'))
            records.number(metadata['bytes'],maximum=MAX_FILE)
            need(quota._pattern(metadata['sha256'],r'[0-9a-f]{64}'))
    for name in (NOTE,SPOOL):records.exact(files[name],dict(bytes=56,sha256=quota.HASH))
    records.exact(value,dict(schema=2,package=records.APP,identity=value['identity'],
        quota_association_scope=base,quota_stage=quota.ASSOCIATED_BEFORE_CYCLE,
        collection_id=value['collection_id'],admission_metadata_canonical_sha256=value['admission_metadata_canonical_sha256'],
        spool_source=SPOOL_SOURCE,files=files,app_stopped_before_and_after=True,
        credential_store_copied=False,SQL_executed_on_device=False,normal_phone_permissions_verified=False))
    return value,base


def parent_value(value):
    records.closed(value,('schema','phase','parent_intent_id','input_intent_id','native_invocation_id_observed'))
    need(quota._uuid(value['parent_intent_id']) and quota._uuid(value['input_intent_id'])
         and value['parent_intent_id']!=value['input_intent_id'])
    result = dict(schema=1,phase=records.PHASE,parent_intent_id=value['parent_intent_id'],
        input_intent_id=value['input_intent_id'],native_invocation_id_observed=False)
    records.exact(value,result)
    return result


def bind_associated_capture(manifest_raw,projection,evidence,note,spool,*,parent,ready,cycle_scope):
    try:
        manifest,base = manifest_value(manifest_raw)
        need(type(note) is bytes and type(spool) is bytes and note==spool==quota.BODY)
        captured = quota.before_associated_capture(projection,base)
        records.closed(evidence,('original_manifest_raw_sha256','original_files','query_only_new_working_copies',
            'raw_DB_WAL_SHM_never_opened_by_SQLite','credentials_selected','completed_driver_and_stop_gate_verified',
            'runtime_verified','collection_id','admission_metadata_canonical_sha256',
            'original_cycle_ids_present_at_capture','later_cycle_binding_verified'))
        need(quota._pattern(evidence['original_manifest_raw_sha256'],r'[0-9a-f]{64}'))
        records.exact(evidence,dict(original_manifest_raw_sha256=evidence['original_manifest_raw_sha256'],
            original_files=manifest['files'],query_only_new_working_copies=True,
            raw_DB_WAL_SHM_never_opened_by_SQLite=True,credentials_selected=False,
            completed_driver_and_stop_gate_verified=False,runtime_verified=False,
            collection_id=manifest['collection_id'],
            admission_metadata_canonical_sha256=manifest['admission_metadata_canonical_sha256'],
            original_cycle_ids_present_at_capture=False,later_cycle_binding_verified=False))
        scope = quota._scope(cycle_scope)
        actual_base = {k:v for k,v in scope.items() if k not in ('parent_id','input_id','window')}
        records.exact(actual_base,base)
        parent = parent_value(parent)
        ready = prefix.ready_value(ready,parent,base['uid'])
        records.exact(scope,dict(base,parent_id=parent['parent_intent_id'],
                                 input_id=parent['input_intent_id'],window=ready['window']))
        known_ids = {v for v in base.values() if quota._uuid(v)} | {manifest['collection_id']}
        need(parent['parent_intent_id'] not in known_ids and parent['input_intent_id'] not in known_ids)
        need(len(records.encoded(captured)) <= quota.MAX_JSON and len(records.encoded(evidence)) <= MAX_MANIFEST
             and len(records.encoded(parent)) <= 4096 and len(records.encoded(ready)) <= 4096)
        # Every retained value is now approved and closed. Hash no unknown
        # strings or raw unapproved data while trying to repair an input.
        raw_digest = hashlib.sha256(manifest_raw).hexdigest()
        records.exact(evidence['original_manifest_raw_sha256'],raw_digest)
        before = dict(captured,schema=1,scope=scope,stage='associated')
        del before['association_scope']
        before = quota._before(before,scope)
        binding = dict(schema=1,collection_id=manifest['collection_id'],
            original_associated_manifest_raw_sha256=raw_digest,
            associated_projection_metadata_canonical_sha256=records.digest(captured),
            quota_parent_metadata_canonical_sha256=records.digest(parent),
            quota_ready_metadata_canonical_sha256=records.digest(ready),full_cycle_scope=scope,
            original_cycle_ids_present_at_capture=False,projected_scope_bound_after_ready=True,
            independent_raw_parent_ready_verified=False,Native_or_client_ACK=False,runtime_verified=False)
        result = dict(schema=1,before=before,binding=binding,typed_correlation_only=True,
            captured_before_quota_launch_independently_verified=False,
            original_files_independently_verified=False,runtime_verified=False)
        raw = records.encoded(result);need(len(raw) <= records.MAX_BUNDLE)
        # JSON detaches even repeated scope/list references between outputs.
        return json.loads(raw)
    except (ValueError,RuntimeError,KeyError,IndexError,UnicodeError,RecursionError):
        raise ValueError('SIGNED_QUOTA_LATE_BINDING_UNAVAILABLE') from None
