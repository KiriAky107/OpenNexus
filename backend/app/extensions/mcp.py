"""本地 stdio MCP Bridge。

第三方 Server 始终运行在子进程中。Bridge 只把通过校验的 MCP Tool 转换为项目内部
ToolDefinition/ToolResult，不把 MCP 原始协议泄露给 Agent Runtime 或前端。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.agent.permissions import KNOWN_PERMISSIONS
from app.agent.tools import ToolExecutionError
from app.contracts import (
    PluginBackend,
    PluginHostState,
    PluginHostStatus,
    ToolDefinition,
)
from app.schema_security import (
    SchemaReferenceError,
    reject_external_schema_references,
)

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    MCP_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
MAX_MCP_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_MCP_TOOL_RESULT_BYTES = 256 * 1024
MAX_MCP_TOOLS = 500
MAX_MCP_LIST_PAGES = 100


class McpBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class McpDiscoveredTool:
    remote_name: str
    definition: ToolDefinition


@dataclass(slots=True)
class _PendingRequest:
    response: queue.Queue[dict[str, Any] | BaseException]


class McpStdioClient:
    """线程驱动的换行分隔 JSON-RPC 客户端，避免阻塞 FastAPI 事件循环。"""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
        on_seen: Callable[[], None],
        on_broken: Callable[[str], None],
        on_tools_changed: Callable[[], None],
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.environment = environment or {}
        self.on_seen = on_seen
        self.on_broken = on_broken
        self.on_tools_changed = on_tools_changed
        self.process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _PendingRequest] = {}
        self._next_id = 1
        self._stopping = False
        # stderr 只在 Host 内部保留有限尾部，不进入 API、Trace 或普通日志。
        self._stderr_tail: deque[str] = deque(maxlen=50)

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        # TODO(extension-security): 社区 Plugin 开放前迁移到 Tauri/Rust Host 的
        # 平台级沙箱启动器；uvx 只隔离 Python 依赖，不能替代系统权限限制。
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        environment = _subprocess_environment()
        environment.update(self.environment)
        environment.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=environment,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise McpBridgeError(
                "PLUGIN_HOST_START_FAILED",
                f"Cannot start MCP server process: {exc}",
                status_code=503,
            ) from exc
        threading.Thread(target=self._stdout_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        timeout_code: str,
        response_error_code: str = "MCP_TOOL_CALL_FAILED",
    ) -> dict[str, Any]:
        request_id, pending = self.begin_request(method, params)
        return self.wait_response(
            request_id,
            pending,
            timeout=timeout,
            timeout_code=timeout_code,
            response_error_code=response_error_code,
        )

    def begin_request(
        self, method: str, params: dict[str, Any]
    ) -> tuple[int, _PendingRequest]:
        self._ensure_running()
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingRequest(response=queue.Queue(maxsize=1))
            self._pending[request_id] = pending
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        except BaseException:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        return request_id, pending

    def wait_response(
        self,
        request_id: int,
        pending: _PendingRequest,
        *,
        timeout: float,
        timeout_code: str,
        response_error_code: str = "MCP_TOOL_CALL_FAILED",
    ) -> dict[str, Any]:
        try:
            response = pending.response.get(timeout=timeout)
        except queue.Empty as exc:
            self.cancel(request_id, "Request timed out.")
            self.abandon(request_id)
            raise McpBridgeError(timeout_code, "MCP request timed out.", status_code=504) from exc
        if isinstance(response, BaseException):
            raise response
        if "error" in response:
            error = response.get("error")
            message = (
                str(error.get("message", "MCP JSON-RPC error."))
                if isinstance(error, dict)
                else "MCP JSON-RPC error."
            )
            raise McpBridgeError(response_error_code, message)
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpBridgeError(
                response_error_code, "MCP response result must be an object."
            )
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def cancel(self, request_id: int, reason: str = "Cancelled by host.") -> None:
        try:
            self.notify(
                "notifications/cancelled",
                {"requestId": request_id, "reason": reason},
            )
        except McpBridgeError:
            pass

    def abandon(
        self, request_id: int, wake_error: BaseException | None = None
    ) -> None:
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        # asyncio.to_thread 被取消时不会停止底层线程；主动唤醒 Queue，避免线程
        # 一直占用默认线程池直至远端超时。
        if pending is not None and wake_error is not None:
            try:
                pending.response.put_nowait(wake_error)
            except queue.Full:
                pass

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        self._stopping = True
        try:
            if process.stdin:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        finally:
            self._fail_pending(
                McpBridgeError("PLUGIN_HOST_UNAVAILABLE", "MCP host stopped.", status_code=503)
            )
            self.process = None

    def _send(self, message: dict[str, Any]) -> None:
        self._ensure_running()
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES:
            raise McpBridgeError("MCP_TOOL_CALL_FAILED", "MCP request is too large.")
        process = self.process
        assert process is not None and process.stdin is not None
        try:
            with self._write_lock:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise McpBridgeError(
                "PLUGIN_HOST_UNAVAILABLE", "MCP host input is closed.", status_code=503
            ) from exc

    def _stdout_loop(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        failure: str | None = None
        try:
            while True:
                # readline(size) 在换行缺失时仍有硬上限，不能先把任意大的
                # 第三方 stdout 行完整读入宿主内存再检查。
                raw_line = process.stdout.readline(MAX_MCP_MESSAGE_BYTES + 1)
                if raw_line == "":
                    break
                if not raw_line.endswith("\n"):
                    failure = "MCP server emitted an oversized or unterminated message."
                    break
                if len(raw_line.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES:
                    failure = "MCP server emitted an oversized protocol message."
                    break
                try:
                    message = json.loads(raw_line)
                except json.JSONDecodeError:
                    failure = "MCP server emitted invalid JSON on stdout."
                    break
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    failure = "MCP server emitted an invalid JSON-RPC message."
                    break
                self.on_seen()
                if "id" in message and ("result" in message or "error" in message):
                    request_id = message.get("id")
                    if isinstance(request_id, int):
                        with self._pending_lock:
                            pending = self._pending.pop(request_id, None)
                        if pending:
                            pending.response.put(message)
                    continue
                method = message.get("method")
                if method == "notifications/tools/list_changed":
                    self.on_tools_changed()
                elif isinstance(method, str) and "id" in message:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Method not supported."},
                        }
                    )
        except (McpBridgeError, OSError, ValueError) as exc:
            failure = f"MCP stdout closed unexpectedly: {type(exc).__name__}."
        finally:
            if failure and process.poll() is None:
                process.terminate()
            exit_code = process.poll()
            if exit_code is None:
                try:
                    exit_code = process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    exit_code = None
            if not self._stopping:
                message = failure or f"MCP host exited unexpectedly with code {exit_code}."
                error = McpBridgeError(
                    "PLUGIN_HOST_UNAVAILABLE", message, status_code=503
                )
                self._fail_pending(error)
                self.on_broken(message)

    def _stderr_loop(self) -> None:
        process = self.process
        assert process is not None and process.stderr is not None
        try:
            while True:
                # stderr 不是协议通道，但同样按块读取，避免无换行日志造成
                # 宿主侧的无界字符串分配。
                line = process.stderr.readline(1025)
                if line == "":
                    break
                self._stderr_tail.append(line.rstrip()[:1024])
        except (OSError, ValueError):
            return

    def _ensure_running(self) -> None:
        if self.process is None or self.process.poll() is not None:
            raise McpBridgeError(
                "PLUGIN_HOST_UNAVAILABLE", "MCP host is not running.", status_code=503
            )

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.response.put(error)


@dataclass(slots=True)
class _McpHost:
    backend: PluginBackend
    client: McpStdioClient
    status: PluginHostStatus


class McpBridge:
    """管理每个 Plugin 的独立 MCP Client，并执行 Contract 转换。"""

    def __init__(self) -> None:
        self._hosts: dict[str, _McpHost] = {}
        self._statuses: dict[str, PluginHostStatus] = {}
        self._calls: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def start(
        self,
        plugin_id: str,
        backend: PluginBackend,
        package_path: Path,
        declared_permissions: list[str],
        on_unavailable: Callable[[str, str], None],
        *,
        command_override: list[str] | None = None,
        environment: dict[str, str] | None = None,
        tool_source: str = "plugin",
    ) -> list[McpDiscoveredTool]:
        if backend.transport != "stdio":
            raise McpBridgeError(
                "MCP_CAPABILITY_UNSUPPORTED",
                "Phase C only supports the MCP stdio transport.",
                status_code=501,
            )
        command = command_override or self._resolve_command(package_path, backend)
        now = datetime.now(timezone.utc)
        status = PluginHostStatus(
            plugin_id=plugin_id,
            backend_type="mcp",
            transport="stdio",
            status=PluginHostState.starting,
            started_at=now,
            last_seen_at=now,
        )
        host_ref: dict[str, _McpHost] = {}

        def seen() -> None:
            host = host_ref.get("host")
            if host:
                host.status.last_seen_at = datetime.now(timezone.utc)

        def broken(message: str) -> None:
            host = host_ref.get("host")
            if host:
                host.status.status = PluginHostState.unhealthy
                host.status.error = message
            on_unavailable(plugin_id, message)

        def tools_changed() -> None:
            broken("MCP tool list changed; restart the Plugin Host to revalidate tools.")

        client = McpStdioClient(
            command,
            cwd=package_path,
            environment=environment,
            on_seen=seen,
            on_broken=broken,
            on_tools_changed=tools_changed,
        )
        host = _McpHost(backend=backend, client=client, status=status)
        host_ref["host"] = host
        with self._lock:
            if plugin_id in self._hosts:
                raise McpBridgeError(
                    "PLUGIN_HOST_START_FAILED",
                    f"MCP host is already running: {plugin_id}",
                    status_code=409,
                )
            self._hosts[plugin_id] = host
            self._statuses[plugin_id] = status
        try:
            client.start()
            initialize = client.request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "NotesAgent", "version": "0.1.0"},
                },
                timeout=backend.startup_timeout_seconds,
                timeout_code="MCP_INITIALIZE_FAILED",
                response_error_code="MCP_INITIALIZE_FAILED",
            )
            version = initialize.get("protocolVersion")
            if version not in SUPPORTED_PROTOCOL_VERSIONS:
                raise McpBridgeError(
                    "MCP_INITIALIZE_FAILED",
                    f"Unsupported MCP protocol version: {version}",
                )
            capabilities = initialize.get("capabilities")
            if not isinstance(capabilities, dict) or not isinstance(
                capabilities.get("tools"), dict
            ):
                raise McpBridgeError(
                    "MCP_CAPABILITY_UNSUPPORTED",
                    "MCP server does not declare the tools capability.",
                )
            server_info = initialize.get("serverInfo")
            if not isinstance(server_info, dict):
                server_info = {}
            status.protocol_version = str(version)
            status.server_name = _optional_string(server_info.get("name"))
            status.server_version = _optional_string(server_info.get("version"))
            client.notify("notifications/initialized")
            discovered = self._discover_tools(
                plugin_id, client, backend, declared_permissions, tool_source
            )
            status.status = PluginHostState.ready
            status.tools_count = len(discovered)
            status.last_seen_at = datetime.now(timezone.utc)
            status.error = None
            return discovered
        except McpBridgeError as exc:
            status.status = PluginHostState.error
            status.error = exc.message
            client.stop()
            with self._lock:
                self._hosts.pop(plugin_id, None)
            raise
        except Exception as exc:
            status.status = PluginHostState.error
            status.error = f"MCP initialization failed: {type(exc).__name__}."
            client.stop()
            with self._lock:
                self._hosts.pop(plugin_id, None)
            raise McpBridgeError("MCP_INITIALIZE_FAILED", status.error) from exc

    async def call_tool(
        self,
        plugin_id: str,
        remote_name: str,
        arguments: dict[str, Any],
        *,
        request_id: str,
    ) -> Any:
        host = self._host(plugin_id)
        rpc_id, pending = host.client.begin_request(
            "tools/call", {"name": remote_name, "arguments": arguments}
        )
        call_key = (plugin_id, request_id)
        with self._lock:
            self._calls[call_key] = rpc_id
        try:
            result = await asyncio.to_thread(
                host.client.wait_response,
                rpc_id,
                pending,
                timeout=host.backend.tool_timeout_seconds,
                timeout_code="MCP_TOOL_CALL_FAILED",
            )
        except asyncio.CancelledError:
            host.client.cancel(rpc_id)
            host.client.abandon(
                rpc_id,
                McpBridgeError(
                    "MCP_TOOL_CALL_FAILED", "MCP request was cancelled."
                ),
            )
            raise
        except McpBridgeError as exc:
            raise ToolExecutionError(exc.code, exc.message) from exc
        finally:
            with self._lock:
                self._calls.pop(call_key, None)

        encoded_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size > MAX_MCP_TOOL_RESULT_BYTES:
            raise ToolExecutionError(
                "MCP_TOOL_RESULT_TOO_LARGE",
                "MCP tool result exceeds the configured size limit.",
            )
        if result.get("isError") is True:
            raise ToolExecutionError(
                "MCP_TOOL_CALL_FAILED", _mcp_error_message(result.get("content"))
            )
        structured = result.get("structuredContent")
        if structured is not None:
            if not isinstance(structured, dict):
                raise ToolExecutionError(
                    "MCP_TOOL_CALL_FAILED",
                    "MCP structuredContent must be an object.",
                )
            return structured
        content = result.get("content", [])
        if not isinstance(content, list):
            raise ToolExecutionError(
                "MCP_TOOL_CALL_FAILED", "MCP tool content must be an array."
            )
        return {"content": content}

    def cancel(self, plugin_id: str, request_id: str) -> None:
        with self._lock:
            rpc_id = self._calls.get((plugin_id, request_id))
            host = self._hosts.get(plugin_id)
        if rpc_id is not None and host is not None:
            host.client.cancel(rpc_id)

    def stop(self, plugin_id: str) -> None:
        with self._lock:
            host = self._hosts.pop(plugin_id, None)
        if host:
            host.client.stop()
            host.status.status = PluginHostState.stopped
            host.status.tools_count = 0
            host.status.error = None

    def remove(self, plugin_id: str) -> None:
        """停止 Host，并清除卸载后不应跨安装保留的状态与调用索引。"""

        self.stop(plugin_id)
        with self._lock:
            self._statuses.pop(plugin_id, None)
            stale_calls = [key for key in self._calls if key[0] == plugin_id]
            for key in stale_calls:
                self._calls.pop(key, None)

    def status(self, plugin_id: str, backend: PluginBackend) -> PluginHostStatus:
        with self._lock:
            status = self._statuses.get(plugin_id)
            if status:
                return status.model_copy(deep=True)
        return PluginHostStatus(
            plugin_id=plugin_id,
            backend_type=backend.type,
            transport=backend.transport,
            status=PluginHostState.stopped,
        )

    def _discover_tools(
        self,
        plugin_id: str,
        client: McpStdioClient,
        backend: PluginBackend,
        declared_permissions: list[str],
        tool_source: str,
    ) -> list[McpDiscoveredTool]:
        discovered: list[McpDiscoveredTool] = []
        cursor: str | None = None
        for _ in range(MAX_MCP_LIST_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = client.request(
                "tools/list",
                params,
                timeout=backend.startup_timeout_seconds,
                timeout_code="MCP_INITIALIZE_FAILED",
                response_error_code="MCP_INITIALIZE_FAILED",
            )
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise McpBridgeError(
                    "MCP_TOOL_SCHEMA_INVALID", "MCP tools/list must return a tools array."
                )
            for raw in raw_tools:
                discovered.append(
                    self._map_tool(plugin_id, raw, declared_permissions, tool_source)
                )
                if len(discovered) > MAX_MCP_TOOLS:
                    raise McpBridgeError(
                        "MCP_TOOL_SCHEMA_INVALID",
                        f"MCP server exposes more than {MAX_MCP_TOOLS} tools.",
                    )
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise McpBridgeError(
                    "MCP_TOOL_SCHEMA_INVALID", "MCP nextCursor must be a non-empty string."
                )
            cursor = next_cursor
        else:
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID", "MCP tools/list exceeded the page limit."
            )
        names = [item.definition.name for item in discovered]
        if len(names) != len(set(names)):
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID", "MCP server returned duplicate tool names."
            )
        return discovered

    @staticmethod
    def _map_tool(
        plugin_id: str,
        raw: Any,
        declared_permissions: list[str],
        tool_source: str = "plugin",
    ) -> McpDiscoveredTool:
        if not isinstance(raw, dict):
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID", "MCP tool definition must be an object."
            )
        remote_name = raw.get("name")
        if not isinstance(remote_name, str) or not remote_name:
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID", "MCP tool name must be a non-empty string."
            )
        if (
            len(remote_name) > 128
            or not remote_name[0].isalnum()
            or not all(
                character.islower()
                or character.isdigit()
                or character in "._-"
                for character in remote_name
            )
        ):
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID",
                f"MCP tool name is not a valid NotesAgent id: {remote_name}",
            )
        schema = raw.get("inputSchema", {"type": "object", "properties": {}})
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID",
                f"MCP tool inputSchema must be an object schema: {remote_name}",
            )
        try:
            Draft202012Validator.check_schema(schema)
            reject_external_schema_references(schema)
        except (SchemaReferenceError, SchemaError) as exc:
            message = exc.message if isinstance(exc, SchemaError) else str(exc)
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID",
                f"Invalid MCP tool schema for {remote_name}: {message}",
            ) from exc
        metadata = raw.get("_meta")
        permission = (
            metadata.get("notesagent/permission") if isinstance(metadata, dict) else None
        )
        if permission is not None and (
            not isinstance(permission, str) or permission not in KNOWN_PERMISSIONS
        ):
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID",
                f"MCP tool declares an unknown permission: {remote_name}",
            )
        if permission and permission not in declared_permissions:
            raise McpBridgeError(
                "MCP_TOOL_SCHEMA_INVALID",
                f"MCP tool permission is missing from Plugin manifest: {permission}",
            )
        description = raw.get("description")
        return McpDiscoveredTool(
            remote_name=remote_name,
            definition=ToolDefinition(
                name=f"{plugin_id}.{remote_name}",
                description=description if isinstance(description, str) else remote_name,
                parameters=schema,
                permission=permission,
                source=tool_source,
            ),
        )

    def _host(self, plugin_id: str) -> _McpHost:
        with self._lock:
            host = self._hosts.get(plugin_id)
        if host is None or host.status.status != PluginHostState.ready:
            raise ToolExecutionError(
                "PLUGIN_HOST_UNAVAILABLE", f"MCP Plugin Host is not ready: {plugin_id}"
            )
        return host

    @staticmethod
    def _resolve_command(root: Path, backend: PluginBackend) -> list[str]:
        if not backend.command or not backend.command.strip():
            raise McpBridgeError(
                "PLUGIN_HOST_START_FAILED", "MCP stdio backend requires a command."
            )
        command = backend.command.strip()
        if Path(command).is_absolute() or "/" in command or "\\" in command:
            executable = (
                (root / command).resolve()
                if not Path(command).is_absolute()
                else Path(command).resolve()
            )
            try:
                executable.relative_to(root)
            except ValueError as exc:
                raise McpBridgeError(
                    "PLUGIN_HOST_START_FAILED",
                    "MCP executable path must stay inside the Plugin package.",
                ) from exc
            command = str(executable)
        return [command, *backend.args]


def _mcp_error_message(content: Any) -> str:
    if isinstance(content, list):
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)[:4096]
    return "MCP tool returned an error result."


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _subprocess_environment() -> dict[str, str]:
    """只传递启动进程所需的系统变量，隔离 Provider Key、Vault 路径等宿主状态。"""

    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "VIRTUAL_ENV",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment
