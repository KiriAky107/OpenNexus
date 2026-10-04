"""Awaited response writes, isolated from the event loop and request cancellation."""
from __future__ import annotations

import asyncio
from anyio import CancelScope

from app.operation_logs import log_event
from app.services import chat_history

_pending: set[asyncio.Task] = set()


async def _finish(write):
    cancelled = False
    # ASGI disconnect uses AnyIO level cancellation; shielding only the asyncio
    # task would otherwise keep cancelling each await and spin the event loop.
    with CancelScope(shield=True):
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                if write.cancelled():
                    raise
                cancelled = True
        result = write.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def save(conversation_id: str, **message) -> None:
    async def write():
        try:
            # to_thread copies this response's Vault ContextVars. No request
            # cancellation may abandon the database transaction halfway through.
            await asyncio.to_thread(chat_history.append_message, conversation_id, **message)
        except Exception as exc:
            log_event('chat', 'chat.persistence_failed', level='ERROR', error=exc)
            raise

    task = asyncio.create_task(write(), name='chat-response-persistence')
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    await _finish(task)


async def shutdown() -> None:
    while _pending:
        # Each write reports its failure itself; drain all responses on shutdown.
        await _finish(asyncio.gather(*list(_pending), return_exceptions=True))
