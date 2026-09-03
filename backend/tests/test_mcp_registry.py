import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.config import BACKEND_DIR, get_settings
from app.contracts import McpServerCreateRequest, McpServerUpdateRequest, ToolCall
from app.extensions.mcp import McpLegacySseClient
from app.extensions.mcp_registry import McpRegistryError, McpServerRegistry
from app.providers.credentials import CredentialStoreError, EncryptedCredentialStore

SERVER = BACKEND_DIR / "extensions" / "fixtures" / "mcp-echo" / "server.py"


def request(**overrides) -> McpServerCreateRequest:
    values = {
        "name": "Echo MCP",
        "command": sys.executable,
        "args": [str(SERVER)],
        "permissions": ["notes.read", "secrets.use"],
        "secret_environment_keys": ["TEST_MCP_SECRET"],
    }
    values.update(overrides)
    return McpServerCreateRequest(**values)


def registry(*, launch: bool = True) -> McpServerRegistry:
    return McpServerRegistry(
        ToolRegistry(),
        EncryptedCredentialStore(),
        get_settings().data_dir,
        allow_process_launch=launch,
    )


def test_registry_requires_current_trust_and_never_returns_secret() -> None:
    service = registry()
    created = service.create(request())
    assert created.trusted is False
    assert created.secret_environment == {"TEST_MCP_SECRET": False}

    service.put_secret(created.server_id, "TEST_MCP_SECRET", "do-not-return")
    configured = service.get(created.server_id)
    assert configured.secret_environment == {"TEST_MCP_SECRET": True}
    assert "do-not-return" not in configured.model_dump_json()

    with pytest.raises(McpRegistryError, match="approve"):
        service.test(created.server_id)

    service.trust(created.server_id, created.command_digest)
    tested = service.test(created.server_id)
    assert tested.status == "stopped"
    assert tested.last_test_succeeded is True
    assert tested.tools_count > 0
    service.shutdown()


def test_secret_change_disables_server_and_requires_a_new_connection_test() -> None:
    service = registry()
    created = service.create(request())
    service.put_secret(created.server_id, "TEST_MCP_SECRET", "first")
    service.trust(created.server_id, created.command_digest)
    service.test(created.server_id)
    service.enable(created.server_id)

    service.put_secret(created.server_id, "TEST_MCP_SECRET", "second")
    current = service.get(created.server_id)
    assert current.enabled is False
    assert current.last_test_succeeded is None
    assert not any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in service.tools.definitions()
    )
    with pytest.raises(McpRegistryError) as error:
        service.enable(created.server_id)
    assert error.value.code == "MCP_CONNECTION_TEST_REQUIRED"
    service.shutdown()


def test_update_disables_server_and_revokes_command_trust() -> None:
    service = registry()
    created = service.create(request(secret_environment_keys=[]))
    service.trust(created.server_id, created.command_digest)
    service.test(created.server_id)
    enabled = service.enable(created.server_id)
    assert enabled.enabled is True
    assert any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in service.tools.definitions()
    )

    updated = service.update(
        created.server_id,
        McpServerUpdateRequest(
            **request(name="Changed", secret_environment_keys=[]).model_dump(),
            version=enabled.version,
        ),
    )
    assert updated.enabled is False
    assert updated.trusted is False
    assert not any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in service.tools.definitions()
    )
    service.shutdown()


def test_update_remains_retryable_when_removed_secret_cleanup_fails(monkeypatch) -> None:
    service = registry()
    created = service.create(request())
    service.put_secret(created.server_id, "TEST_MCP_SECRET", "keep-until-retry")

    def fail_delete_many(_secret_ids: list[str]) -> set[str]:
        raise CredentialStoreError("credential store unavailable")

    monkeypatch.setattr(service.credentials, "delete_many", fail_delete_many)
    with pytest.raises(McpRegistryError) as error:
        service.update(
            created.server_id,
            McpServerUpdateRequest(
                **request(secret_environment_keys=[]).model_dump(),
                version=created.version,
            ),
        )

    current = service.get(created.server_id)
    assert error.value.code == "MCP_SECRET_STORE_ERROR"
    assert current.version == created.version
    assert current.secret_environment == {"TEST_MCP_SECRET": True}
    service.shutdown()


def test_delete_keeps_server_retryable_when_secret_cleanup_fails(monkeypatch) -> None:
    service = registry()
    created = service.create(request())
    service.put_secret(created.server_id, "TEST_MCP_SECRET", "keep-until-retry")

    def fail_delete_many(_secret_ids: list[str]) -> set[str]:
        raise CredentialStoreError("credential store unavailable")

    monkeypatch.setattr(service.credentials, "delete_many", fail_delete_many)
    with pytest.raises(McpRegistryError) as error:
        service.delete(created.server_id)

    current = service.get(created.server_id)
    assert error.value.code == "MCP_SECRET_STORE_ERROR"
    assert current.server_id == created.server_id
    assert current.secret_environment == {"TEST_MCP_SECRET": True}
    service.shutdown()


def test_unavailable_server_removes_bridge_host(monkeypatch) -> None:
    service = registry()
    created = service.create(request(secret_environment_keys=[]))
    with service._lock:
        service._records[created.server_id] = {
            **service._records[created.server_id],
            "enabled": True,
        }
    removed: list[str] = []
    monkeypatch.setattr(service.bridge, "remove", removed.append)

    service._unavailable(created.server_id, "connection lost")

    current = service.get(created.server_id)
    assert removed == [f"mcp.{created.server_id}"]
    assert current.enabled is False
    assert current.status == "unhealthy"
    service.shutdown()


def test_production_rejects_process_launch_even_after_approval() -> None:
    service = registry(launch=False)
    created = service.create(request(secret_environment_keys=[]))
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.enable(created.server_id)
    assert error.value.code == "MCP_SANDBOX_REQUIRED"


def test_enable_requires_successful_test_and_update_checks_version() -> None:
    service = registry()
    created = service.create(request(secret_environment_keys=[]))
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.enable(created.server_id)
    assert error.value.code == "MCP_CONNECTION_TEST_REQUIRED"

    with pytest.raises(McpRegistryError) as error:
        service.update(
            created.server_id,
            McpServerUpdateRequest(
                **request(secret_environment_keys=[]).model_dump(), version=99
            ),
        )
    assert error.value.code == "MCP_SERVER_VERSION_CONFLICT"


def test_http_transport_rejects_invalid_cross_transport_fields() -> None:
    service = registry()
    with pytest.raises(McpRegistryError) as error:
        service.create(
            request(
                transport="streamable_http",
                url="https://example.invalid/mcp",
                secret_environment_keys=[],
            )
        )
    assert error.value.code == "MCP_CONFIG_INVALID"


def test_registry_rejects_corrupt_persisted_json(tmp_path) -> None:
    path = tmp_path / "mcp"
    path.mkdir()
    (path / "servers.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(McpRegistryError) as error:
        McpServerRegistry(
            ToolRegistry(),
            EncryptedCredentialStore(),
            tmp_path,
            allow_process_launch=True,
        )
    assert error.value.code == "MCP_REGISTRY_INVALID"


def test_registry_rejects_structurally_invalid_record(tmp_path) -> None:
    path = tmp_path / "mcp"
    path.mkdir()
    (path / "servers.json").write_text(
        json.dumps({"server-1": {"name": "Broken", "transport": "stdio"}}),
        encoding="utf-8",
    )
    with pytest.raises(McpRegistryError) as error:
        McpServerRegistry(
            ToolRegistry(),
            EncryptedCredentialStore(),
            tmp_path,
            allow_process_launch=True,
        )
    assert error.value.code == "MCP_REGISTRY_INVALID"


def test_registry_rejects_create_before_exceeding_persisted_limit(
    monkeypatch,
) -> None:
    service = registry()
    service.create(request(name="Only server"))
    monkeypatch.setattr("app.extensions.mcp_registry._MAX_MCP_SERVERS", 1)
    with pytest.raises(McpRegistryError) as error:
        service.create(request(name="One too many"))
    assert error.value.code == "MCP_SERVER_LIMIT_REACHED"
    assert len(service.list()) == 1
    service.shutdown()


def test_stdio_command_is_not_parsed_as_a_shell_string() -> None:
    service = registry()
    created = service.create(
        request(
            command=f'"{sys.executable}" "{SERVER}"',
            args=[],
            secret_environment_keys=[],
        )
    )
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.test(created.server_id)
    assert error.value.code == "PLUGIN_HOST_START_FAILED"
    assert service.get(created.server_id).last_test_succeeded is False
    service.shutdown()


def test_enabled_server_is_restored_from_persisted_registry() -> None:
    first = registry()
    created = first.create(request(secret_environment_keys=[]))
    first.trust(created.server_id, created.command_digest)
    first.test(created.server_id)
    first.enable(created.server_id)
    first.shutdown()

    restored = registry()
    restored.restore_enabled()
    current = restored.get(created.server_id)
    assert current.enabled is True
    assert current.status == "ready"
    assert any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in restored.tools.definitions()
    )
    restored.shutdown()


def test_lifecycle_operations_are_serialized_and_tool_names_are_isolated() -> None:
    service = registry()
    servers = [
        service.create(request(name=f"Echo {index}", secret_environment_keys=[]))
        for index in range(2)
    ]
    for server in servers:
        service.trust(server.server_id, server.command_digest)
        service.test(server.server_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        enabled = list(pool.map(lambda item: service.enable(item.server_id), servers * 2))
    assert all(item.enabled for item in enabled)
    names = [
        item.name
        for item in service.tools.definitions()
        if item.source == "mcp_server"
    ]
    assert len(names) == len(set(names))
    assert all(any(name.startswith(f"mcp.{item.server_id}.") for name in names) for item in servers)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: service.disable(item.server_id), servers * 2))
    assert not any(item.source == "mcp_server" for item in service.tools.definitions())
    service.shutdown()


def _http_result(request_id: int, result: dict) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": request_id, "result": result},
    )


def test_streamable_http_supports_session_headers_secrets_and_tool_summary(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []
    request_timeouts: dict[str, float] = {}

    def handler(request_value: httpx.Request) -> httpx.Response:
        requests.append(request_value)
        if request_value.method == "GET":
            return httpx.Response(405)
        if request_value.method == "DELETE":
            return httpx.Response(405)
        payload = json.loads(request_value.content)
        timeout = request_value.extensions.get("timeout", {}).get("read")
        if isinstance(timeout, (int, float)):
            request_timeouts[payload.get("method", "notification")] = float(timeout)
        if payload.get("method") == "initialize":
            response = _http_result(
                payload["id"],
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "HTTP Fixture", "version": "1"},
                },
            )
            response.headers["MCP-Session-Id"] = "session-test"
            return response
        if payload.get("method") == "tools/list":
            return _http_result(
                payload["id"],
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo over HTTP",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            )
        if payload.get("method") == "tools/call":
            return _http_result(
                payload["id"], {"structuredContent": {"transport": "http"}}
            )
        return httpx.Response(202)

    real_client = httpx.Client
    monkeypatch.setattr(
        "app.extensions.mcp.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    service = registry()
    created = service.create(
        McpServerCreateRequest(
            name="Remote MCP",
            transport="streamable_http",
            url="https://mcp.example.test/mcp",
            headers={"X-Client": "NotesAgent"},
            secret_header_keys=["Authorization"],
        )
    )
    service.put_secret(
        created.server_id, "Authorization", "Bearer hidden", kind="header"
    )
    service.trust(created.server_id, created.command_digest)
    tested = service.test(created.server_id)

    assert tested.last_test_succeeded is True
    assert tested.secret_headers == {"Authorization": True}
    assert "Bearer hidden" not in tested.model_dump_json()
    assert service.list_tools(created.server_id)[0].remote_name == "echo"
    assert any(
        request.headers.get("mcp-session-id") == "session-test" for request in requests
    )
    assert any(
        request.headers.get("mcp-protocol-version") == "2025-11-25"
        for request in requests
    )
    assert all(
        request.headers.get("authorization") == "Bearer hidden" for request in requests
    )
    assert request_timeouts["initialize"] == 15
    assert request_timeouts["notifications/initialized"] == 15
    assert request_timeouts["tools/list"] == 15
    enabled = service.enable(created.server_id)
    tool_name = service.list_tools(created.server_id)[0].name
    result = asyncio.run(
        service.tools.execute(
            ToolCall(tool_call_id="call-1", name=tool_name, arguments={}),
            ToolExecutionContext(run_id="run-1"),
        )
    )
    assert enabled.enabled is True
    assert result.success is True
    assert result.output == {"transport": "http"}
    assert request_timeouts["tools/call"] == 30
    service.disable(created.server_id)
    service.shutdown()


class _LegacyEventStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.closed = threading.Event()

    def __iter__(self):
        yield b"event: endpoint\ndata: /messages\n\n"
        time.sleep(0.1)
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Legacy Fixture"},
            },
        }
        yield f"data: {json.dumps(initialize)}\n\n".encode()
        time.sleep(0.1)
        tools = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": []},
        }
        yield f"data: {json.dumps(tools)}\n\n".encode()
        self.closed.wait()

    def close(self) -> None:
        self.closed.set()


def test_legacy_sse_uses_same_origin_endpoint(monkeypatch) -> None:
    posted_urls: list[str] = []
    event_stream = _LegacyEventStream()

    def handler(request_value: httpx.Request) -> httpx.Response:
        if request_value.method == "GET":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=event_stream,
            )
        posted_urls.append(str(request_value.url))
        return httpx.Response(202)

    real_client = httpx.Client
    monkeypatch.setattr(
        "app.extensions.mcp.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    service = registry()
    created = service.create(
        McpServerCreateRequest(
            name="Legacy MCP",
            transport="sse",
            url="https://legacy.example.test/sse",
        )
    )
    service.trust(created.server_id, created.command_digest)
    tested = service.test(created.server_id)
    assert tested.last_test_succeeded is True
    assert posted_urls and all(
        url == "https://legacy.example.test/messages" for url in posted_urls
    )
    service.shutdown()
    event_stream.close()


class _EndingLegacyEventStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"event: endpoint\ndata: /messages\n\n"


def test_legacy_sse_eof_marks_client_unavailable(monkeypatch) -> None:
    def handler(_request_value: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_EndingLegacyEventStream(),
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "app.extensions.mcp.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    broken = threading.Event()
    client = McpLegacySseClient(
        "https://legacy.example.test/sse",
        headers={},
        startup_timeout_seconds=1,
        on_seen=lambda: None,
        on_broken=lambda _message: broken.set(),
        on_tools_changed=lambda: None,
    )
    client.start()
    assert broken.wait(timeout=1)
    client.stop()


class _CrossOriginLegacyEventStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"event: endpoint\ndata: https://attacker.example/messages\n\n"


def test_legacy_sse_rejects_cross_origin_message_endpoint(monkeypatch) -> None:
    def handler(request_value: httpx.Request) -> httpx.Response:
        assert request_value.method == "GET"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_CrossOriginLegacyEventStream(),
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "app.extensions.mcp.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    service = registry()
    created = service.create(
        McpServerCreateRequest(
            name="Unsafe legacy MCP",
            transport="sse",
            url="https://legacy.example.test/sse",
        )
    )
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.test(created.server_id)
    assert error.value.code == "MCP_HTTP_RESPONSE_INVALID"
    service.shutdown()
