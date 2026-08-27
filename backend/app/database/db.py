"""SQLite 连接管理。

每次调用 connect() 打开一个新连接：加载 sqlite-vec 扩展、打开外键、应用迁移。
连接由调用方负责关闭；写入通过 transaction() 上下文显式控制提交，避免隐式事务带来的
半提交状态。
"""

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

import sqlite_vec

from app.config import get_settings
from app.database.migrations import migrate


def _load_extension(conn: sqlite3.Connection) -> None:
    """在当前连接上注册 sqlite-vec 扩展，随后关闭 load_extension 开关。"""
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    # 关闭 Python sqlite3 的隐式事务，提交时机由 transaction() 或显式 commit 控制。
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    _load_extension(conn)
    migrate(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """显式事务：提交成功则 COMMIT，异常则 ROLLBACK。"""
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
