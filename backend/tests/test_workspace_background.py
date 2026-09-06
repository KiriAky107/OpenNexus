import asyncio

from app import repository
from app.config import get_settings
from app.services import index_service, workspace_service


def test_external_new_note_does_not_rebuild_existing_notes(monkeypatch):
    from app.services import note_service
    async def scenario():
        await note_service.create_note(title='Existing', markdown='Keep existing vectors', folder=None, tags=[])
        calls = []
        original = index_service.prepare_note_index
        async def record(parsed, **kwargs):
            calls.append(parsed.file_path)
            return await original(parsed, **kwargs)
        async def forbidden(*args, **kwargs):
            raise AssertionError('full rebuild should not run')
        monkeypatch.setattr(index_service, 'prepare_note_index', record)
        monkeypatch.setattr(index_service, 'rebuild', forbidden)
        path = get_settings().vault_path / 'external.md'
        path.write_text('# External\n\nNew content', encoding='utf-8')
        try:
            await workspace_service.refresh_workspace_tree()
            await index_service._background_task
            assert calls == ['external.md']
            assert not index_service.get_status().vector_refresh_required
            await workspace_service.open_workspace(None)
            assert calls == ['external.md']
        finally:
            await index_service.shutdown()
    asyncio.run(scenario())


def test_open_returns_before_vectors_and_deduplicates_background(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        original = index_service.prepare_note_index
        calls = 0
        async def slow(*args, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(index_service, 'prepare_note_index', slow)
        vault = get_settings().vault_path
        vault.mkdir(parents=True, exist_ok=True)
        (vault / 'demo.md').write_text('# Demo\n\nsearchable content', encoding='utf-8')
        try:
            snapshot = await asyncio.wait_for(workspace_service.open_workspace(None), 1)
            assert snapshot.items[0].note_id
            await asyncio.wait_for(started.wait(), 1)
            task = index_service._background_task
            await asyncio.wait_for(workspace_service.open_workspace(None), 1)
            assert index_service._background_task is task
            assert index_service.get_status().status == 'running'
            # A mutation still completes while the model is waiting.
            await asyncio.wait_for(workspace_service.create_folder('/', 'new-folder'), 1)
            assert repository.list_note_locations()[0].note_id == snapshot.items[0].note_id
            release.set()
            await asyncio.wait_for(task, 2)
            assert calls == 1
            assert not index_service.get_status().vector_refresh_required
        finally:
            release.set()
            await index_service.shutdown()
    asyncio.run(scenario())


def test_background_retries_changed_snapshot_without_overwriting(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        original = index_service.prepare_note_index
        calls = 0
        async def slow(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(index_service, 'prepare_note_index', slow)
        vault = get_settings().vault_path
        vault.mkdir(parents=True, exist_ok=True)
        path = vault / 'demo.md'
        path.write_text('# Before\n\nold', encoding='utf-8')
        try:
            await workspace_service.open_workspace(None)
            await asyncio.wait_for(started.wait(), 1)
            path.write_text('# After\n\nnew', encoding='utf-8')
            release.set()
            await asyncio.wait_for(index_service._background_task, 4)
            assert calls == 2
            assert repository.list_note_locations()[0].title == 'After'
            assert not index_service.get_status().vector_refresh_required
        finally:
            release.set()
            await index_service.shutdown()
    asyncio.run(scenario())


def test_save_returns_while_vectors_wait_and_latest_revision_wins(monkeypatch):
    from app.services import note_service
    async def scenario():
        note = await note_service.create_note(title='Draft', markdown='# Draft\n\ninitial', folder=None, tags=[])
        started, release = asyncio.Event(), asyncio.Event()
        original = index_service.prepare_note_index
        calls = 0
        async def slow(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(index_service, 'prepare_note_index', slow)
        try:
            await asyncio.wait_for(note_service.update_note(note.note_id, markdown='# First\n\none', defer_vectors=True), 1)
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(note_service.update_note(note.note_id, title='Custom title', tags=['kept'], markdown='# Latest\n\ntwo', defer_vectors=True), 1)
            assert (await note_service.get_note(note.note_id)).markdown == '# Latest\n\ntwo'
            assert index_service.get_status().vector_refresh_required
            release.set()
            await asyncio.wait_for(index_service._background_task, 3)
            current = repository.get_note_record(note.note_id)
            assert current.title == 'Custom title'
            assert current.tags == ['kept']
            assert calls == 2
            assert not index_service.get_status().vector_refresh_required
        finally:
            release.set()
            await index_service.shutdown()
    asyncio.run(scenario())


def test_failed_vectors_do_not_undo_save_and_pending_work_can_resume(monkeypatch):
    from app.services import note_service
    async def scenario():
        note = await note_service.create_note(title='Draft', markdown='# Draft', folder=None, tags=[])
        original = index_service.prepare_note_index
        async def fail(*args, **kwargs):
            raise RuntimeError('model unavailable')
        monkeypatch.setattr(index_service, 'prepare_note_index', fail)
        try:
            await note_service.update_note(note.note_id, markdown='# Saved', defer_vectors=True)
            await index_service._background_task
            assert (await note_service.get_note(note.note_id)).markdown == '# Saved'
            assert index_service.get_status().status == 'failed'
            assert index_service.get_status().vector_refresh_required
            await index_service.shutdown()
            monkeypatch.setattr(index_service, 'prepare_note_index', original)
            await workspace_service.open_workspace(None)
            await index_service._background_task
            assert not index_service.get_status().vector_refresh_required
        finally:
            await index_service.shutdown()
    asyncio.run(scenario())
