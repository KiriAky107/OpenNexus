import asyncio

from app.main import health, service_status
from app.routes import get_index_status, list_notes, list_plugins, list_providers, list_skills
from app.routes import create_provider, delete_provider, get_provider, update_provider
from app.contracts import ProviderCreateRequest, ProviderType, ProviderUpdateRequest


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
        "/api/skills",
        "/api/plugins",
        "/api/plugins/install",
        "/api/plugins/{plugin_id}/enable",
        "/api/plugins/{plugin_id}/disable",
        "/api/providers/test",
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
