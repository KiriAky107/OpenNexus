"""进程本地检索活动，由搜索、RAG 和 Agent 调用者共享。"""
import asyncio
from functools import wraps

active = 0
completed = 0
failed = 0
cancelled = 0


def track_search(operation):
    @wraps(operation)
    async def wrapped(self, request):
        global active, completed, failed, cancelled
        if request.mode == 'fts':
            return await operation(self, request)
        active += 1
        try:
            result = await operation(self, request)
            completed += 1
            return result
        except asyncio.CancelledError:
            cancelled += 1
            raise
        except Exception:
            failed += 1
            raise
        finally:
            active -= 1
    return wrapped
