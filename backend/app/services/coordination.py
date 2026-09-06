import asyncio
from functools import wraps
from weakref import WeakKeyDictionary

_vault_locks = WeakKeyDictionary()


def vault_mutation_lock():
    # Service/test lifecycle restarts must not reuse a lock bound to a closed loop.
    loop = asyncio.get_running_loop()
    return _vault_locks.setdefault(loop, asyncio.Lock())


def serialized_vault_mutation(operation):
    """串行化 Vault 文件与可重建索引的写入，避免 rebuild 与 Note 写操作交错。"""

    @wraps(operation)
    async def wrapped(*args, **kwargs):
        async with vault_mutation_lock():
            return await operation(*args, **kwargs)

    return wrapped
