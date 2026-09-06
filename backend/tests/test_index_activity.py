import asyncio
from types import SimpleNamespace

import pytest

from app.retrieval import activity
from app.services import index_service


@pytest.fixture(autouse=True)
def reset_activity(monkeypatch):
    for field in ('active', 'completed', 'failed', 'cancelled'):
        monkeypatch.setattr(activity, field, 0)
    monkeypatch.setattr(index_service, '_active_job_id', None)
    monkeypatch.setattr(index_service, '_active_scope', None)
    monkeypatch.setattr(index_service, '_last_error', None)
    monkeypatch.setattr(index_service.repository, 'stats', lambda: {'notes': 16, 'blocks': 3787})
    monkeypatch.setattr(index_service.repository, 'get_index_meta', lambda: {})


def test_pending_rebuild_and_incremental_jobs(monkeypatch):
    meta = {'workspace_vectors_pending': '1', 'note_vectors_pending:a': '1', 'note_vectors_pending:b': '1'}
    monkeypatch.setattr(index_service.repository, 'get_index_meta', lambda: meta)
    status = index_service.get_status()
    assert status.vector_refresh_required and status.pending_jobs == 1
    assert status.running_jobs == 0
    monkeypatch.setattr(index_service, '_active_job_id', 'job_test')
    monkeypatch.setattr(index_service, '_active_scope', 'all')
    status = index_service.get_status()
    assert (status.status, status.pending_jobs, status.running_jobs) == ('running', 1, 1)
    monkeypatch.setattr(index_service, '_active_scope', 'note')
    assert index_service.get_status().pending_jobs == 2
    meta.pop('workspace_vectors_pending')
    assert index_service.get_status().pending_jobs == 2
    monkeypatch.setattr(index_service, '_active_job_id', None)
    monkeypatch.setattr(index_service, '_last_error', 'failed')
    status = index_service.get_status()
    assert (status.status, status.pending_jobs, status.running_jobs) == ('failed', 2, 0)
    meta.clear()
    assert index_service.get_status().pending_jobs == 0


def test_search_activity_covers_completion_failure_and_cancellation():
    async def scenario():
        gate = asyncio.Event()

        @activity.track_search
        async def search(_self, request):
            await gate.wait()
            if request.query == 'fail':
                raise ValueError('failure')
            return 'ok'

        tasks = [asyncio.create_task(search(None, SimpleNamespace(mode=mode, query=query)))
                 for mode, query in [('vector', 'ok'), ('hybrid', 'fail'), ('vector', 'cancel'), ('fts', 'ok')]]
        await asyncio.sleep(0)
        assert index_service.get_status().active_searches == 3
        tasks[2].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[2]
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert results[0] == results[3] == 'ok'
        status = index_service.get_status()
        assert (status.active_searches, status.completed_searches, status.failed_searches, status.cancelled_searches) == (0, 1, 1, 1)
        assert status.pending_jobs == 0

    asyncio.run(scenario())
