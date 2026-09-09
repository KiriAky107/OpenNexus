"""从持久路由向量派生的持久 vec0 索引，每个空间/维度一个。"""
import hashlib
import json
import threading

import sqlite_vec

from app.retrieval.vectorstore import VectorHit


_migration_lock = threading.Lock()


def is_ready(conn, batches):
    return all(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',
               (table_name(batch.space_id, batch.dimensions),)).fetchone() for batch in batches)


def prepare(conn, batches):
    """打开搜索快照前完成延迟写入；索引预热后的搜索不再写入。"""
    from app.retrieval.routed_vectors import _ensure_table
    batches = list(batches)
    if is_ready(conn, batches):
        return
    # 等待不保留任何读取事务，因此可以提交并发迁移。
    with _migration_lock:
        if is_ready(conn, batches):
            return
        conn.execute('BEGIN IMMEDIATE')
        try:
            _ensure_table(conn)
            for batch in batches:
                ensure(conn, batch.space_id, batch.dimensions)
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise


def table_name(space, dimensions):
    return 'routed_vec_' + hashlib.sha256(json.dumps([space, dimensions]).encode()).hexdigest()


def ensure(conn, space, dimensions):
    from app.retrieval.routed_vectors import _unit_vector
    table = table_name(space, dimensions)
    if conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
        return table
    if type(dimensions) is not int or not 0 < dimensions <= 8192:
        raise ValueError('unsupported vector dimensions')
    conn.execute(f'CREATE VIRTUAL TABLE {table} USING vec0(block_id TEXT PRIMARY KEY, embedding float[{dimensions}], local_only INTEGER)')
    for row in conn.execute('SELECT r.block_id,r.vector,b.embedding_local_only FROM routed_block_vectors r JOIN blocks b USING(block_id) WHERE r.space_id=? AND r.dimensions=?', (space, dimensions)):
        conn.execute(f'INSERT INTO {table}(block_id,embedding,local_only) VALUES (?,?,?)',
                     (row[0], sqlite_vec.serialize_float32(_unit_vector(json.loads(row[1]), dimensions)), row[2]))
    literal = conn.execute('SELECT quote(?)', (space,)).fetchone()[0]
    for event in ('DELETE', 'UPDATE'):
        conn.execute(f'''CREATE TRIGGER {table}_{event.lower()} AFTER {event} ON routed_block_vectors
            WHEN old.space_id={literal} AND old.dimensions={dimensions}
            BEGIN DELETE FROM {table} WHERE block_id=old.block_id; END''')
    return table


def upsert(conn, block_ids, batch):
    from app.retrieval.routed_vectors import _unit_vector
    table = ensure(conn, batch.space_id, batch.dimensions)
    for block_id, vector in zip(block_ids, batch.vectors):
        conn.execute(f'DELETE FROM {table} WHERE block_id=?', (block_id,))
        conn.execute(f'INSERT INTO {table}(block_id,embedding,local_only) SELECT block_id,?,embedding_local_only FROM blocks WHERE block_id=?',
                     (sqlite_vec.serialize_float32(_unit_vector(vector, batch.dimensions)), block_id))


def search(conn, batch, top_k, policy=None):
    table = table_name(batch.space_id, batch.dimensions)
    # 覆盖范围检查保持相关性；热路径上没有 JSON 解码或 Python 点积。
    where = '' if policy is None else ' AND b.embedding_local_only=?'
    params = () if policy is None else (int(policy),)
    missing = conn.execute(f'''SELECT 1 FROM blocks b LEFT JOIN routed_block_vectors r
        ON r.block_id=b.block_id AND r.space_id=? AND r.dimensions=?
        WHERE r.block_id IS NULL{where} LIMIT 1''', (batch.space_id, batch.dimensions, *params)).fetchone()
    expected = conn.execute('SELECT COUNT(*) FROM blocks' + ('' if policy is None else ' WHERE embedding_local_only=?'), params).fetchone()[0]
    actual = conn.execute(f'SELECT COUNT(*) FROM {table}' + ('' if policy is None else ' WHERE local_only=?'), params).fetchone()[0]
    if missing or actual != expected:
        raise ValueError('incomplete vector space coverage')
    if top_k <= 0:
        return []
    rows = conn.execute(f'SELECT block_id,distance FROM {table} WHERE embedding MATCH ? AND k=?'
                        + ('' if policy is None else ' AND local_only=?'),
                        (sqlite_vec.serialize_float32(batch.vectors[0]), top_k, *params)).fetchall()
    return [VectorHit(id=row[0], score=max(0.0, min(1.0, 1 - row[1] ** 2 / 2))) for row in rows]
