import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.contracts import (
    McpServerSecretStatus,
    McpServerSecretWriteRequest,
    ProviderCreateRequest,
    ProviderType,
    ProviderUpdateRequest,
    TaskCreateRequest,
    TaskStatus,
    TaskUpdateRequest,
)
from app.main import health, service_status
from app.routes import (
    create_provider,
    create_task,
    delete_provider,
    delete_task,
    get_index_status,
    get_provider,
    get_task,
    list_notes,
    list_plugins,
    list_provider_presets,
    list_providers,
    list_skills,
    list_tasks,
    update_provider,
    update_task,
)


def test_mcp_secret_routes_offload_blocking_lifecycle_work(monkeypatch) -> None:
    from app import routes

    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    class FakeMcpRegistry:
        def put_secret(self, server_id, key, secret, *, kind):
            worker_threads.append(threading.get_ident())
            return McpServerSecretStatus(key=key, configured=True)

        def delete_secret(self, server_id, key, *, kind):
            worker_threads.append(threading.get_ident())
            return McpServerSecretStatus(key=key, configured=False)

    monkeypatch.setattr(
        routes,
        "container",
        SimpleNamespace(mcp_servers=FakeMcpRegistry()),
    )
    written = asyncio.run(
        routes.put_mcp_server_secret(
            "server-1",
            "TOKEN",
            McpServerSecretWriteRequest(secret="hidden"),
            kind="environment",
        )
    )
    deleted = asyncio.run(
        routes.delete_mcp_server_secret("server-1", "TOKEN", kind="environment")
    )

    assert written.configured is True
    assert deleted.configured is False
    assert worker_threads and all(item != caller_thread for item in worker_threads)


def test_health() -> None:
    response = asyncio.run(health())

    assert response.model_dump() == {"status": "ok"}


def test_mcp_create_and_trust_are_not_executed_on_event_loop(monkeypatch) -> None:
    from app import routes
    from app.contracts import McpServerCreateRequest, McpServerTrustRequest

    caller = threading.get_ident()
    workers = []

    class Registry:
        def create(self, request):
            workers.append(threading.get_ident())
            return "created"

        def trust(self, server_id, digest):
            workers.append(threading.get_ident())
            return "trusted"

    monkeypatch.setattr(routes, "container", SimpleNamespace(mcp_servers=Registry()))
    assert (
        asyncio.run(
            routes.create_mcp_server(McpServerCreateRequest(name="test", command="uvx"))
        )
        == "created"
    )
    assert (
        asyncio.run(
            routes.trust_mcp_server(
                "test", McpServerTrustRequest(command_digest="a" * 64)
            )
        )
        == "trusted"
    )
    assert len(workers) == 2
    assert all(worker != caller for worker in workers)


def test_mcp_split_config_and_secret_requests_persist_without_plaintext(
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    from app import routes
    from app.agent.tools import ToolRegistry
    from app.config import get_settings
    from app.extensions.mcp_registry import McpServerRegistry
    from app.main import app
    from app.providers.credentials import EncryptedCredentialStore

    service = McpServerRegistry(
        ToolRegistry(),
        EncryptedCredentialStore(),
        get_settings().data_dir,
        allow_process_launch=True,
    )
    monkeypatch.setattr(routes, "container", SimpleNamespace(mcp_servers=service))
    client = TestClient(app)
    config = {
        "name": "MiniMax configuration test",
        "command": "uvx",
        "environment": {"MINIMAX_API_HOST": "https://api.minimaxi.com"},
        "secret_environment_keys": ["MINIMAX_API_KEY"],
        "startup_timeout_seconds": 120,
        "tool_timeout_seconds": 300,
    }
    # Reproduce the old frontend payload. The backend still enforces separation.
    invalid = client.post(
        "/api/mcp/servers",
        json={
            **config,
            "environment": {
                **config["environment"],
                "MINIMAX_API_KEY": "synthetic-only",
            },
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "MCP_ENVIRONMENT_INVALID"
    created = client.post("/api/mcp/servers", json=config)
    assert created.status_code == 201
    server_id = created.json()["server_id"]
    saved = client.put(
        f"/api/mcp/servers/{server_id}/secrets/MINIMAX_API_KEY",
        json={"secret": "synthetic-only"},
    )
    assert saved.status_code == 200
    current = client.get(f"/api/mcp/servers/{server_id}")
    assert current.json()["secret_environment"] == {"MINIMAX_API_KEY": True}
    assert "synthetic-only" not in current.text
    assert "synthetic-only" not in service._path.read_text(encoding="utf-8")
    _, credentials_path = service.credentials._paths()
    assert "synthetic-only" not in credentials_path.read_text(encoding="utf-8")
    assert not current.json()["enabled"]  # Saving never starts a third-party process.
    client.close()


@pytest.mark.parametrize("operation", ["create", "trust"])
def test_mcp_lifecycle_lock_contention_keeps_event_loop_responsive(
    monkeypatch,
    operation,
) -> None:
    from app import routes
    from app.agent.tools import ToolRegistry
    from app.config import get_settings
    from app.contracts import McpServerCreateRequest, McpServerTrustRequest
    from app.extensions.mcp_registry import McpServerRegistry
    from app.providers.credentials import EncryptedCredentialStore

    service = McpServerRegistry(
        ToolRegistry(),
        EncryptedCredentialStore(),
        get_settings().data_dir,
        allow_process_launch=True,
    )
    request = McpServerCreateRequest(
        name="Lock contention fixture", command="not-executed"
    )
    server = service.create(request)
    monkeypatch.setattr(routes, "container", SimpleNamespace(mcp_servers=service))
    entered = threading.Event()
    locked = threading.Event()
    release = threading.Event()
    original = getattr(service, operation)

    def observed(*args):
        entered.set()
        return original(*args)

    def hold_lifecycle_lock():
        with service._lifecycle_lock:
            locked.set()
            release.wait(timeout=5)

    monkeypatch.setattr(service, operation, observed)
    holder = threading.Thread(target=hold_lifecycle_lock, daemon=True)
    holder.start()
    # An independent watchdog lets the test fail rather than hang if a regression
    # blocks the event loop itself (an asyncio timeout alone cannot catch that).
    watchdog = threading.Timer(5, release.set)
    watchdog.start()

    async def exercise():
        pending = asyncio.create_task(
            routes.create_mcp_server(request)
            if operation == "create"
            else routes.trust_mcp_server(
                server.server_id,
                McpServerTrustRequest(command_digest=server.command_digest),
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not pending.done()
            assert not release.is_set()
            assert (await health()).status == "ok"
        finally:
            release.set()
            await pending

    try:
        assert locked.wait(timeout=2)
        asyncio.run(exercise())
    finally:
        release.set()
        watchdog.cancel()
        holder.join(timeout=2)


def test_service_status() -> None:
    response = asyncio.run(service_status())

    assert response.name == "Notes Agent AI Core"
    assert response.status == "ok"


def test_core_collections_are_typed() -> None:
    notes = asyncio.run(list_notes(limit=20, offset=0, folder=None, tag=None))
    skills = asyncio.run(list_skills())
    plugins = asyncio.run(list_plugins())
    providers = asyncio.run(list_providers())
    index = asyncio.run(get_index_status())

    assert notes.items == []
    assert notes.page.limit == 20
    assert [skill.manifest.skill_id for skill in skills.items] == [
        "knowledge-assistant"
    ]
    assert skills.items[0].status == "ready"
    assert [plugin.manifest.plugin_id for plugin in plugins.items] == ["text-tools"]
    assert plugins.items[0].status == "ready"
    assert [provider.provider_id for provider in providers.items] == ["mock"]
    assert index.status == "idle"


def test_provider_presets_include_openai_and_deepseek() -> None:
    presets = asyncio.run(list_provider_presets())
    by_id = {item.preset_id: item for item in presets.items}

    assert by_id["openai"].base_url == "https://api.openai.com/v1"
    assert by_id["deepseek"].base_url == "https://api.deepseek.com"
    assert by_id["deepseek"].provider_type == ProviderType.openai_compatible
    assert by_id["deepseek"].default_credential_id == "deepseek"


def test_provider_presets_static_route_precedes_provider_id_route() -> None:
    from app.routes import router

    get_paths = [
        route.path
        for route in router.routes
        if "GET" in getattr(route, "methods", set())
    ]

    assert get_paths.index("/api/providers/presets") < get_paths.index(
        "/api/providers/{provider_id}"
    )


def test_openapi_contains_documented_frontend_interfaces() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    expected_paths = {
        "/api/notes/{note_id}",
        "/api/search",
        "/api/chat",
        "/api/agent/runs",
        "/api/agent/runs/{run_id}/cancel",
        "/api/agent/runs/{run_id}/events",
        "/api/agent/runs/{run_id}/trace",
        "/api/skills",
        "/api/plugins",
        "/api/plugins/install",
        "/api/plugins/{plugin_id}/host",
        "/api/plugins/{plugin_id}/host/restart",
        "/api/plugin-contributions/commands",
        "/api/plugin-contributions/commands/{command_id}/execute",
        "/api/plugins/{plugin_id}/settings",
        "/api/plugins/{plugin_id}/settings/{key}/secret",
        "/api/plugins/{plugin_id}/enable",
        "/api/plugins/{plugin_id}/disable",
        "/api/mcp/servers",
        "/api/mcp/servers/{server_id}",
        "/api/mcp/servers/{server_id}/tools",
        "/api/mcp/servers/{server_id}/trust",
        "/api/mcp/servers/{server_id}/test",
        "/api/mcp/servers/{server_id}/enable",
        "/api/mcp/servers/{server_id}/disable",
        "/api/mcp/servers/{server_id}/secrets/{key}",
        "/api/providers/test",
        "/api/providers/presets",
        "/api/credentials/{credential_id}",
        "/api/index/rebuild",
    }

    assert expected_paths <= paths.keys()


def test_provider_configuration_lifecycle() -> None:
    created = asyncio.run(
        create_provider(
            ProviderCreateRequest(
                provider_type=ProviderType.ollama,
                name="Local Ollama",
                base_url="http://127.0.0.1:11434",
                default_model="qwen3:latest",
            )
        )
    )
    fetched = asyncio.run(get_provider(created.provider_id))
    disabled = asyncio.run(
        update_provider(created.provider_id, ProviderUpdateRequest(enabled=False))
    )
    deleted = asyncio.run(delete_provider(created.provider_id))

    assert fetched.provider_type == ProviderType.ollama
    assert disabled.enabled is False
    assert deleted.resource_id == created.provider_id


def test_provider_patch_can_clear_nullable_fields() -> None:
    created = asyncio.run(
        create_provider(
            ProviderCreateRequest(
                provider_type=ProviderType.ollama,
                name="Clearable",
                base_url="http://127.0.0.1:11434",
                default_model="qwen",
                credential_id="unused",
            )
        )
    )
    try:
        cleared = asyncio.run(
            update_provider(
                created.provider_id,
                ProviderUpdateRequest(
                    base_url=None, default_model=None, credential_id=None
                ),
            )
        )
        assert cleared.base_url is None
        assert cleared.default_model is None
        assert cleared.credential_id is None
    finally:
        asyncio.run(delete_provider(created.provider_id))


def test_task_lifecycle_is_persistent() -> None:
    created = asyncio.run(create_task(TaskCreateRequest(title="审阅修复")))
    fetched = asyncio.run(get_task(created.task_id))
    updated = asyncio.run(
        update_task(created.task_id, TaskUpdateRequest(status=TaskStatus.done))
    )
    listed = asyncio.run(list_tasks(limit=20, offset=0))
    deleted = asyncio.run(delete_task(created.task_id))

    assert fetched.title == "审阅修复"
    assert updated.status == TaskStatus.done
    assert any(item.task_id == created.task_id for item in listed.items)
    assert deleted.resource_id == created.task_id
