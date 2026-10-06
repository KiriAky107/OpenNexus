"""Proposal switches cannot grant execution consent or widen other permissions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import httpx
import pytest
from fastapi import FastAPI

from app.agent.permissions import PermissionPolicy, PermissionMode
from app.container import build_container
from app.database.db import connect
from app.errors import ApiError, api_error_handler
from app.services import experiment_permissions as prefs


def test_device_preferences_default_deny_and_restore_only_explicit_proposals():
    policy = PermissionPolicy()
    assert prefs.read() == {'revision': 0, 'rules': {'experiments.run': 'deny', 'experiments.import': 'deny'}}
    state = prefs.update(prefs.Update(permission='experiments.run', mode='confirm', expected_revision=0), policy)
    assert state['revision'] == 1 and policy.mode_for('experiments.run') == PermissionMode.confirm
    assert policy.mode_for('experiments.import') == PermissionMode.deny
    assert build_container().permissions.mode_for('experiments.run') == PermissionMode.confirm
    with pytest.raises(ApiError) as stale:
        prefs.update(prefs.Update(permission='experiments.import', mode='confirm', expected_revision=0), policy)
    assert stale.value.code == 'EXPERIMENT_PERMISSION_SETTINGS_CHANGED'
    prefs.update(prefs.Update(permission='experiments.run', mode='deny', expected_revision=1), policy)
    assert policy.mode_for('experiments.run') == PermissionMode.deny
    assert build_container().permissions.mode_for('experiments.run') == PermissionMode.deny


def test_concurrent_preference_changes_do_not_overwrite_each_other():
    policy = PermissionPolicy()
    def save(permission):
        try:
            return prefs.update(prefs.Update(permission=permission, mode='confirm', expected_revision=0), policy)
        except ApiError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, prefs.PERMISSIONS))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 'EXPERIMENT_PERMISSION_SETTINGS_CHANGED' in results
    state = prefs.read()
    assert state['revision'] == 1
    assert all(policy.mode_for(key).value == value for key, value in state['rules'].items())


def test_failed_save_or_invalid_stored_rules_never_enable_execution():
    policy = PermissionPolicy()
    with closing(connect()) as conn:
        conn.execute("CREATE TRIGGER block_permission_save BEFORE INSERT ON experiment_permission_policy BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(Exception, match='fixture'):
        prefs.update(prefs.Update(permission='experiments.run', mode='confirm', expected_revision=0), policy)
    assert policy.mode_for('experiments.run') == PermissionMode.deny and prefs.read()['revision'] == 0
    with closing(connect()) as conn:
        conn.execute('DROP TRIGGER block_permission_save')
        conn.execute('INSERT INTO experiment_permission_policy VALUES(1,1,?)', ('{"experiments.run":"allow","experiments.import":"confirm"}',))
    restored = PermissionPolicy()
    prefs.restore(restored)
    assert all(restored.mode_for(key) == PermissionMode.deny for key in prefs.PERMISSIONS)
    with pytest.raises(ApiError) as invalid: prefs.read()
    assert invalid.value.code == 'EXPERIMENT_PERMISSION_SETTINGS_INVALID'
    with closing(connect()) as conn:
        assert '"allow"' in conn.execute('SELECT rules FROM experiment_permission_policy').fetchone()['rules']


def test_permission_settings_api_is_strict_and_separate_from_native_execution(monkeypatch):
    from app import routes
    container = build_container()
    monkeypatch.setattr(routes, 'container', container)
    app = FastAPI()
    app.include_router(routes.router)
    app.add_exception_handler(ApiError, api_error_handler)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            path = '/api/permissions/experiments'
            assert (await client.get(path)).json()['revision'] == 0
            base = {'permission':'experiments.run', 'mode':'confirm', 'expected_revision':0}
            for extra in [{'mode':'allow'}, {'permission':'notes.write'}, {'approved':True}, {'expected_revision':False}, {'command':'python'}]:
                assert (await client.put(path, json={**base, **extra})).status_code == 422
            assert (await client.put(path, json=base)).json()['rules']['experiments.run'] == 'confirm'
            assert container.permissions.mode_for('experiments.import') == PermissionMode.deny
            assert not any(definition.name.startswith('permissions.') for definition in container.tools.definitions())
            assert (await client.put(path, json={**base, 'mode':'deny'})).status_code == 409
            assert (await client.get('/api/permissions/policy')).json()['experiments.run'] == 'confirm'
        await container.agent.shutdown()
    asyncio.run(scenario())
