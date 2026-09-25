import asyncio
import httpx
from fastapi import FastAPI
from app.container import build_container
from app.agent import management_routes
from app.errors import ApiError, api_error_handler
from app import host_bridge


def test_management_api_review_scopes_and_start(monkeypatch):
    async def scenario():
        container = build_container()
        monkeypatch.setattr(management_routes, 'runtime', lambda: container.agent)
        app = FastAPI()
        app.include_router(management_routes.router, prefix='/api')
        app.add_exception_handler(ApiError, api_error_handler)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                config = {'name': 'Reviewer', 'provider_id': 'mock', 'model': 'mock-1', 'tools': ['system.echo']}
                created = await client.post('/api/agent/definitions', json=config)
                assert created.status_code == 200, created.text
                definition = created.json()
                result = await client.post('/api/agent/collaborations', json={'title': 'Review', 'members': [
                    {'member_id': 'a', 'agent_id': definition['id'], 'input': 'hello'}]})
                group = result.json()
                assert group['status'] == 'awaiting_confirmation'
                assert container.agent.list_runs(20, 0)[1] == 0
                path = f"/api/agent/collaborations/{group['id']}"
                token = host_bridge.vault_id.set('unrelated-vault')
                try:
                    assert (await client.get(path)).status_code == 404
                    assert (await client.post(path + '/review', json={'expected_revision': 1, 'decision': 'approve'})).status_code == 404
                    assert (await client.get('/api/agent/definitions')).json()['items'] == []
                finally:
                    host_bridge.vault_id.reset(token)
                assert (await client.post(path + '/review', json={'expected_revision': 99, 'decision': 'approve'})).status_code == 409
                approved = await client.post(path + '/review', json={'expected_revision': 1, 'decision': 'approve'})
                assert approved.status_code == 200
                await asyncio.wait_for(container.agent._coordinator.tasks[group['id']], 5)
                assert (await client.get(path)).json()['status'] == 'completed'
                assert (await client.post(path + '/review', json={'expected_revision': 1, 'decision': 'approve'})).json()['status'] == 'completed'
                assert container.agent.list_runs(20, 0)[1] == 1
                assert (await client.delete('/api/agent/definitions/' + definition['id'], params={'expected_revision': 1})).status_code == 200
                assert (await client.get(path)).json()['members'][0]['definition']['config']['name'] == 'Reviewer'
        finally:
            await container.agent.shutdown()
    asyncio.run(scenario())
