"""Synthetic, isolated exact-search comparison; does not access the user Vault."""
import heapq
import json
import math
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import sqlite_vec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import space_index
from app.retrieval.routed_vectors import RemoteEmbeddings, _unit_vector


def main():
    rng = random.Random(42)
    count, dimensions = 4000, 384
    with tempfile.TemporaryDirectory(prefix='notes-vec-bench-') as temporary:
        conn = sqlite3.connect(Path(temporary) / 'vectors.db')
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute('CREATE TABLE blocks(block_id TEXT PRIMARY KEY, embedding_local_only INTEGER)')
        conn.execute('CREATE TABLE routed_block_vectors(space_id TEXT,block_id TEXT,dimensions INTEGER,vector TEXT,PRIMARY KEY(space_id,dimensions,block_id))')
        vectors = [_unit_vector([rng.uniform(-1, 1) for _ in range(dimensions)], dimensions) for _ in range(count)]
        conn.executemany('INSERT INTO blocks VALUES (?,0)', [(str(i),) for i in range(count)])
        conn.executemany('INSERT INTO routed_block_vectors VALUES (?,?,?,?)', [('benchmark', str(i), dimensions, json.dumps(v)) for i, v in enumerate(vectors)])
        start = time.perf_counter()
        space_index.ensure(conn, 'benchmark', dimensions)
        migration_ms = (time.perf_counter() - start) * 1000
        conn.commit()
        query = vectors[0]
        batch = RemoteEmbeddings('benchmark', dimensions, [query])
        def legacy():
            def hits():
                for row in conn.execute('SELECT block_id,vector FROM routed_block_vectors'):
                    vector = _unit_vector(json.loads(row[1]), dimensions)
                    yield row[0], max(0., min(1., math.fsum(a*b for a,b in zip(query,vector))))
            return heapq.nlargest(20, hits(), key=lambda hit:hit[1])
        def native():
            return [(hit.id,hit.score) for hit in space_index.search(conn,batch,20)]
        measurements = {}
        results = {}
        for name, operation in [('python_json_scan', legacy), ('sqlite_vec',native)]:
            elapsed = []
            for _ in range(5):
                start = time.perf_counter()
                results[name] = operation()
                elapsed.append((time.perf_counter()-start)*1000)
            measurements[name] = {'median_ms':statistics.median(elapsed), 'samples_ms':elapsed}
        assert [hit[0] for hit in results['python_json_scan']] == [hit[0] for hit in results['sqlite_vec']]
        print(json.dumps({'blocks':count,'dimensions':dimensions,'top_k':20,'migration_ms':migration_ms,
                          'same_top_k':True,'measurements':measurements},indent=2))
        conn.close()


if __name__ == '__main__':
    main()
