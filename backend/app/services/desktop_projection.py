"""每个 Vault 独立、可重建的 FTS 投影，仅通过 Host 代理读取源数据。"""
from __future__ import annotations
import asyncio
from app import repository
from app.database.db import connect_knowledge, transaction
from app.knowledge.parser import parse_note
from app.services import desktop_notes
from app.services.coordination import vault_mutation_lock


def entries():
    result, offset = [], 0
    while True:
        page = desktop_notes.call('list', offset=offset, limit=1000)
        result.extend(page['items'])
        offset += len(page['items'])
        if offset >= page['total'] or not page['items']: return result


def _refresh():
    current = entries()  # 始终验证授权，包括缓存处于最新状态时。
    conn = connect_knowledge()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS host_projection (file_id TEXT PRIMARY KEY, hash TEXT NOT NULL, path TEXT NOT NULL)')
        old = {row['file_id']: (row['hash'], row['path']) for row in conn.execute('SELECT * FROM host_projection')}
        changed = []
        for entry in current:
            if old.get(entry['file_id']) == (entry['hash'], entry['path']): continue
            document = desktop_notes.call('read', file_id=entry['file_id'])
            note = desktop_notes.note_from_document(document)
            parsed = parse_note(markdown=note.markdown, file_path=note.file_path,
                                folder=note.file_path.rpartition('/')[0], note_id=note.note_id,
                                tags=note.tags, created_at=note.created_at, updated_at=note.updated_at)
            changed.append((document, parsed))
        removed = set(old) - {entry['file_id'] for entry in current}
        # 启动投影事务前先验证内容；事务内部不执行模型或网络 I/O。
        with transaction(conn):
            task_links = []
            for entry in current:
                for alias in entry.get('aliases', []):
                    if alias in removed:
                        task_links.extend((entry['file_id'], row['task_id']) for row in conn.execute('SELECT task_id FROM tasks WHERE note_id=?', [alias]))
            for file_id in removed:
                for block_id in repository.delete_note(file_id, conn=conn):
                    conn.execute('DELETE FROM vec_blocks WHERE block_id=?', [block_id])
                conn.execute('DELETE FROM host_projection WHERE file_id=?', [file_id])
            for document, parsed in changed:
                old_ids = repository.replace_note_metadata(conn=conn, note_id=parsed.note_id, title=parsed.title,
                    file_path=parsed.file_path, folder=parsed.folder, tags=parsed.tags, created_at=parsed.created_at,
                    updated_at=parsed.updated_at, blocks=parsed.blocks)
                for block_id in old_ids:
                    conn.execute('DELETE FROM vec_blocks WHERE block_id=?', [block_id])
                conn.execute('UPDATE blocks SET embedding_local_only=? WHERE note_id=?', (int(parsed.embedding_local_only), parsed.note_id))
                conn.execute('INSERT OR REPLACE INTO host_projection VALUES (?,?,?)', (parsed.note_id, document['hash'], parsed.file_path))
            for file_id, task_id in task_links:
                conn.execute('UPDATE tasks SET note_id=? WHERE task_id=? AND note_id IS NULL', [file_id, task_id])
            if removed or changed:
                repository.set_index_meta({'workspace_vectors_pending': '1'}, conn=conn)
    finally:
        conn.close()


async def refresh():
    async with vault_mutation_lock():
        work = asyncio.create_task(asyncio.to_thread(_refresh))
        # 即使请求被取消，也要保留投影门直到工作人员完成。
        cancelled = False
        while not work.done():
            try: await asyncio.shield(work)
            except asyncio.CancelledError: cancelled = True
        work.result()
        if cancelled: raise asyncio.CancelledError
