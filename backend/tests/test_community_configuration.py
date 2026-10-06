from contextlib import closing
import json
from uuid import uuid4

import pytest

from app import host_bridge
from app.agent.tools import ToolRegistry
from app.config import get_settings
from app.contracts import McpServerCreateRequest, McpServerUpdateRequest
from app.database.db import connect
from app.errors import ApiError
from app.extensions.mcp_registry import McpRegistryError, McpServerRegistry
from app.local_models.catalog import CATALOG
from app.local_models.runtime import configuration, configure
from app.providers.credentials import EncryptedCredentialStore
from app.services import community_configuration as service

SLOT = 'a' * 64


class Bridge:
    def __init__(self, kind='mcp'):
        self.calls = []
        self.error = None
        self.material = {'vault_id': str(uuid4()), 'slot': SLOT, 'kind': kind, 'binding': 'b' * 64,
                         'package_name': 'Reviewed MCP', 'package_version': '1.0.0',
                         'manifest': {'transport': 'stdio', 'command': 'python', 'args': ['server.py']}}

    def call(self, method, **params):
        self.calls.append((method, params))
        assert method == 'catalog.configuration_candidate'
        assert params == {'vault_id': self.material['vault_id'], 'slot': SLOT}
        if self.error:
            raise RuntimeError(self.error)
        return json.loads(json.dumps(self.material))


@pytest.fixture
def owned(monkeypatch):
    bridge = Bridge()
    monkeypatch.setattr(host_bridge, 'active', bridge)
    token = host_bridge.vault_id.set(bridge.material['vault_id'])
    registry = McpServerRegistry(ToolRegistry(), EncryptedCredentialStore(), get_settings().data_dir,
                                 allow_process_launch=False)
    yield bridge, registry
    registry.shutdown()
    host_bridge.vault_id.reset(token)


def apply(review, registry, operation_id=None):
    return service.apply(review['review_id'], review['fingerprint'], operation_id or str(uuid4()), registry)


def model(bridge):
    spec = CATALOG['granite']
    bridge.material.update(kind='model', package_name='Pinned embedding plan', manifest={
        'source': spec.repository, 'revision': spec.revision, 'license': spec.license,
        'resources': {'ram_gb': 8}, 'verified_platforms': ['windows', 'linux'],
        'model_key': spec.key, 'runtime_config': {'embedding_model': spec.key, 'cpu_threads': 4}})


def test_create_is_reviewed_disabled_and_receipt_survives_registry_restart(owned):
    bridge, registry = owned
    options = service.targets(SLOT, registry)
    assert options['items'][0]['id'] == 'mcp:new'
    review = service.preview(SLOT, 'mcp:new', registry)
    assert review['before'] is None and registry.list() == []
    assert 'binding' not in review and review['after']['enabled'] is False
    receipt = apply(review, registry)
    assert receipt['state'] == 'applied' and receipt['after_sha256'] == service.digest(review['after'])
    current = registry.get(review['target'][4:])
    assert current.enabled is False and current.trusted is False and current.tools_count == 0
    assert len(bridge.calls) >= 5
    restored = McpServerRegistry(ToolRegistry(), registry.credentials, registry.data_dir, allow_process_launch=False)
    assert service.operation(receipt['operation_id'], review['fingerprint'], restored) == receipt
    assert apply(review, restored, receipt['operation_id']) == receipt
    assert len(restored.list()) == 1
    restored.shutdown()


def test_existing_target_retains_secrets_and_still_requires_independent_trust(owned):
    bridge, registry = owned
    previous = registry.create(McpServerCreateRequest(name='Old', command='old-python',
                                                    secret_environment_keys=['API_KEY']))
    registry.put_secret(previous.server_id, 'API_KEY', 'synthetic-private-value')
    registry.trust(previous.server_id, previous.command_digest)
    review = service.preview(SLOT, 'mcp:' + previous.server_id, registry)
    assert review['before']['version'] == 1 and review['after']['secret_environment_keys'] == ['API_KEY']
    assert 'synthetic-private-value' not in json.dumps(review)
    receipt = apply(review, registry)
    current = registry.get(previous.server_id)
    assert current.version == 2 and not current.trusted and not current.enabled
    assert current.secret_environment == {'API_KEY': True}
    with pytest.raises(McpRegistryError) as error:
        registry.enable(current.server_id)
    assert error.value.code == 'MCP_SANDBOX_REQUIRED'
    registry.allow_process_launch = True
    with pytest.raises(McpRegistryError) as error:
        registry.enable(current.server_id)
    assert error.value.code == 'MCP_TRUST_APPROVAL_REQUIRED'
    # Ordinary edits retain the exact receipt, without rolling their newer settings back.
    edited = {key: value for key, value in review['after'].items() if key in McpServerUpdateRequest.model_fields}
    edited['name'] = 'Later human edit'
    registry.update(current.server_id, McpServerUpdateRequest(**edited))
    assert service.operation(receipt['operation_id'], review['fingerprint'], registry) == receipt
    assert registry.get(current.server_id).name == 'Later human edit'


def test_target_revision_conflict_never_overwrites_human_edit(owned):
    bridge, registry = owned
    old = registry.create(McpServerCreateRequest(name='Original', command='python'))
    review = service.preview(SLOT, 'mcp:' + old.server_id, registry)
    registry.update(old.server_id, McpServerUpdateRequest(name='Human', command='python', version=1))
    with pytest.raises(ApiError) as error:
        apply(review, registry)
    assert error.value.code == 'CATALOG_TARGET_CHANGED'
    assert registry.get(old.server_id).name == 'Human'


@pytest.mark.parametrize('change', ['binding', 'revoked', 'vault', 'expired', 'fingerprint'])
def test_stale_package_vault_or_review_never_reaches_configuration(owned, change):
    bridge, registry = owned
    review = service.preview(SLOT, 'mcp:new', registry)
    if change == 'binding': bridge.material['binding'] = 'c' * 64
    if change == 'revoked': bridge.error = 'EXTENSION_KEY_REVOKED'
    if change == 'vault': host_bridge.vault_id.set(str(uuid4()))
    if change == 'expired':
        with closing(connect()) as conn:
            conn.execute('UPDATE catalog_config_reviews SET created=0')
    if change == 'fingerprint': review['fingerprint'] = 'd' * 64
    with pytest.raises(ApiError): apply(review, registry)
    assert registry.list() == []


def test_lost_reply_after_atomic_mcp_write_is_recovered_without_reapplying(owned, monkeypatch):
    bridge, registry = owned
    review = service.preview(SLOT, 'mcp:new', registry)
    operation_id = str(uuid4())
    original = registry.apply_catalog
    def crash(*args):
        original(*args)
        raise RuntimeError('owned crash before SQLite acknowledgment')
    monkeypatch.setattr(registry, 'apply_catalog', crash)
    with pytest.raises(RuntimeError): apply(review, registry, operation_id)
    restored = McpServerRegistry(ToolRegistry(), registry.credentials, registry.data_dir, allow_process_launch=False)
    receipt = service.operation(operation_id, review['fingerprint'], restored)
    assert receipt['state'] == 'applied' and len(restored.list()) == 1
    assert apply(review, restored, operation_id) == receipt
    assert restored.get(review['target'][4:]).version == 1
    restored.shutdown()


def test_failed_mcp_write_remains_unconfirmed_and_is_never_automatically_replayed(owned, monkeypatch):
    bridge, registry = owned
    review = service.preview(SLOT, 'mcp:new', registry)
    operation_id = str(uuid4())
    def fail(_): raise McpRegistryError('MCP_STORAGE_ERROR', 'Owned injected write failure')
    monkeypatch.setattr(registry, '_write', fail)
    with pytest.raises(McpRegistryError): apply(review, registry, operation_id)
    assert service.operation(operation_id, review['fingerprint'], registry)['state'] == 'unconfirmed'
    with pytest.raises(ApiError) as error: apply(review, registry, operation_id)
    assert error.value.code == 'CATALOG_APPLICATION_UNCONFIRMED' and registry.list() == []


@pytest.mark.parametrize('manifest', [
    {'transport': 'stdio', 'command': 'python', 'args': [], 'environment': {'API_KEY': 'private'}},
    {'transport': 'streamable_http', 'url': 'https://example.test/mcp', 'headers': {'Authorization': 'private'}},
    {'transport': 'stdio', 'command': 'python', 'args': [], 'enabled': True},
    {'transport': 'stdio', 'command': 'python', 'args': [], 'surprise': 'ignored?'}])
def test_package_cannot_inject_credentials_enablement_or_unknown_config_fields(owned, manifest):
    bridge, registry = owned
    bridge.material['manifest'] = manifest
    with pytest.raises(ApiError): service.preview(SLOT, 'mcp:new', registry)
    assert registry.list() == []


def test_model_plan_commits_real_runtime_config_and_receipt_without_downloading(owned):
    bridge, registry = owned
    model(bridge)
    old = configuration()
    review = service.preview(SLOT, 'model:local_runtime', registry)
    assert configuration() == old
    receipt = apply(review, registry)
    updated = configuration()
    assert updated.embedding_model == 'granite' and updated.cpu_threads == 4 and updated.version == old.version + 1
    assert not (get_settings().data_dir / 'models').exists()
    assert service.operation(receipt['operation_id'], review['fingerprint'], registry) == receipt
    assert apply(review, registry, receipt['operation_id']) == receipt
    assert configuration().version == updated.version


@pytest.mark.parametrize('change', ['pin', 'unknown-field', 'invalid-budget', 'model-key', 'legacy'])
def test_model_proposal_must_match_pins_and_existing_runtime_validation(owned, change):
    bridge, registry = owned
    model(bridge)
    manifest = bridge.material['manifest']
    if change == 'pin': manifest['revision'] = 'floating-main'
    if change == 'unknown-field': manifest['runtime_config']['download'] = True
    if change == 'invalid-budget': manifest['runtime_config']['memory_limit_mb'] = 1
    if change == 'model-key': manifest['model_key'] = 'unreviewed-model'
    if change == 'legacy': manifest.pop('runtime_config')
    old = configuration()
    with pytest.raises(ApiError): service.preview(SLOT, 'model:local_runtime', registry)
    assert configuration() == old and not (get_settings().data_dir / 'models').exists()


def test_model_cas_and_review_vault_protect_current_settings(owned):
    bridge, registry = owned
    model(bridge)
    review = service.preview(SLOT, 'model:local_runtime', registry)
    human = configure(configuration().model_copy(update={'cpu_threads': 7}))
    with pytest.raises(ApiError) as error: apply(review, registry)
    assert error.value.code == 'CATALOG_TARGET_CHANGED' and configuration() == human


def test_receipt_is_bound_to_exact_operation_review_and_vault(owned):
    bridge, registry = owned
    review = service.preview(SLOT, 'mcp:new', registry)
    receipt = apply(review, registry)
    assert service.operation(str(uuid4()), review['fingerprint'], registry) is None
    with pytest.raises(ApiError): service.operation(receipt['operation_id'], 'e' * 64, registry)
    host_bridge.vault_id.set(str(uuid4()))
    with pytest.raises(ApiError): service.operation(receipt['operation_id'], review['fingerprint'], registry)


@pytest.mark.parametrize('kind', ['mcp', 'model'])
def test_cancellation_during_final_host_check_does_not_write_config(owned, kind):
    bridge, registry = owned
    if kind == 'model': model(bridge)
    review = service.preview(SLOT, 'mcp:new' if kind == 'mcp' else 'model:local_runtime', registry)
    cancelled = False
    original = bridge.call
    def cancelling_call(*args, **kwargs):
        nonlocal cancelled
        response = original(*args, **kwargs)
        if len(bridge.calls) >= 3: cancelled = True
        return response
    bridge.call = cancelling_call
    def checkpoint():
        if cancelled: service.fail('REQUEST_CANCELLED')
    operation_id = str(uuid4())
    with pytest.raises(ApiError) as error:
        service.apply(review['review_id'], review['fingerprint'], operation_id, registry, checkpoint)
    assert error.value.code == 'REQUEST_CANCELLED'
    assert registry.list() == []
    if kind == 'model': assert configuration().model_dump(mode='json') == review['before']
    assert service.operation(operation_id, review['fingerprint'], registry)['state'] == 'unconfirmed'


def test_http_routes_use_native_candidate_and_reject_client_supplied_manifests(owned, monkeypatch):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from app import community_configuration_routes
    from app.main import app
    bridge, registry = owned
    monkeypatch.setattr(community_configuration_routes, 'container', SimpleNamespace(mcp_servers=registry))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.test') as client:
            invalid = await client.post('/api/community/configurations/preview', json={
                'slot': SLOT, 'target': 'mcp:new', 'manifest': {'command': 'injected'}})
            assert invalid.status_code == 422 and bridge.calls == []
            targets = await client.get('/api/community/configurations/targets', params={'slot': SLOT})
            assert targets.status_code == 200 and targets.json()['kind'] == 'mcp'
            response = await client.post('/api/community/configurations/preview', json={'slot': SLOT, 'target': 'mcp:new'})
            assert response.status_code == 200
            review = response.json()
            operation_id = str(uuid4())
            applied = await client.post('/api/community/configurations/apply', json={
                'review_id': review['review_id'], 'fingerprint': review['fingerprint'], 'operation_id': operation_id})
            assert applied.status_code == 200 and applied.json()['state'] == 'applied'
            restored = await client.get('/api/community/configurations/operations/' + operation_id,
                                        params={'fingerprint': review['fingerprint']})
            assert restored.status_code == 200 and restored.json() == applied.json()
    asyncio.run(run())


def test_runtime_config_and_receipt_roll_back_together_if_commit_pipeline_fails(owned, monkeypatch):
    bridge, registry = owned
    model(bridge)
    review = service.preview(SLOT, 'model:local_runtime', registry)
    original = service.configure_in_transaction
    def failed_commit(*args):
        original(*args)
        raise RuntimeError('owned failure after config SQL before receipt commit')
    monkeypatch.setattr(service, 'configure_in_transaction', failed_commit)
    operation_id = str(uuid4())
    with pytest.raises(RuntimeError): apply(review, registry, operation_id)
    assert configuration().model_dump(mode='json') == review['before']
    assert service.operation(operation_id, review['fingerprint'], registry)['state'] == 'unconfirmed'


def test_shared_runtime_configuration_cas_serializes_concurrent_human_writes(owned):
    from concurrent.futures import ThreadPoolExecutor
    initial = configuration()
    def write(threads):
        try:
            return configure(initial.model_copy(update={'cpu_threads': threads}))
        except ApiError as exc:
            assert exc.code == 'VERSION_CONFLICT'
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, [3,4,5,6]))
    successes = [value for value in results if value is not None]
    assert len(successes) == 1 and configuration() == successes[0]


@pytest.mark.parametrize('field,key', [('environment','ACCESS_TOKEN'),('headers','Authorization')])
def test_legacy_plain_secrets_are_never_copied_to_review_or_response(owned, field, key):
    bridge, registry = owned
    data = {'name':'Legacy',field:{key:'synthetic-existing-private'}}
    data.update({'command':'python'} if field=='environment' else
                {'transport':'streamable_http','url':'https://example.test/mcp'})
    current = registry.create(McpServerCreateRequest(**data))
    with pytest.raises(ApiError) as error: service.preview(SLOT, 'mcp:' + current.server_id, registry)
    assert error.value.code == 'CATALOG_PLAINTEXT_SECRET'
    assert 'synthetic-existing-private' not in error.value.message
    with closing(connect()) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='catalog_config_reviews'").fetchone() is None
