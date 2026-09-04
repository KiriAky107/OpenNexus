from fastapi.testclient import TestClient

from app.main import app
from app.services import search_history


def test_history_survives_new_clients_and_clear():
    with TestClient(app) as client:
        for query in ['first', 'second', ' first ']:
            assert client.post('/api/search', json={'query': query, 'mode': 'fts'}).status_code == 200
        assert client.get('/api/search/history').json() == {'queries': ['first', 'second']}
    with TestClient(app) as client:
        assert client.get('/api/search/history').json() == {'queries': ['first', 'second']}
        assert client.delete('/api/search/history').json() == {'queries': []}
    assert search_history.list_queries() == []


def test_history_is_bounded_and_blank_queries_are_ignored():
    for number in range(12):
        search_history.record(str(number))
    search_history.record(' ')
    assert search_history.list_queries() == [str(number) for number in range(11, 1, -1)]
