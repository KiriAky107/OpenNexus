"""Read-modify-write operations must not deadlock with the Trace writer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import time

from app.database.db import connect, transaction


def test_parallel_read_modify_write_transactions_are_serialized():
    with closing(connect()) as conn:
        conn.execute('CREATE TABLE qa_counter (value INTEGER NOT NULL)')
        conn.execute('INSERT INTO qa_counter VALUES (0)')

    def increment(_):
        with closing(connect()) as conn, transaction(conn, immediate=True):
            value = conn.execute('SELECT value FROM qa_counter').fetchone()[0]
            time.sleep(.01)
            conn.execute('UPDATE qa_counter SET value=?', (value + 1,))

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(increment, range(12)))
    with closing(connect()) as conn:
        assert conn.execute('SELECT value FROM qa_counter').fetchone()[0] == 12
