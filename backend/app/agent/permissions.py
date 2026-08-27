import asyncio
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4


class PermissionMode(str, Enum):
    allow = "allow"
    confirm = "confirm"
    deny = "deny"


class PermissionPolicy:
    def __init__(self) -> None:
        self._rules: dict[str, PermissionMode] = {
            "notes.delete": PermissionMode.confirm,
            "notes.write": PermissionMode.confirm,
            "network.request": PermissionMode.confirm,
            "secrets.use": PermissionMode.confirm,
        }

    def set_rule(self, permission: str, mode: PermissionMode) -> None:
        self._rules[permission] = mode

    def mode_for(self, permission: str | None) -> PermissionMode:
        if permission is None:
            return PermissionMode.allow
        return self._rules.get(permission, PermissionMode.allow)


@dataclass(slots=True)
class PermissionTicket:
    request_id: str
    run_id: str
    permission: str
    future: asyncio.Future[str]


class PermissionManager:
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
            self._session_grants.add(ticket.permission)
        ticket.future.set_result(decision)
        return True

    def cancel_run(self, run_id: str) -> None:
        for key, ticket in list(self._pending.items()):
            if ticket.run_id == run_id:
                if not ticket.future.done():
                    ticket.future.cancel()
                self._pending.pop(key, None)
