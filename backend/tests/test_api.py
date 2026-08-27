import asyncio

from app.main import health, service_status
from app.routes import get_index_status, list_notes, list_plugins, list_providers, list_skills


def test_health() -> None:
    response = asyncio.run(health())

    assert response.model_dump() == {"status": "ok"}


def test_service_status() -> None:
    response = asyncio.run(service_status())

    assert response.name == "Notes Agent AI Core"
    assert response.status == "ok"


def test_query_shells_are_empty_and_typed() -> None:
    notes = asyncio.run(list_notes(limit=20, offset=0, folder=None, tag=None))
    skills = asyncio.run(list_skills())
    plugins = asyncio.run(list_plugins())
    providers = asyncio.run(list_providers())
    index = asyncio.run(get_index_status())

    assert notes.items == []
    assert notes.page.limit == 20
    assert skills.items == []
    assert plugins.items == []
    assert providers.items == []
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
