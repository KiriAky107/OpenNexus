import sys

import pytest

from app.agent.tools import ToolRegistry
from app.config import BACKEND_DIR, get_settings
from app.contracts import McpServerCreateRequest, McpServerUpdateRequest
from app.extensions.mcp_registry import McpRegistryError, McpServerRegistry
from app.providers.credentials import EncryptedCredentialStore

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


def test_update_disables_server_and_revokes_command_trust() -> None:
    service = registry()
    created = service.create(request(secret_environment_keys=[]))
    service.trust(created.server_id, created.command_digest)
    enabled = service.enable(created.server_id)
    assert enabled.enabled is True
    assert any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in service.tools.definitions()
    )

    updated = service.update(
        created.server_id,
        McpServerUpdateRequest(
            **request(name="Changed", secret_environment_keys=[]).model_dump()
        ),
    )
    assert updated.enabled is False
    assert updated.trusted is False
    assert not any(
        item.name.startswith(f"mcp.{created.server_id}.")
        for item in service.tools.definitions()
    )
    service.shutdown()


def test_production_rejects_process_launch_even_after_approval() -> None:
    service = registry(launch=False)
    created = service.create(request(secret_environment_keys=[]))
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.enable(created.server_id)
    assert error.value.code == "MCP_SANDBOX_REQUIRED"


def test_non_stdio_transport_is_explicitly_reserved() -> None:
    service = registry()
    created = service.create(
        request(
            transport="streamable_http",
            command="https://example.invalid/mcp",
            secret_environment_keys=[],
        )
    )
    service.trust(created.server_id, created.command_digest)
    with pytest.raises(McpRegistryError) as error:
        service.test(created.server_id)
    assert error.value.code == "MCP_TRANSPORT_UNSUPPORTED"


def test_enabled_server_is_restored_from_persisted_registry() -> None:
    first = registry()
    created = first.create(request(secret_environment_keys=[]))
    first.trust(created.server_id, created.command_digest)
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
