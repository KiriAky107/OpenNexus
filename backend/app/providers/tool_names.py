"""保持内部命名空间工具与提供程序的 64 字符名称兼容。"""
import hashlib
import re
from functools import wraps

from app.contracts import MessageRole, ModelRequest


def prepare_tool_names(request: ModelRequest) -> tuple[ModelRequest, dict[str, str]]:
    names = {tool.name for tool in request.tools}
    for message in request.messages:
        names.update(call.name for call in message.tool_calls)
        if message.role == MessageRole.tool and message.name:
            names.add(message.name)
    mapping = {name: name for name in names if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)}
    used = set(mapping)
    for name in sorted(names - mapping.keys()):
        salt = 0
        while True:
            alias = "tool_" + hashlib.sha256(f"{name}:{salt}".encode()).hexdigest()[:56]
            if alias not in used:
                break
            salt += 1
        mapping[name] = alias
        used.add(alias)
    if all(name == alias for name, alias in mapping.items()):
        return request, {}
    wire = request.model_copy(deep=True)
    for tool in wire.tools:
        tool.name = mapping[tool.name]
    for message in wire.messages:
        for call in message.tool_calls:
            call.name = mapping[call.name]
        if message.role == MessageRole.tool and message.name:
            message.name = mapping[message.name]
    return wire, {alias: name for name, alias in mapping.items()}


def mapped_tool_names(complete):
    @wraps(complete)
    async def wrapped(self, request: ModelRequest):
        wire, originals = prepare_tool_names(request)
        turn = await complete(self, wire)
        for call in turn.tool_calls:
            call.name = originals.get(call.name, call.name)
        return turn
    return wrapped
