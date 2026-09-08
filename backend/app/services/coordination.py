import asyncio
from contextlib import contextmanager
from functools import wraps
from weakref import WeakKeyDictionary

_vault_locks = WeakKeyDictionary()


@contextmanager
def web_vault_ownership():
    """与 Rust fs2 使用同一 OS 文件锁，避免首次切换时两套写入者重叠。"""
    from app.config import get_settings
    from app.errors import ApiError
    if get_settings().environment == 'desktop':
        raise ApiError(409, 'WORKSPACE_OWNER_DESKTOP', '桌面笔记写入必须通过 Rust Host')
    root = get_settings().vault_path
    managed = root / '.ainote'
    if managed.is_symlink() or (hasattr(managed, 'is_junction') and managed.is_junction()):
        raise ApiError(403, 'WORKSPACE_UNSAFE_PATH', '工作区元数据路径不安全')
    managed.mkdir(parents=True, exist_ok=True)
    path = managed / 'host.lock'
    if path.is_symlink():
        raise ApiError(403, 'WORKSPACE_UNSAFE_PATH', '工作区锁路径不安全')
    with path.open('a+b') as stream:
        import os
        locked = False
        try:
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                raise ApiError(409, 'WORKSPACE_OWNER_BUSY', '工作区由其他进程持有，请稍后重试') from None
            # 桌面元数据已建立后必须经 Host 写入；不以进程退出自动降回 Web 所有权。
            if (managed / 'host.sqlite3').exists():
                raise ApiError(409, 'WORKSPACE_OWNER_DESKTOP', '该 Vault 已由桌面 Host 管理，Web 禁止写入')
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def vault_mutation_lock():
    # Service/test lifecycle restarts must not reuse a lock bound to a closed loop.
    loop = asyncio.get_running_loop()
    return _vault_locks.setdefault(loop, asyncio.Lock())


def serialized_vault_mutation(operation):
    """串行化 Vault 文件与可重建索引的写入，避免 rebuild 与 Note 写操作交错。"""

    @wraps(operation)
    async def wrapped(*args, **kwargs):
        async with vault_mutation_lock():
            with web_vault_ownership():
                return await operation(*args, **kwargs)

    return wrapped
