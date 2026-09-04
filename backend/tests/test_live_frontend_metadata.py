import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.container import container
from app.agent.permissions import PermissionMode
from app.services.note_service import create_note


def test_index_status_returns_real_counts():
    with TestClient(app) as client:
        initial = client.get('/api/index/status').json()
        assert (initial['total_notes'], initial['total_blocks']) == (0, 0)
        note = asyncio.run(create_note(title='Real note', markdown='# Real note\n\ncontent', folder=None, tags=[]))
        result = client.get('/api/index/status').json()
        assert result['total_notes'] == 1
        assert result['total_blocks'] == len(note.blocks)


def test_permissions_endpoint_reads_effective_backend_policy():
    policy = container.permissions.policy
    original = policy.mode_for('attachments.read')
    try:
        policy.set_rule('attachments.read', PermissionMode.deny)
        with TestClient(app) as client:
            response = client.get('/api/permissions/policy')
        assert response.status_code == 200
        assert response.json()['attachments.read'] == 'deny'
    finally:
        policy.set_rule('attachments.read', original)
