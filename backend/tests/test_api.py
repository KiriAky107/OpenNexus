import asyncio

from app.main import health, service_status
from app.routes import (
    get_index_status,
    list_notes,
    list_plugins,
    list_provider_presets,
    list_providers,
    list_skills,
)
from app.routes import (
    create_provider,
    create_task,
    delete_provider,
    delete_task,
    get_provider,
    get_task,
    list_tasks,
    update_provider,
    update_task,
)
from app.contracts import (
    ProviderCreateRequest,
    ProviderType,
    ProviderUpdateRequest,
    TaskCreateRequest,
    TaskStatus,
    TaskUpdateRequest,
)


def test_health() -> None:
    response = asyncio.run(health())

    assert response.model_dump() == {"status": "ok"}


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
    assert [skill.manifest.skill_id for skill in skills.items] == ["knowledge-assistant"]
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

    get_paths = [route.path for route in router.routes if "GET" in getattr(route, "methods", set())]

    assert get_paths.index("/api/providers/presets") < get_paths.index("/api/providers/{provider_id}")


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
        "/api/plugins/{plugin_id}/enable",
        "/api/plugins/{plugin_id}/disable",
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
