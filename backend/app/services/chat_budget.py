"""In-process budget decisions for an active chat stream.

The pending request is scoped to the vault and the exact response. A disconnected
stream removes its request; a new chat request must never replay its tool calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from app import host_bridge
from app.errors import ApiError


@dataclass
class Pending:
    request_id: str
    conversation_id: str
    assistant_message_id: str
    scope_id: str
    decision: asyncio.Future[int]


_pending: dict[str, Pending] = {}


def create(conversation_id: str | None, assistant_message_id: str | None) -> Pending:
    pending = Pending(
        request_id=f"chat_budget_{uuid4().hex}",
        conversation_id=conversation_id or "",
        assistant_message_id=assistant_message_id or "",
        scope_id=host_bridge.vault_id.get() or "",
        decision=asyncio.get_running_loop().create_future(),
    )
    _pending[pending.request_id] = pending
    return pending


def close(pending: Pending) -> None:
    if _pending.get(pending.request_id) is pending:
        _pending.pop(pending.request_id)
    if not pending.decision.done():
        pending.decision.cancel()


def close_response(conversation_id: str | None, assistant_message_id: str | None) -> None:
    for pending in list(_pending.values()):
        if (pending.conversation_id == (conversation_id or '')
                and pending.assistant_message_id == (assistant_message_id or '')
                and pending.scope_id == (host_bridge.vault_id.get() or '')):
            close(pending)


def resolve(request_id: str, conversation_id: str, assistant_message_id: str, additional_tokens: int) -> None:
    pending = _pending.get(request_id)
    if (pending is None or pending.scope_id != (host_bridge.vault_id.get() or "")
            or pending.conversation_id != conversation_id
            or pending.assistant_message_id != assistant_message_id):
        raise ApiError(404, 'CHAT_BUDGET_NOT_FOUND', 'This chat budget request is no longer active')
    if pending.decision.done():
        raise ApiError(409, 'CHAT_BUDGET_ALREADY_RESOLVED', 'This chat budget request was already resolved')
    pending.decision.set_result(additional_tokens)
