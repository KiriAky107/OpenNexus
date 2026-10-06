"""Reviewed signed data -> existing configuration services, with durable exact receipts."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re
import threading
import time
from uuid import UUID, uuid4

from pydantic import ConfigDict, ValidationError

from app import host_bridge
from app.contracts import McpServerCreateRequest
from app.database.db import connect, transaction
from app.errors import ApiError
from app.local_models.catalog import CATALOG
from app.local_models.runtime import RuntimeConfig, configuration, configure_in_transaction

_lock = threading.RLock()
_digest = re.compile(r'^[0-9a-f]{64}$')
_secret = re.compile(r'(api[_-]?key|token|password|secret|authorization|cookie)', re.I)


def fail(code='CATALOG_CONFIGURATION_INVALID', status=409):
    raise ApiError(status, code, '请重新核对社区配置和实际目标。 / Review the catalog configuration and target again.')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def candidate(slot):
    vault = host_bridge.vault_id.get()
    if not vault:
        fail('WORKSPACE_NOT_OPEN')
    if not _digest.fullmatch(slot):
        fail()
    if host_bridge.active is None:
        fail('HOST_UNAVAILABLE', 503)
    try:
        value = host_bridge.active.call('catalog.configuration_candidate', vault_id=vault, slot=slot)
    except RuntimeError as exc:
        code = str(exc)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
            code = 'HOST_UNAVAILABLE'
        fail(code, 503 if code in {'HOST_UNAVAILABLE', 'HOST_TIMEOUT'} else 409)
    if (not isinstance(value, dict) or value.get('vault_id') != vault or value.get('slot') != slot
            or value.get('kind') not in {'mcp', 'model'}
            or not _digest.fullmatch(value.get('binding', ''))
            or not isinstance(value.get('manifest'), dict)
            or not all(isinstance(value.get(k), str) and 0 < len(value[k]) <= 256
                       for k in ('package_name', 'package_version'))):
        fail('HOST_RESPONSE_INVALID', 503)
    return value


def schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS catalog_config_reviews '
                 '(review_id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS catalog_config_operations '
                 '(operation_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, fingerprint TEXT NOT NULL, '
                 'state TEXT NOT NULL, receipt TEXT)')


class RuntimeProposal(RuntimeConfig):
    model_config = ConfigDict(extra='forbid')


def proposal(material, before, registry):
    manifest = dict(material['manifest'])
    if material['kind'] == 'mcp':
        for key in ('schema_version', 'configuration_schema'):
            manifest.pop(key, None)
        manifest.setdefault('name', material['package_name'])
        # Community bundles carry declarations, not credential values. Existing
        # declarations are retained so applying a package cannot erase credentials.
        for field in ('environment', 'headers'):
            if not isinstance(manifest.get(field, {}), dict):
                fail()
            if any(_secret.search(key) for key in manifest.get(field, {})):
                fail('CATALOG_PLAINTEXT_SECRET', 422)
            if any(_secret.search(key) for key in (before or {}).get(field, {})):
                # A legacy plain credential must not be copied into review
                # storage or returned as a before/after field.
                fail('CATALOG_PLAINTEXT_SECRET', 422)
        for field in ('secret_environment_keys', 'secret_header_keys'):
            declared = manifest.get(field, [])
            if not isinstance(declared, list) or not all(isinstance(key, str) for key in declared):
                fail()
            manifest[field] = list(dict.fromkeys([*declared, *(before or {}).get(field, [])]))
        request = McpServerCreateRequest.model_validate(manifest)
        registry._validate(request)
        data = request.model_dump(mode='json')
        data['name'] = request.name.strip()
        data['command'] = request.command.strip() if request.command else None
        data['url'] = request.url.strip() if request.url else None
        data.update(version=(before or {}).get('version', 0) + 1, enabled=False,
                    approved_digest=None, tested_digest=None)
        return data
    allowed = {'schema_version', 'permissions', 'configuration_schema', 'name', 'source', 'revision',
               'license', 'resources', 'verified_platforms', 'model_key', 'runtime_config'}
    if set(manifest) - allowed or not isinstance(manifest.get('runtime_config'), dict):
        fail('CATALOG_MODEL_TARGET_UNSUPPORTED', 422)
    key = manifest.get('model_key')
    spec = CATALOG.get(key) if isinstance(key, str) else None
    if (spec is None or spec.capability != 'embedding' or manifest.get('source') != spec.repository
            or manifest.get('revision') != spec.revision or manifest.get('license') != spec.license):
        fail('CATALOG_MODEL_PIN_MISMATCH', 422)
    requested = dict(manifest['runtime_config'])
    if 'version' in requested or requested.get('embedding_model') != key:
        fail('CATALOG_MODEL_TARGET_UNSUPPORTED', 422)
    result = RuntimeProposal.model_validate({**before, **requested})
    if result.version >= 9007199254740991:
        fail('CATALOG_VERSION_EXHAUSTED')
    return result.model_copy(update={'version': result.version + 1}).model_dump(mode='json')


def snapshot(kind, target, registry):
    if kind == 'model' and target == 'model:local_runtime':
        return configuration().model_dump(mode='json')
    if kind == 'mcp' and re.fullmatch(r'mcp:[A-Za-z0-9][A-Za-z0-9_-]{0,63}', target):
        return registry.catalog_snapshot(target[4:])
    fail('CATALOG_CONFIGURATION_TARGET')


def targets(slot, registry):
    material = candidate(slot)
    options = ([{'id': 'mcp:new', 'label': '新建 MCP 配置 / New MCP configuration'}]
               + [{'id': 'mcp:' + server.server_id, 'label': server.name} for server in registry.list()]
               if material['kind'] == 'mcp' else
               [{'id': 'model:local_runtime', 'label': '本地模型运行设置 / Local model runtime'}])
    return {'kind': material['kind'], 'package_name': material['package_name'], 'items': options}


def preview(slot, target, registry):
    with _lock:
        material = candidate(slot)
        if material['kind'] == 'mcp' and target == 'mcp:new':
            target = 'mcp:' + uuid4().hex[:12]
        before = snapshot(material['kind'], target, registry)
        try:
            after = proposal(material, before, registry)
        except (ValidationError, TypeError, ValueError):
            fail(status=422)
        payload = {'vault_id': material['vault_id'], 'slot': slot, 'target': target, 'kind': material['kind'],
                   'binding': material['binding'], 'before': before, 'after': after,
                   'after_sha256': digest(after), 'package_name': material['package_name'],
                   'package_version': material['package_version']}
        if material['kind'] == 'model':
            payload['model_plan'] = {key: material['manifest'][key] for key in
                                     ('model_key', 'source', 'revision', 'license', 'resources', 'verified_platforms')}
        payload['fingerprint'] = digest(payload)
        payload['review_id'] = str(uuid4())
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 512 * 1024:
            fail('CATALOG_REVIEW_TOO_LARGE', 422)
        with closing(connect()) as conn, transaction(conn, immediate=True):
            schema(conn)
            conn.execute('DELETE FROM catalog_config_reviews WHERE created < ? AND review_id NOT IN '
                         '(SELECT review_id FROM catalog_config_operations)', (time.time() - 3600,))
            count = conn.execute('SELECT COUNT(*) FROM catalog_config_reviews WHERE created >= ?',
                                 (time.time() - 300,)).fetchone()[0]
            if count >= 128:
                fail('CATALOG_REVIEW_LIMIT')
            conn.execute('INSERT INTO catalog_config_reviews VALUES (?,?,?)',
                         (payload['review_id'], time.time(), json.dumps(payload, ensure_ascii=False)))
        return {key: value for key, value in payload.items() if key not in {'binding', 'vault_id'}}


def load_review(conn, review_id, fingerprint):
    row = conn.execute('SELECT created,payload FROM catalog_config_reviews WHERE review_id=?', (review_id,)).fetchone()
    if row is None:
        fail('CATALOG_REVIEW_NOT_FOUND')
    payload = json.loads(row['payload'])
    if payload['vault_id'] != host_bridge.vault_id.get() or payload['fingerprint'] != fingerprint:
        fail('CATALOG_REVIEW_CHANGED')
    return row['created'], payload


def result(payload, operation_id):
    return {'operation_id': operation_id, 'fingerprint': payload['fingerprint'], 'state': 'applied',
            'target': payload['target'], 'kind': payload['kind'], 'after_sha256': payload['after_sha256']}


def operation(operation_id, fingerprint, registry):
    with _lock, closing(connect()) as conn, transaction(conn, immediate=True):
        schema(conn)
        row = conn.execute('SELECT * FROM catalog_config_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            return None
        _, payload = load_review(conn, row['review_id'], fingerprint)
        if row['fingerprint'] != fingerprint:
            fail('CATALOG_OPERATION_CHANGED')
        if row['state'] == 'applied':
            return json.loads(row['receipt'])
        marker = registry.catalog_receipt(payload['target'][4:], operation_id) if payload['kind'] == 'mcp' else None
        if marker == {key: result(payload, operation_id)[key] for key in ('operation_id', 'fingerprint', 'after_sha256')}:
            receipt = result(payload, operation_id)
            conn.execute('UPDATE catalog_config_operations SET state=?,receipt=? WHERE operation_id=?',
                         ('applied', json.dumps(receipt), operation_id))
            return receipt
        return {'operation_id': operation_id, 'fingerprint': fingerprint, 'target': payload['target'],
                'state': 'unconfirmed', 'after_sha256': payload['after_sha256']}


def apply(review_id, fingerprint, operation_id, registry, checkpoint=lambda: None):
    try:
        if str(UUID(operation_id)) != operation_id or str(UUID(review_id)) != review_id or not _digest.fullmatch(fingerprint):
            fail()
    except (ValueError, TypeError):
        fail()
    with _lock:
        checkpoint()
        recovered = operation(operation_id, fingerprint, registry)
        if recovered is not None:
            if recovered['state'] == 'applied':
                return recovered
            # An unknown write is never replayed just because its reply was lost.
            fail('CATALOG_APPLICATION_UNCONFIRMED')
        with closing(connect()) as conn:
            created, payload = load_review(conn, review_id, fingerprint)
            if not 0 <= time.time() - created <= 300:
                fail('CATALOG_REVIEW_EXPIRED')
            if conn.execute('SELECT COUNT(*) FROM catalog_config_operations').fetchone()[0] >= 10000:
                fail('CATALOG_OPERATION_LIMIT')
        if snapshot(payload['kind'], payload['target'], registry) != payload['before']:
            fail('CATALOG_TARGET_CHANGED')
        fresh = candidate(payload['slot'])
        if fresh['binding'] != payload['binding']:
            fail('CATALOG_REVIEW_CHANGED')
        checkpoint()
        with closing(connect()) as conn, transaction(conn, immediate=True):
            conn.execute('INSERT INTO catalog_config_operations VALUES (?,?,?,?,NULL)',
                         (operation_id, review_id, fingerprint, 'pending'))
        receipt = result(payload, operation_id)
        def authorize():
            checkpoint()
            if candidate(payload['slot'])['binding'] != payload['binding']:
                fail('CATALOG_REVIEW_CHANGED')
            checkpoint()
        if payload['kind'] == 'mcp':
            fields = {key: payload['after'][key] for key in McpServerCreateRequest.model_fields}
            marker = {key: receipt[key] for key in ('operation_id', 'fingerprint', 'after_sha256')}
            registry.apply_catalog(payload['target'][4:], McpServerCreateRequest.model_validate(fields),
                                   payload['before'], marker, authorize)
        else:
            # Use the same RuntimeConfig and SQLite CAS as the ordinary config
            # service. Configuration and receipt commit together; no downloads.
            after = RuntimeProposal.model_validate(payload['after'])
            with closing(connect()) as conn, transaction(conn, immediate=True):
                row = conn.execute('SELECT config_json FROM local_runtime_config WHERE id=1').fetchone()
                previous = RuntimeConfig.model_validate_json(row[0]) if row else RuntimeConfig()
                if previous.model_dump(mode='json') != payload['before']:
                    fail('CATALOG_TARGET_CHANGED')
                authorize()
                committed = configure_in_transaction(conn, after.model_copy(update={'version': after.version - 1}))
                if committed.model_dump(mode='json') != payload['after']:
                    fail('CATALOG_REVIEW_CHANGED')
                conn.execute('UPDATE catalog_config_operations SET state=?,receipt=? WHERE operation_id=?',
                             ('applied', json.dumps(receipt), operation_id))
            return receipt
        with closing(connect()) as conn, transaction(conn, immediate=True):
            conn.execute('UPDATE catalog_config_operations SET state=?,receipt=? WHERE operation_id=?',
                         ('applied', json.dumps(receipt), operation_id))
        return receipt
