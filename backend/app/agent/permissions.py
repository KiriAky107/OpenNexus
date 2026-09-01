"""Agent 工具权限策略与一次性确认票据。"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4


class PermissionMode(str, Enum):
    allow = "allow"
    confirm = "confirm"
    deny = "deny"


KNOWN_PERMISSIONS = frozenset(
    {
        "notes.read",
        "notes.search",
        "notes.write",
        "notes.delete",
        "tasks.read",
        "tasks.write",
        "attachments.read",
        "network.request",
        "secrets.use",
        "ui.command",
        "ui.settings",
        "ui.sidebar",
    }
)


class PermissionPolicy:
    def __init__(self) -> None:
        self._rules: dict[str, PermissionMode] = {
            "notes.read": PermissionMode.allow,
            "notes.search": PermissionMode.allow,
            "notes.delete": PermissionMode.confirm,
            "notes.write": PermissionMode.confirm,
            "tasks.read": PermissionMode.allow,
            "tasks.write": PermissionMode.confirm,
            "attachments.read": PermissionMode.allow,
            "network.request": PermissionMode.confirm,
            "secrets.use": PermissionMode.confirm,
            "ui.command": PermissionMode.allow,
            "ui.settings": PermissionMode.allow,
            "ui.sidebar": PermissionMode.allow,
        }

    def set_rule(self, permission: str, mode: PermissionMode) -> None:
        self._rules[permission] = mode

    def mode_for(self, permission: str | None) -> PermissionMode:
        if permission is None:
            return PermissionMode.allow
        # 未登记权限一律拒绝，防止扩展通过拼写错误或新权限绕过策略。
        return self._rules.get(permission, PermissionMode.deny)


@dataclass(slots=True)
class PermissionTicket:
    request_id: str
    run_id: str
    permission: str
    future: asyncio.Future[str]


class PermissionManager:
    """管理当前进程内的确认请求与会话级授权。"""

    def __init__(self, policy: PermissionPolicy) -> None:
        self.policy = policy
        self._pending: dict[tuple[str, str], PermissionTicket] = {}
        self._session_grants: set[str] = set()

    def mode_for(self, permission: str | None) -> PermissionMode:
        if permission in self._session_grants:
            return PermissionMode.allow
        return self.policy.mode_for(permission)

    def create_ticket(self, run_id: str, permission: str) -> PermissionTicket:
        ticket = PermissionTicket(
            request_id=f"permission_{uuid4().hex}",
            run_id=run_id,
            permission=permission,
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[(run_id, ticket.request_id)] = ticket
        return ticket

    async def wait(self, ticket: PermissionTicket, timeout: float) -> str:
        try:
            return await asyncio.wait_for(ticket.future, timeout=timeout)
        finally:
            self._pending.pop((ticket.run_id, ticket.request_id), None)

    def resolve(self, run_id: str, request_id: str, decision: str) -> bool:
        ticket = self._pending.get((run_id, request_id))
        if ticket is None or ticket.future.done():
            return False
        if decision == "allow_session":
            # 会话授权只存在于进程内，应用重启后按默认策略重新确认。
            self._session_grants.add(ticket.permission)
        ticket.future.set_result(decision)
        return True

    def get_ticket(self, run_id: str, request_id: str) -> PermissionTicket | None:
        """只读返回待确认票据，供 Trace 记录权限类型；不暴露 Future 给接口层。"""

        return self._pending.get((run_id, request_id))

    def cancel_run(self, run_id: str) -> None:
        for key, ticket in list(self._pending.items()):
            if ticket.run_id == run_id:
                if not ticket.future.done():
                    ticket.future.cancel()
                self._pending.pop(key, None)
