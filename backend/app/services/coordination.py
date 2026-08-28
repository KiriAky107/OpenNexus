import asyncio
from functools import wraps

_vault_mutation_lock = asyncio.Lock()


def serialized_vault_mutation(operation):
    """串行化 Vault 文件与可重建索引的写入，避免 rebuild 与 Note 写操作交错。"""

    @wraps(operation)
    async def wrapped(*args, **kwargs):
        async with _vault_mutation_lock:
            return await operation(*args, **kwargs)

    return wrapped
