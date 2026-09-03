"""确定性的 MCP stdio 测试 Server；仅使用标准库，不依赖产品代码。"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

WRITE_LOCK = threading.Lock()
CANCELLED: dict[int, threading.Event] = {}
MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


def send(message: dict[str, Any]) -> None:
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def respond(request_id: int, result: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def tool(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(properties or {}),
            "additionalProperties": False,
        },
    }


TOOLS = {
    "echo": {
        **tool(
            "echo",
            "Return the provided text.",
            {
                "text": {"type": "string"},
                "suffix": {"type": ["string", "null"]},
            },
        ),
        "_meta": {"notesagent/permission": "notes.read"},
    },
    "fail": tool("fail", "Return an MCP business error."),
    "sleep": tool("sleep", "Wait until completed or cancelled.", {"seconds": {"type": "number"}}),
    "large": tool("large", "Return a result larger than the host limit."),
    "environment": tool("environment", "Report whether host secrets leaked into the process."),
    "exit": tool("exit", "Terminate the fixture process."),
    "command": tool(
        "command",
        "Execute a NotesAgent Plugin Command envelope.",
        {"_notesagent": {"type": "object"}},
    ),
}
# suffix 是可选字段，用于验证 Host 不会把缺省值擅自补成 null。
TOOLS["echo"]["inputSchema"]["required"] = ["text"]


def call_tool(request_id: int, params: dict[str, Any]) -> None:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name == "command":
        envelope = arguments.get("_notesagent") or {}
        command_arguments = envelope.get("arguments") or {}
        context = envelope.get("context") or {}
        settings = envelope.get("settings") or {}
        secrets = envelope.get("secrets") or {}
        if not isinstance(secrets.get("api_key"), str):
            respond(
                request_id,
                {
                    "content": [{"type": "text", "text": "declared secret missing"}],
                    "isError": True,
                },
            )
            return
        message = command_arguments.get("message") or context.get("selection") or ""
        message = f"{settings.get('message_prefix', '')}{message}"
        respond(
            request_id,
            {
                "content": [{"type": "text", "text": "command completed"}],
                "structuredContent": {
                    "type": "notification",
                    "payload": {
                        "level": "success",
                        "message": str(message),
                    },
                },
                "isError": False,
            },
        )
        return
    if name == "echo":
        text = str(arguments.get("text", ""))
        structured_content = {"echo": text}
        if "suffix" in arguments:
            structured_content["suffix"] = arguments["suffix"]
        respond(
            request_id,
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured_content,
                "isError": False,
            },
        )
        return
    if name == "fail":
        respond(
            request_id,
            {
                "content": [{"type": "text", "text": "fixture failure"}],
                "isError": True,
            },
        )
        return
    if name == "large":
        respond(
            request_id,
            {
                "content": [{"type": "text", "text": "x" * 300_000}],
                "isError": False,
            },
        )
        return
    if name == "environment":
        respond(
            request_id,
            {
                "content": [{"type": "text", "text": "environment checked"}],
                "structuredContent": {
                    "has_openai_key": "OPENAI_API_KEY" in os.environ,
                    "has_app_db_path": "APP_DB_PATH" in os.environ,
                },
                "isError": False,
            },
        )
        return
    if name == "exit":
        os._exit(17)
    if name == "sleep":
        cancelled = CANCELLED.setdefault(request_id, threading.Event())
        seconds = max(0.0, min(float(arguments.get("seconds", 0)), 30.0))
        if cancelled.wait(seconds):
            respond(
                request_id,
                {
                    "content": [{"type": "text", "text": "cancelled"}],
                    "isError": True,
                },
            )
        else:
            respond(
                request_id,
                {
                    "content": [{"type": "text", "text": "completed"}],
                    "structuredContent": {"slept": seconds},
                    "isError": False,
                },
            )
        CANCELLED.pop(request_id, None)
        return
    send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": f"Unknown tool: {name}"},
        }
    )


def main() -> None:
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize" and isinstance(request_id, int):
            if MODE == "invalid-result":
                send({"jsonrpc": "2.0", "id": request_id, "result": None})
                continue
            if MODE == "oversized-stdout":
                # 不带换行，验证 Host 在读取完整内容前执行硬上限。
                sys.stdout.write("x" * (2 * 1024 * 1024 + 1))
                sys.stdout.flush()
                time.sleep(10)
                return
            respond(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion"),
                    "capabilities": (
                        {} if MODE == "no-tools" else {"tools": {"listChanged": False}}
                    ),
                    "serverInfo": {"name": "notesagent-mcp-fixture", "version": "1.0.0"},
                },
            )
        elif method == "tools/list" and isinstance(request_id, int):
            if MODE == "invalid-schema":
                respond(
                    request_id,
                    {
                        "tools": [
                            {
                                "name": "broken",
                                "description": "invalid schema",
                                "inputSchema": {"type": "string"},
                            }
                        ]
                    },
                )
            elif params.get("cursor") == "page-2":
                respond(
                    request_id,
                    {
                        "tools": [
                            TOOLS["large"],
                            TOOLS["environment"],
                            TOOLS["exit"],
                            TOOLS["command"],
                        ]
                    },
                )
            else:
                respond(
                    request_id,
                    {"tools": [TOOLS["echo"], TOOLS["fail"], TOOLS["sleep"]], "nextCursor": "page-2"},
                )
        elif method == "tools/call" and isinstance(request_id, int):
            threading.Thread(target=call_tool, args=(request_id, params), daemon=True).start()
        elif method == "notifications/cancelled":
            cancelled_id = params.get("requestId")
            if isinstance(cancelled_id, int):
                CANCELLED.setdefault(cancelled_id, threading.Event()).set()
        elif method == "ping" and isinstance(request_id, int):
            respond(request_id, {})


if __name__ == "__main__":
    main()
