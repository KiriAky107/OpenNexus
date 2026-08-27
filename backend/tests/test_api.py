import asyncio

from app.main import health, service_status


def test_health() -> None:
    response = asyncio.run(health())

    assert response.model_dump() == {"status": "ok"}


def test_service_status() -> None:
    response = asyncio.run(service_status())

    assert response.name == "Notes Agent AI Core"
    assert response.status == "ok"
