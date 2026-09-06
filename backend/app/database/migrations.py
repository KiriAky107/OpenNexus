"""轻量 schema 迁移。

约定：MIGRATIONS 列表按版本号顺序排列，只增不改。每一条是一个完整的 SQL 脚本，
执行后写入 schema_migrations 记录版本。修改 Schema 时在末尾追加新脚本，禁止删旧脚本或
依赖运行时自动删表重建（团队约定）。
"""

from datetime import datetime, timezone
import sqlite3

from app.constants import EMBEDDING_DIM

# 每个元素对应一个版本（下标 + 1）。vec0 建表需要本连接已加载 sqlite-vec 扩展，
# 由 db.connect() 在调用 migrate 之前完成。
MIGRATIONS: list[str] = [
    # v1: 笔记元数据 + Block + FTS5 全文索引 + 向量表 + 索引元信息
    f"""
    CREATE TABLE IF NOT EXISTS notes (
        note_id     TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        file_path   TEXT NOT NULL UNIQUE,
        folder      TEXT NOT NULL DEFAULT '',
        tags        TEXT NOT NULL DEFAULT '[]',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS blocks (
        block_id     TEXT PRIMARY KEY,
        note_id      TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
        heading_path TEXT NOT NULL DEFAULT '[]',
        start_offset INTEGER NOT NULL,
        end_offset   INTEGER NOT NULL,
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        token_count  INTEGER NOT NULL,
        position     INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_blocks_note ON blocks(note_id, position);

    CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
        block_id UNINDEXED,
        note_id UNINDEXED,
        heading_path,
        content,
        tokenize = 'unicode61'
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0(
        block_id  TEXT PRIMARY KEY,
        embedding float[{EMBEDDING_DIM}]
    );

    CREATE TABLE IF NOT EXISTS index_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # v2: 第一阶段 Task Core；正文仍归 Note/Vault，任务状态持久化到 SQLite。
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id      TEXT PRIMARY KEY,
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'todo',
        note_id      TEXT REFERENCES notes(note_id) ON DELETE SET NULL,
        due_at       TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
    """,
    # v3: 第二阶段 Agent Trace；Run 与事件事实持久化，供 SSE 恢复和 Benchmark 复用。
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        run_id               TEXT PRIMARY KEY,
        status               TEXT NOT NULL,
        run_json             TEXT NOT NULL,
        request_json         TEXT NOT NULL,
        config_snapshot_json TEXT NOT NULL DEFAULT '{}',
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_agent_runs_created
        ON agent_runs(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_agent_runs_status
        ON agent_runs(status, updated_at DESC);

    CREATE TABLE IF NOT EXISTS agent_events (
        run_id     TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
        sequence   INTEGER NOT NULL,
        event      TEXT NOT NULL,
        data_json  TEXT NOT NULL DEFAULT '{}',
        timestamp  TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence)
    );
    CREATE INDEX IF NOT EXISTS idx_agent_events_type
        ON agent_events(run_id, event, sequence);
    """,
    # v4: durable media jobs, replayable events and revisions.
    """
    CREATE TABLE media_jobs (
        job_id TEXT PRIMARY KEY, status TEXT NOT NULL, job_json TEXT NOT NULL,
        request_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        idempotency_key TEXT UNIQUE, fingerprint TEXT NOT NULL
    );
    CREATE INDEX media_jobs_created ON media_jobs(created_at DESC);
    CREATE TABLE media_events (
        job_id TEXT NOT NULL REFERENCES media_jobs(job_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL, event TEXT NOT NULL, data_json TEXT NOT NULL,
        timestamp TEXT NOT NULL, PRIMARY KEY(job_id, sequence)
    );
    CREATE TABLE media_revisions (
        job_id TEXT NOT NULL REFERENCES media_jobs(job_id) ON DELETE CASCADE,
        revision INTEGER NOT NULL, job_json TEXT NOT NULL,
        PRIMARY KEY(job_id, revision)
    );
    CREATE TABLE media_notes (
        job_id TEXT NOT NULL REFERENCES media_jobs(job_id), revision INTEGER NOT NULL,
        options_hash TEXT NOT NULL, note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
        PRIMARY KEY(job_id, revision, options_hash)
    );
    """,
    # v5: application-owned search history, shared by web and desktop clients.
    """
    CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL UNIQUE
    );
    """,
    # v6: persist each block's embedding policy for partitioned retrieval.
    """
    ALTER TABLE blocks ADD COLUMN embedding_local_only INTEGER NOT NULL DEFAULT 0;
    """,
    # v7: application-owned chat conversations and messages, shared by web and desktop clients.
    """
    CREATE TABLE IF NOT EXISTS chat_conversations (
        conversation_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chat_conversations_updated
        ON chat_conversations(updated_at DESC);

    CREATE TABLE IF NOT EXISTS chat_messages (
        message_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        thinking TEXT,
        citations_json TEXT NOT NULL DEFAULT '[]',
        tool_calls_json TEXT NOT NULL DEFAULT '[]',
        usage_json TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(conversation_id, sequence)
    );
    CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
        ON chat_messages(conversation_id, sequence);
    """,
    """
    ALTER TABLE chat_messages ADD COLUMN parent_message_id TEXT;
    ALTER TABLE chat_messages ADD COLUMN activity_json TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE chat_conversations ADD COLUMN active_leaf TEXT;
    UPDATE chat_messages SET parent_message_id=(SELECT prev.message_id FROM chat_messages prev
        WHERE prev.conversation_id=chat_messages.conversation_id AND prev.sequence<chat_messages.sequence ORDER BY prev.sequence DESC LIMIT 1);
    UPDATE chat_conversations SET active_leaf=(SELECT message_id FROM chat_messages WHERE conversation_id=chat_conversations.conversation_id ORDER BY sequence DESC LIMIT 1);
    CREATE INDEX idx_chat_parent ON chat_messages(conversation_id,parent_message_id);
    """,
    """ALTER TABLE chat_conversations ADD COLUMN active_response_id TEXT;""",
    """ALTER TABLE chat_messages ADD COLUMN workspace_context_json TEXT;""",
    """ALTER TABLE chat_messages ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]';""",
    """ALTER TABLE chat_messages ADD COLUMN context_captured INTEGER NOT NULL DEFAULT 0;""",
]


def _statements(script: str):
    """Split complete SQLite statements without executescript's implicit COMMIT."""
    pending = ""
    for char in script:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        yield pending


def migrate(conn) -> None:
    """把尚未应用的迁移脚本按序应用到给定连接。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}

    for idx, script in enumerate(MIGRATIONS, start=1):
        if idx in applied:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another connection may have migrated while this one waited.
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (idx,)).fetchone():
                recovered_v6 = False
                if idx == 6:
                    column = next((row for row in conn.execute("PRAGMA table_info(blocks)")
                                   if row["name"] == "embedding_local_only"), None)
                    if column is not None:
                        # Recover the precise partial state left by the old v6 runner.
                        if column["type"].upper() != "INTEGER" or column["notnull"] != 1 or column["dflt_value"] != "0":
                            raise sqlite3.DatabaseError("Unexpected embedding_local_only column schema")
                        recovered_v6 = True
                if not recovered_v6:
                    for statement in _statements(script):
                        conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (idx, datetime.now(timezone.utc).isoformat()),
                )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
