from contextlib import closing

from app.database.db import connect, transaction


def list_queries():
    with closing(connect()) as conn:
        return [row['query'] for row in conn.execute('SELECT query FROM search_history ORDER BY id DESC LIMIT 10')]


def record(query: str):
    query = query.strip()
    if not query:
        return
    with closing(connect()) as conn, transaction(conn):
        conn.execute('DELETE FROM search_history WHERE query=?', (query,))
        conn.execute('INSERT INTO search_history(query) VALUES (?)', (query,))
        conn.execute('DELETE FROM search_history WHERE id NOT IN (SELECT id FROM search_history ORDER BY id DESC LIMIT 10)')


def clear():
    with closing(connect()) as conn, transaction(conn):
        conn.execute('DELETE FROM search_history')
