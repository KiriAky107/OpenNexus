import asyncio
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

from app import host_bridge, repository
from app.config import get_settings
from app.database import db
from app.contracts import SearchRequest, SearchMode
from app.retrieval.engine import engine
from app.services import desktop_notes, desktop_projection, index_service, note_service
from app.retrieval.embedding import HashEmbeddingProvider


def test_projection_isolates_same_path_and_refreshes_changed_deleted_content(tmp_path, monkeypatch):
    settings = replace(get_settings(), environment='desktop', data_dir=tmp_path, db_path=tmp_path/'global.sqlite3')
    monkeypatch.setattr(db, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    first, second = str(uuid4()), str(uuid4())
    documents = {first: {'file_id': 'file-a', 'path': 'same.md', 'content': 'uniquefirsttoken', 'created_at': 0, 'updated_at': 1},
                 second: {'file_id': 'file-b', 'path': 'same.md', 'content': 'uniquesecondtoken', 'created_at': 0, 'updated_at': 1}}
    def call(method, **params):
        doc = documents.get(host_bridge.vault_id.get())
        if doc: doc['hash'] = sha256(doc['content'].encode()).hexdigest()
        if method == 'list': return {'items': [doc] if doc else [], 'total': int(doc is not None)}
        assert method == 'read' and doc['file_id'] == params['file_id']
        return doc
    monkeypatch.setattr(desktop_notes, 'call', call)
    def search(vault, query):
        token = host_bridge.vault_id.set(vault)
        try: return asyncio.run(engine.search(SearchRequest(query=query, mode=SearchMode.fts)))
        finally: host_bridge.vault_id.reset(token)
    assert 'file-a' in search(first, 'uniquefirsttoken').model_dump_json()
    assert 'file-a' not in search(second, 'uniquefirsttoken').model_dump_json()
    assert 'file-b' in search(second, 'uniquesecondtoken').model_dump_json()
    documents[first]['content'] = 'replacementtoken'
    assert 'file-a' not in search(first, 'uniquefirsttoken').model_dump_json()
    assert 'file-a' in search(first, 'replacementtoken').model_dump_json()
    del documents[first]
    assert 'file-a' not in search(first, 'replacementtoken').model_dump_json()
    assert (tmp_path/'vault-state'/first/'core.sqlite3').is_file()
    assert (tmp_path/'vault-state'/second/'core.sqlite3').is_file()
    from app.services import task_service
    token = host_bridge.vault_id.set(second)
    try:
        task = task_service.create_task(title='Scoped task', note_id='file-b')
        assert task.note_id == 'file-b'
    finally:
        host_bridge.vault_id.reset(token)
    token = host_bridge.vault_id.set(first)
    try:
        assert task_service.get_task(task.task_id) is None
    finally:
        host_bridge.vault_id.reset(token)


def test_desktop_semantic_rebuild_preserves_host_file_id(tmp_path, monkeypatch):
    settings = replace(get_settings(), environment='desktop', data_dir=tmp_path, db_path=tmp_path/'global.sqlite3')
    monkeypatch.setattr(db, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setattr(index_service, 'get_settings', lambda: settings)
    document = {'file_id': 'stable-host-id', 'path': 'same.md', 'hash': sha256(b'test note').hexdigest(),
                'content': 'test note', 'created_at': 0, 'updated_at': 1}
    monkeypatch.setattr(desktop_notes, 'call', lambda method, **params: {'items': [document], 'total': 1} if method == 'list' else document)
    monkeypatch.setattr(note_service, 'embedding', HashEmbeddingProvider())
    async def no_remote(*args, **kwargs): return None
    monkeypatch.setattr('app.retrieval.routed_vectors.embed_remote', no_remote)
    from app.contracts import IndexRebuildRequest
    token = host_bridge.vault_id.set(str(uuid4()))
    try:
        result = asyncio.run(index_service.rebuild(IndexRebuildRequest()))
        assert result.status == 'completed'
        assert repository.get_note_record('stable-host-id') is not None
        assert [record.note_id for record in repository.list_note_locations()] == ['stable-host-id']
    finally:
        host_bridge.vault_id.reset(token)


def test_host_identity_adoption_keeps_existing_task_links(tmp_path, monkeypatch):
    settings = replace(get_settings(), environment='desktop', data_dir=tmp_path, db_path=tmp_path/'global.sqlite3')
    monkeypatch.setattr(db, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    document = {'file_id': 'before-merge', 'path': 'same.md', 'hash': sha256(b'test').hexdigest(), 'content': 'test', 'created_at': 0, 'updated_at': 1}
    monkeypatch.setattr(desktop_notes, 'call', lambda method, **params: {'items': [document], 'total': 1} if method == 'list' else document)
    from app.services import task_service
    token = host_bridge.vault_id.set(str(uuid4()))
    try:
        task = task_service.create_task(title='Preserve link', note_id='before-merge')
        document['file_id'] = 'after-merge'
        document['aliases'] = ['before-merge']
        asyncio.run(desktop_projection.refresh())
        assert task_service.get_task(task.task_id).note_id == 'after-merge'
        assert repository.get_note_record('before-merge') is None
        assert repository.get_note_record('after-merge') is not None
    finally:
        host_bridge.vault_id.reset(token)
