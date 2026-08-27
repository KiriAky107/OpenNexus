"""轻量 schema 迁移。

约定：MIGRATIONS 列表按版本号顺序排列，只增不改。每一条是一个完整的 SQL 脚本，
执行后写入 schema_migrations 记录版本。修改 Schema 时在末尾追加新脚本，禁止删旧脚本或
依赖运行时自动删表重建（团队约定）。
"""

from datetime import datetime, timezone

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
]


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
        conn.executescript(script)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (idx, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
