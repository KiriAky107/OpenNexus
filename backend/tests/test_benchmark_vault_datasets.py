import asyncio
import json
from uuid import uuid4

import pytest

from app.benchmarks import datasets, service
from app.config import get_settings
from app.contracts import BenchmarkKind, RAGRunRequest, SearchMode
from app.errors import ApiError


def payload(identifier='course', query='双指针', kind='rag'):
    cases = [{'case_id': 'q1', 'query': query, 'expected_note_paths': ['课程/双指针.md']}]
    if kind == 'agent':
        cases = [{'case_id': 'q1', 'prompt': query, 'output_contains': ['双指针']}]
    return json.dumps(dict(dataset_id=identifier, kind=kind, version='1', cases=cases))


def test_import_is_vault_scoped_and_survives_reload(monkeypatch, tmp_path):
    first = get_settings().vault_path
    datasets.import_dataset(payload())
    assert datasets.list_datasets(BenchmarkKind.rag)[0].scope == 'vault'
    monkeypatch.setenv('APP_VAULT_PATH', str(tmp_path / 'second-vault'))
    get_settings.cache_clear()
    assert datasets.list_datasets(BenchmarkKind.rag) == []
    with pytest.raises(ApiError, match='does not exist'):
        datasets.load_dataset('course', BenchmarkKind.rag)
    datasets.import_dataset(payload(query='项目任务'))
    assert datasets.load_dataset('course', BenchmarkKind.rag).cases[0].query == '项目任务'
    monkeypatch.setenv('APP_VAULT_PATH', str(first)); get_settings.cache_clear()
    assert datasets.load_dataset('course', BenchmarkKind.rag).cases[0].query == '双指针'
    assert datasets.export_dataset('course', BenchmarkKind.rag)['cases'][0]['expected_note_paths'] == ['课程/双指针.md']


def test_duplicate_does_not_overwrite_and_kinds_are_separate():
    original = datasets.import_dataset(payload())
    assert datasets.import_dataset(payload()).content_hash == original.content_hash
    with pytest.raises(ApiError) as error:
        datasets.import_dataset(payload(query='不同内容'))
    assert error.value.code == 'BENCHMARK_DATASET_EXISTS'
    datasets.import_dataset(payload(kind='agent'))
    assert datasets.load_dataset('course', BenchmarkKind.agent).cases[0].prompt == '双指针'


@pytest.mark.parametrize('identifier', ['../escape', 'C:/outside', 'a\\b', 'CON', 'nul', '.hidden', 'x'*81])
def test_import_rejects_unsafe_identifiers(identifier):
    with pytest.raises(ApiError) as error:
        datasets.import_dataset(payload(identifier=identifier))
    assert error.value.status_code == 422


@pytest.mark.parametrize('content', ['[]', 'null', '{}', '{', '{"kind":"unknown"}'])
def test_import_rejects_invalid_content(content):
    with pytest.raises(ApiError):
        datasets.import_dataset(content)


def test_size_limit_and_failed_validation_leave_no_dataset():
    with pytest.raises(ApiError) as error:
        datasets.import_dataset(' ' * 1048577)
    assert error.value.status_code == 413
    with pytest.raises(ApiError):
        datasets.import_dataset(json.dumps(dict(dataset_id='invalid', kind='rag', cases=[])))
    assert datasets.list_datasets(BenchmarkKind.rag) == []


def test_import_and_export_api():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        mismatch = client.post('/api/benchmarks/datasets/import', json={'content': payload(), 'expected_vault_id': 'previous-vault'})
        assert mismatch.status_code == 409
        assert datasets.list_datasets(BenchmarkKind.rag) == []
        response = client.post('/api/benchmarks/datasets/import', json={'content': payload()})
        assert response.status_code == 200
        assert response.json()['scope'] == 'vault'
        assert client.get('/api/benchmarks/datasets?kind=rag').json()['items'][0]['dataset_id'] == 'course'
        exported = client.get('/api/benchmarks/datasets/course/export?kind=rag')
        assert exported.status_code == 200
        assert exported.json() == json.loads(payload())
        assert client.get('/api/benchmarks/datasets/course/export?kind=agent').status_code == 404


def test_desktop_requires_authenticated_vault(monkeypatch):
    from app.host_bridge import vault_id
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop'); get_settings.cache_clear()
    token = vault_id.set(None)
    try:
        with pytest.raises(ApiError) as error:
            datasets.import_dataset(payload())
        assert error.value.code == 'WORKSPACE_NOT_OPEN'
        vault_id.set(str(uuid4()))
        datasets.import_dataset(payload())
        vault_id.set(str(uuid4()))
        assert datasets.list_datasets(BenchmarkKind.rag) == []
    finally:
        vault_id.reset(token)


def test_note_paths_resolve_only_in_current_index():
    from app.services import note_service
    note = asyncio.run(note_service.create_note(title='双指针', folder='课程', markdown='# 双指针\n排序后移动左右指针。', tags=[]))
    datasets.import_dataset(payload())
    dataset = datasets.load_dataset('course', BenchmarkKind.rag)
    datasets.resolve_note_paths(dataset)
    assert dataset.cases[0].expected_note_ids == [note.note_id]
    dataset.cases[0].expected_note_paths = ['其他库/双指针.md']
    with pytest.raises(ApiError) as error:
        datasets.resolve_note_paths(dataset)
    assert error.value.code == 'BENCHMARK_NOTE_NOT_INDEXED'


def test_run_records_do_not_cross_vaults(monkeypatch, tmp_path):
    from app.services import note_service
    asyncio.run(note_service.create_note(title='双指针', folder='课程', markdown='# 双指针\n排序后移动左右指针。', tags=[]))
    datasets.import_dataset(payload())
    async def run():
        created = await service.create_rag_run(RAGRunRequest(dataset_id='course', modes=[SearchMode.fts]))
        await service.wait_for_run(created.run_id)
        return created
    created = asyncio.run(run())
    assert service.get_run(created.run_id)
    assert service.get_report(created.run_id).config_snapshot['dataset_scope'] == 'vault'
    monkeypatch.setenv('APP_VAULT_PATH', str(tmp_path / 'different')); get_settings.cache_clear()
    assert service.get_run(created.run_id) is None
    assert service.get_report(created.run_id) is None
    assert service.cancel_run(created.run_id) is None
