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
    return _connect_path(settings.db_path)


def connect_knowledge() -> sqlite3.Connection:
    """桌面投影不得在不同 Vault 之间共享笔记或向量记录。"""
    settings = get_settings()
    if settings.environment != 'desktop':
        return connect()
    from app import host_bridge
    from app.errors import ApiError
    from uuid import UUID
    try:
        vault = str(UUID(host_bridge.vault_id.get() or ''))
    except ValueError:
        raise ApiError(409, 'WORKSPACE_NOT_OPEN', '请先打开授权工作区。') from None
    # 该数据库还保存持久的逻辑记录（任务）；切勿将其作为缓存删除。
    return _connect_path(settings.data_dir / 'vault-state' / vault / 'core.sqlite3')


def _connect_path(path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # 关闭 Python sqlite3 的隐式事务，提交时机由 transaction() 或显式 commit 控制。
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _load_extension(conn)
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    """读改写事务可预取写锁；只读调用仍允许与其他连接并发。"""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
