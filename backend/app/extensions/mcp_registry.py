"""Independent, user-managed MCP server registry for development builds."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, create_model

from app.agent.permissions import KNOWN_PERMISSIONS
from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import (
    McpServer,
    McpServerCreateRequest,
    McpServerSecretStatus,
    McpServerTransport,
    McpServerUpdateRequest,
    McpToolSummary,
    PluginBackend,
    PluginHostState,
)
from app.extensions.mcp import McpBridge, McpBridgeError, McpDiscoveredTool
from app.providers.credentials import CredentialStoreError, EncryptedCredentialStore

_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HEADER_KEY = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_RESERVED_HEADERS = {
    "accept",
    "content-length",
    "content-type",
    "host",
    "mcp-protocol-version",
    "mcp-session-id",
}


class McpRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _serialized_lifecycle(method):
    """Serialize lifecycle mutations without blocking MCP failure callbacks."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lifecycle_lock:
            return method(self, *args, **kwargs)

    return wrapped


class McpServerRegistry:
    """Persists configuration and owns stdio host/tool lifecycles."""

    def __init__(
        self,
        registry: ToolRegistry,
        credentials: EncryptedCredentialStore,
        data_dir: Path,
        *,
        allow_process_launch: bool,
        bridge: McpBridge | None = None,
    ) -> None:
        self.tools = registry
        self.credentials = credentials
        self.data_dir = data_dir
        self.allow_process_launch = allow_process_launch
        self.bridge = bridge or McpBridge()
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._records = self._read()
        self._registered: dict[str, list[str]] = {}
        self._summaries: dict[str, list[McpToolSummary]] = {}
        self._last_status: dict[str, dict[str, Any]] = {}

    def list(self) -> list[McpServer]:
        with self._lock:
            return [
                self._public(server_id, record)
                for server_id, record in self._records.items()
            ]

    def get(self, server_id: str) -> McpServer:
        with self._lock:
            return self._public(server_id, self._record(server_id))

    def list_tools(self, server_id: str) -> list[McpToolSummary]:
        self._record(server_id)
        return [
            item.model_copy(deep=True) for item in self._summaries.get(server_id, [])
        ]

    @_serialized_lifecycle
    def create(self, request: McpServerCreateRequest) -> McpServer:
        self._validate(request)
        server_id = uuid4().hex[:12]
        record = request.model_dump(mode="json")
        record["name"] = request.name.strip()
        record["command"] = request.command.strip() if request.command else None
        record["url"] = request.url.strip() if request.url else None
        record.update(
            version=1,
            enabled=False,
            approved_digest=None,
            tested_digest=None,
            last_tested_at=None,
            last_test_succeeded=None,
        )
        with self._lock:
            updated = {**self._records, server_id: record}
            self._write(updated)
            self._records = updated
        return self.get(server_id)

    @_serialized_lifecycle
    def update(self, server_id: str, request: McpServerUpdateRequest) -> McpServer:
        self._validate(request)
        current = self._record(server_id)
        if request.version != current.get("version", 1):
            raise McpRegistryError(
                "MCP_SERVER_VERSION_CONFLICT",
                "MCP server configuration version is stale.",
                status_code=409,
            )
        self.disable(server_id)
        with self._lock:
            previous = self._record(server_id)
            removed = [
                (kind, key)
                for kind, old_keys, new_keys in (
                    (
                        "environment",
                        previous.get("secret_environment_keys", []),
                        request.secret_environment_keys,
                    ),
                    (
                        "header",
                        previous.get("secret_header_keys", []),
                        request.secret_header_keys,
                    ),
                )
                for key in set(old_keys) - set(new_keys)
            ]
            record = request.model_dump(mode="json", exclude={"version"})
            record["name"] = request.name.strip()
            record["command"] = request.command.strip() if request.command else None
            record["url"] = request.url.strip() if request.url else None
            record.update(
                version=request.version + 1,
                enabled=False,
                approved_digest=None,
                tested_digest=None,
                last_tested_at=None,
                last_test_succeeded=None,
            )
            updated = {**self._records, server_id: record}
            self._write(updated)
            self._records = updated
            self._last_status.pop(server_id, None)
            self._summaries.pop(server_id, None)
        for kind, key in removed:
            try:
                self.credentials.delete(self._secret_id(server_id, key, kind))
            except CredentialStoreError as exc:
                raise McpRegistryError(
                    "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
                ) from exc
        return self.get(server_id)

    @_serialized_lifecycle
    def delete(self, server_id: str) -> None:
        self.disable(server_id)
        with self._lock:
            record = self._record(server_id)
            secret_ids = [
                self._secret_id(server_id, key, kind)
                for kind, keys in (
                    ("environment", record.get("secret_environment_keys", [])),
                    ("header", record.get("secret_header_keys", [])),
                )
                for key in keys
            ]
            updated = dict(self._records)
            del updated[server_id]
            self._write(updated)
            self._records = updated
            self._last_status.pop(server_id, None)
            self._summaries.pop(server_id, None)
        try:
            self.credentials.delete_many(secret_ids)
        except CredentialStoreError as exc:
            raise McpRegistryError(
                "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc
        self.bridge.remove(self._host_id(server_id))

    @_serialized_lifecycle
    def trust(self, server_id: str, command_digest: str) -> McpServer:
        with self._lock:
            record = self._record(server_id)
            current = self._digest(record)
            if command_digest != current:
                raise McpRegistryError(
                    "MCP_TRUST_DIGEST_STALE",
                    "MCP server configuration changed; review it again.",
                    status_code=409,
                )
            approved = {**record, "approved_digest": current}
            updated = {**self._records, server_id: approved}
            self._write(updated)
            self._records = updated
        return self.get(server_id)

    @_serialized_lifecycle
    def put_secret(
        self, server_id: str, key: str, secret: str, *, kind: str = "environment"
    ) -> McpServerSecretStatus:
        with self._lock:
            record = self._record(server_id)
            declared = self._secret_keys(record, kind)
            self._validate_secret_key(key, kind)
            if key not in declared:
                raise McpRegistryError(
                    "MCP_SECRET_NOT_DECLARED",
                    "Secret environment key is not declared in this server configuration.",
                )
        try:
            self.credentials.put(self._secret_id(server_id, key, kind), secret)
        except CredentialStoreError as exc:
            raise McpRegistryError(
                "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc
        return McpServerSecretStatus(key=key, configured=True)

    @_serialized_lifecycle
    def delete_secret(
        self, server_id: str, key: str, *, kind: str = "environment"
    ) -> McpServerSecretStatus:
        record = self._record(server_id)
        if key not in self._secret_keys(record, kind):
            raise McpRegistryError(
                "MCP_SECRET_NOT_DECLARED",
                "Secret environment key is not declared in this server configuration.",
            )
        try:
            self.credentials.delete(self._secret_id(server_id, key, kind))
        except CredentialStoreError as exc:
            raise McpRegistryError(
                "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc
        return McpServerSecretStatus(key=key, configured=False)

    @_serialized_lifecycle
    def test(self, server_id: str) -> McpServer:
        record = self._record(server_id)
        if record.get("enabled"):
            raise McpRegistryError(
                "MCP_SERVER_ALREADY_ENABLED",
                "Disable the MCP server before running an isolated connection test.",
                status_code=409,
            )
        self._require_launch_allowed(record, require_test=False)
        try:
            discovered = self._start(server_id, record)
        except Exception as exc:
            tested_at = datetime.now(UTC)
            failure = {
                "status": PluginHostState.error,
                "error": str(exc),
                "last_tested_at": tested_at,
                "last_test_succeeded": False,
            }
            self._last_status[server_id] = failure
            with self._lock:
                failed_record = {
                    **record,
                    "tested_digest": None,
                    "last_tested_at": tested_at.isoformat(),
                    "last_test_succeeded": False,
                }
                updated = {**self._records, server_id: failed_record}
                self._write(updated)
                self._records = updated
            raise
        status = self.bridge.status(self._host_id(server_id), self._backend(record))
        tested_at = datetime.now(UTC)
        self._last_status[server_id] = {
            "status": PluginHostState.stopped,
            "tools_count": len(discovered),
            "protocol_version": status.protocol_version,
            "remote_server_name": status.server_name,
            "remote_server_version": status.server_version,
            "error": None,
            "last_tested_at": tested_at,
            "last_test_succeeded": True,
        }
        self._summaries[server_id] = self._tool_summaries(discovered)
        self.bridge.stop(self._host_id(server_id))
        with self._lock:
            tested_record = {
                **record,
                "tested_digest": self._digest(record),
                "last_tested_at": tested_at.isoformat(),
                "last_test_succeeded": True,
            }
            updated = {**self._records, server_id: tested_record}
            self._write(updated)
            self._records = updated
        return self.get(server_id)

    @_serialized_lifecycle
    def enable(self, server_id: str) -> McpServer:
        record = self._record(server_id)
        if server_id in self._registered:
            return self.get(server_id)
        self._require_launch_allowed(record, require_test=True)
        discovered = self._start(server_id, record)
        self._summaries[server_id] = self._tool_summaries(discovered)
        registered: list[str] = []
        try:
            for item in discovered:
                self._register(server_id, item)
                registered.append(item.definition.name)
        except Exception:
            for name in registered:
                self.tools.unregister(name)
            self.bridge.stop(self._host_id(server_id))
            raise
        try:
            with self._lock:
                enabled_record = {**record, "enabled": True}
                updated = {**self._records, server_id: enabled_record}
                self._write(updated)
                self._records = updated
                self._registered[server_id] = registered
        except McpRegistryError:
            for name in registered:
                self.tools.unregister(name)
            self.bridge.stop(self._host_id(server_id))
            raise
        return self.get(server_id)

    @_serialized_lifecycle
    def disable(self, server_id: str) -> McpServer:
        with self._lock:
            record = self._record(server_id)
            disabled_record = {**record, "enabled": False}
            updated = {**self._records, server_id: disabled_record}
            self._write(updated)
            self._records = updated
            for name in self._registered.pop(server_id, []):
                self.tools.unregister(name)
            self.bridge.stop(self._host_id(server_id))
        return self.get(server_id)

    @_serialized_lifecycle
    def restore_enabled(self) -> None:
        if not self._records:
            return
        for server_id, record in list(self._records.items()):
            if record.get("enabled"):
                try:
                    self.enable(server_id)
                except (McpRegistryError, ValueError, OSError) as exc:
                    self._records[server_id] = {**record, "enabled": False}
                    self._last_status[server_id] = {
                        "status": PluginHostState.error,
                        "error": str(exc),
                    }
        self._write()

    @_serialized_lifecycle
    def shutdown(self) -> None:
        for server_id in list(self._records):
            for name in self._registered.pop(server_id, []):
                self.tools.unregister(name)
            self.bridge.stop(self._host_id(server_id))

    def _start(self, server_id: str, record: dict[str, Any]) -> list[McpDiscoveredTool]:
        environment = dict(record.get("environment", {}))
        for key in record.get("secret_environment_keys", []):
            try:
                value = self.credentials.resolve(
                    self._secret_id(server_id, key, "environment")
                )
            except CredentialStoreError as exc:
                raise McpRegistryError(
                    "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
                ) from exc
            if value is None:
                raise McpRegistryError(
                    "MCP_SECRET_REQUIRED",
                    f"Secret environment variable is not configured: {key}",
                    status_code=409,
                )
            environment[key] = value
        headers = dict(record.get("headers", {}))
        for key in record.get("secret_header_keys", []):
            try:
                value = self.credentials.resolve(
                    self._secret_id(server_id, key, "header")
                )
            except CredentialStoreError as exc:
                raise McpRegistryError(
                    "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
                ) from exc
            if value is None:
                raise McpRegistryError(
                    "MCP_SECRET_REQUIRED",
                    f"Secret HTTP header is not configured: {key}",
                    status_code=409,
                )
            headers[key] = value
        host_id = self._host_id(server_id)
        self.bridge.remove(host_id)
        try:
            return self.bridge.start(
                host_id,
                self._backend(record),
                self._server_dir(server_id),
                list(record.get("permissions", [])),
                lambda _host, message: self._unavailable(server_id, message),
                command_override=(
                    [record["command"], *record.get("args", [])]
                    if record.get("command")
                    else None
                ),
                environment=environment,
                tool_source="mcp_server",
                transport_kind=record["transport"],
                url=record.get("url"),
                headers=headers,
            )
        except McpBridgeError as exc:
            raise McpRegistryError(
                exc.code, exc.message, status_code=exc.status_code
            ) from exc

    def _register(self, server_id: str, discovered: McpDiscoveredTool) -> None:
        definition = discovered.definition
        model_name = "McpArgs_" + re.sub(r"\W+", "_", definition.name)
        arguments_model = create_model(model_name, __config__=ConfigDict(extra="allow"))

        async def executor(arguments: BaseModel, context: ToolExecutionContext) -> Any:
            return await self.bridge.call_tool(
                self._host_id(server_id),
                discovered.remote_name,
                arguments.model_dump(exclude_unset=True),
                request_id=context.tool_call_id
                or f"{context.run_id}:{definition.name}",
            )

        self.tools.register(definition, arguments_model, executor)

    def _unavailable(self, server_id: str, message: str) -> None:
        with self._lock:
            for name in self._registered.pop(server_id, []):
                self.tools.unregister(name)
            record = self._records.get(server_id)
            if record is not None:
                self._records[server_id] = {**record, "enabled": False}
                self._last_status[server_id] = {
                    "status": PluginHostState.unhealthy,
                    "error": message,
                }
                self._write()

    def _require_launch_allowed(
        self, record: dict[str, Any], *, require_test: bool
    ) -> None:
        digest = self._digest(record)
        if (
            record.get("transport") == McpServerTransport.stdio.value
            and not self.allow_process_launch
        ):
            raise McpRegistryError(
                "MCP_SANDBOX_REQUIRED",
                "Python process launch is disabled outside development until the desktop sandbox is available.",
                status_code=403,
            )
        if record.get("approved_digest") != digest:
            raise McpRegistryError(
                "MCP_TRUST_APPROVAL_REQUIRED",
                "Review and approve the current MCP connection before testing or enabling it.",
                status_code=409,
            )
        if require_test and record.get("tested_digest") != digest:
            raise McpRegistryError(
                "MCP_CONNECTION_TEST_REQUIRED",
                "Test the current MCP configuration successfully before enabling it.",
                status_code=409,
            )

    def _public(self, server_id: str, record: dict[str, Any]) -> McpServer:
        digest = self._digest(record)
        backend = self._backend(record)
        status = self.bridge.status(self._host_id(server_id), backend)
        cached = self._last_status.get(server_id, {})
        return McpServer(
            server_id=server_id,
            version=record.get("version", 1),
            name=record["name"],
            transport=record["transport"],
            command=record.get("command"),
            args=list(record.get("args", [])),
            url=record.get("url"),
            headers=dict(record.get("headers", {})),
            environment=dict(record.get("environment", {})),
            secret_environment={
                key: self._secret_configured(server_id, key)
                for key in record.get("secret_environment_keys", [])
            },
            secret_headers={
                key: self._secret_configured(server_id, key, "header")
                for key in record.get("secret_header_keys", [])
            },
            permissions=list(record.get("permissions", [])),
            startup_timeout_seconds=backend.startup_timeout_seconds,
            tool_timeout_seconds=backend.tool_timeout_seconds,
            enabled=bool(record.get("enabled")),
            trusted=record.get("approved_digest") == digest,
            command_digest=digest,
            command_summary=self._summary(record),
            status=status.status
            if record.get("enabled")
            else cached.get("status", PluginHostState.stopped),
            tools_count=status.tools_count
            if record.get("enabled")
            else cached.get("tools_count", 0),
            protocol_version=status.protocol_version
            if record.get("enabled")
            else cached.get("protocol_version"),
            remote_server_name=status.server_name
            if record.get("enabled")
            else cached.get("remote_server_name"),
            remote_server_version=status.server_version
            if record.get("enabled")
            else cached.get("remote_server_version"),
            error=status.error if record.get("enabled") else cached.get("error"),
            last_tested_at=record.get("last_tested_at") or cached.get("last_tested_at"),
            last_test_succeeded=record.get("last_test_succeeded")
            if record.get("last_test_succeeded") is not None
            else cached.get("last_test_succeeded"),
        )

    def _validate(self, request: McpServerCreateRequest) -> None:
        if not request.name.strip():
            raise McpRegistryError(
                "MCP_SERVER_NAME_INVALID", "MCP server name cannot be blank."
            )
        if request.transport == McpServerTransport.stdio:
            if (
                not request.command
                or not request.command.strip()
                or "\x00" in request.command
            ):
                raise McpRegistryError(
                    "MCP_COMMAND_INVALID", "MCP executable is invalid."
                )
            if request.url or request.headers or request.secret_header_keys:
                raise McpRegistryError(
                    "MCP_CONFIG_INVALID",
                    "stdio configuration cannot contain HTTP fields.",
                )
            if any("\x00" in arg for arg in request.args):
                raise McpRegistryError(
                    "MCP_COMMAND_INVALID", "MCP argument contains a null byte."
                )
        else:
            self._validate_http_url(request.url)
            if (
                request.command
                or request.args
                or request.environment
                or request.secret_environment_keys
            ):
                raise McpRegistryError(
                    "MCP_CONFIG_INVALID",
                    "HTTP configuration cannot contain stdio fields.",
                )
        for key in [*request.environment, *request.secret_environment_keys]:
            self._validate_environment_key(key)
        if set(request.environment) & set(request.secret_environment_keys):
            raise McpRegistryError(
                "MCP_ENVIRONMENT_INVALID",
                "An environment key cannot be both plain and secret.",
            )
        plain_headers = {key.casefold() for key in request.headers}
        secret_headers = {key.casefold() for key in request.secret_header_keys}
        for key in [*request.headers, *request.secret_header_keys]:
            self._validate_header_key(key)
        if any(
            "\r" in value or "\n" in value or "\x00" in value
            for value in request.headers.values()
        ):
            raise McpRegistryError(
                "MCP_HEADER_INVALID", "HTTP header value contains control characters."
            )
        if plain_headers & secret_headers:
            raise McpRegistryError(
                "MCP_HEADER_INVALID", "An HTTP header cannot be both plain and secret."
            )
        unknown_permissions = set(request.permissions) - KNOWN_PERMISSIONS
        if unknown_permissions:
            raise McpRegistryError(
                "MCP_PERMISSION_INVALID",
                f"Unknown MCP permission: {min(unknown_permissions)}",
            )

    @staticmethod
    def _validate_environment_key(key: str) -> None:
        if not _ENVIRONMENT_KEY.fullmatch(key):
            raise McpRegistryError(
                "MCP_ENVIRONMENT_INVALID", f"Invalid environment variable name: {key}"
            )

    @staticmethod
    def _validate_header_key(key: str) -> None:
        if not _HEADER_KEY.fullmatch(key) or key.casefold() in _RESERVED_HEADERS:
            raise McpRegistryError(
                "MCP_HEADER_INVALID", f"Invalid or reserved HTTP header: {key}"
            )

    @staticmethod
    def _validate_http_url(url: str | None) -> None:
        if not url:
            raise McpRegistryError("MCP_URL_INVALID", "MCP HTTP URL is required.")
        parts = urlsplit(url.strip())
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise McpRegistryError(
                "MCP_URL_INVALID",
                "MCP URL must be an HTTP(S) URL without credentials or fragments.",
            )

    @staticmethod
    def _backend(record: dict[str, Any]) -> PluginBackend:
        return PluginBackend(
            type="mcp",
            transport="stdio",
            command=record.get("command") or "http",
            args=record.get("args", []),
            startup_timeout_seconds=record.get("startup_timeout_seconds", 15),
            tool_timeout_seconds=record.get("tool_timeout_seconds", 30),
        )

    @staticmethod
    def _host_id(server_id: str) -> str:
        return f"mcp.{server_id}"

    def _server_dir(self, server_id: str) -> Path:
        path = self.data_dir / "mcp" / "workdirs" / server_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _digest(record: dict[str, Any]) -> str:
        executable = {
            key: record.get(key)
            for key in (
                "transport",
                "command",
                "args",
                "environment",
                "secret_environment_keys",
                "url",
                "headers",
                "secret_header_keys",
                "permissions",
            )
        }
        return hashlib.sha256(
            json.dumps(
                executable, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()

    @staticmethod
    def _summary(record: dict[str, Any]) -> str:
        if record.get("transport") != McpServerTransport.stdio.value:
            header_names = sorted(
                [*record.get("headers", {}), *record.get("secret_header_keys", [])],
                key=str.casefold,
            )
            suffix = f" headers={','.join(header_names)}" if header_names else ""
            return f"{record.get('transport')} {record.get('url') or ''}{suffix}"
        return " ".join(
            [
                record.get("command") or "",
                *[
                    json.dumps(arg, ensure_ascii=False)
                    for arg in record.get("args", [])
                ],
            ]
        )

    @staticmethod
    def _secret_id(server_id: str, key: str, kind: str = "environment") -> str:
        suffix = hashlib.sha256(f"{kind}\0{key.casefold()}".encode()).hexdigest()[:20]
        return f"mcp.{server_id}.{suffix}"

    def _secret_configured(
        self, server_id: str, key: str, kind: str = "environment"
    ) -> bool:
        try:
            return self.credentials.has(self._secret_id(server_id, key, kind))
        except CredentialStoreError as exc:
            raise McpRegistryError(
                "MCP_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc

    @staticmethod
    def _secret_keys(record: dict[str, Any], kind: str) -> list[str]:
        if kind == "environment":
            return list(record.get("secret_environment_keys", []))
        if kind == "header":
            return list(record.get("secret_header_keys", []))
        raise McpRegistryError("MCP_SECRET_KIND_INVALID", "Unknown MCP secret kind.")

    @staticmethod
    def _validate_secret_key(key: str, kind: str) -> None:
        if kind == "environment":
            McpServerRegistry._validate_environment_key(key)
        elif kind == "header":
            McpServerRegistry._validate_header_key(key)
        else:
            raise McpRegistryError(
                "MCP_SECRET_KIND_INVALID", "Unknown MCP secret kind."
            )

    @staticmethod
    def _tool_summaries(discovered: list[McpDiscoveredTool]) -> list[McpToolSummary]:
        return [
            McpToolSummary(
                name=item.definition.name,
                remote_name=item.remote_name,
                description=item.definition.description,
                permission=item.definition.permission,
            )
            for item in discovered
        ]

    def _record(self, server_id: str) -> dict[str, Any]:
        try:
            return self._records[server_id]
        except KeyError as exc:
            raise McpRegistryError(
                "MCP_SERVER_NOT_FOUND",
                "MCP server configuration was not found.",
                status_code=404,
            ) from exc

    @property
    def _path(self) -> Path:
        return self.data_dir / "mcp" / "servers.json"

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise McpRegistryError(
                "MCP_REGISTRY_INVALID",
                "MCP server registry cannot be loaded.",
                status_code=500,
            ) from exc
        if not isinstance(value, dict):
            raise McpRegistryError(
                "MCP_REGISTRY_INVALID",
                "MCP server registry has an invalid format.",
                status_code=500,
            )
        return value

    def _write(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        temporary = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    records if records is not None else self._records,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise McpRegistryError(
                "MCP_REGISTRY_WRITE_FAILED",
                "MCP server registry cannot be written.",
                status_code=500,
            ) from exc
