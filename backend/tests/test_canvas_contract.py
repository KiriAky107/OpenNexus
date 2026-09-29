import asyncio
import json

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services import canvas_service, workspace_service


def _content(extra=None):
    return json.dumps({"nodes": [{"id": "n", "type": "text", "x": 0, "y": 0,
                                   "width": 200, "height": 100, "text": "hello", "custom": extra}],
                       "edges": [], "extension": {"preserve": True}}, ensure_ascii=False)


def test_canvas_round_trip_and_revision_conflict() -> None:
    vault = get_settings().vault_path
    vault.mkdir(parents=True)
    created = asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(path="map.canvas", content=_content(1))))
    assert created.content_hash == canvas_service.read("/map.canvas").content_hash
    assert workspace_service.get_workspace_tree()[0].path == "/map.canvas"
    assert workspace_service.get_workspace_info().indexed_note_count == 0
    assert workspace_service.get_workspace_info().file_count == 1
    changed = asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(
        path="map.canvas", expected_content_hash=created.content_hash, content=_content(2))))
    assert json.loads(changed.content)["nodes"][0]["custom"] == 2
    with pytest.raises(ApiError) as error:
        asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(
            path="map.canvas", expected_content_hash=created.content_hash, content=_content(3))))
    assert error.value.code == "CANVAS_CONTENT_CONFLICT"
    assert canvas_service.read("map.canvas").content == changed.content
    moved = asyncio.run(canvas_service.move(canvas_service.CanvasMoveRequest(
        path="map.canvas", destination="new.canvas", expected_content_hash=changed.content_hash)))
    assert moved.content_hash == changed.content_hash
    asyncio.run(canvas_service.delete(canvas_service.CanvasDeleteRequest(
        path="new.canvas", expected_content_hash=moved.content_hash)))
    assert not (vault / "new.canvas").exists()


def test_invalid_canvas_never_overwrites() -> None:
    vault = get_settings().vault_path
    vault.mkdir(parents=True)
    created = asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(path="map.canvas", content=_content())))
    for bad in ('{"nodes":{}}', '{"nodes":[{"id":"a","type":"file","x":0,"y":0,"width":1,"height":1,"file":"../secret"}]}', '{"nodes":[{"id":"a","type":"file","x":0,"y":0,"width":1,"height":1,"file":"%2e%2e/secret"}]}', 'NaN'):
        with pytest.raises(ApiError):
            asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(
                path="map.canvas", expected_content_hash=created.content_hash, content=bad)))
    assert canvas_service.read("map.canvas").content == created.content


def test_canvas_float_coordinates_and_safe_links() -> None:
    canvas_service.validate(json.dumps({"nodes": [{"id": "link", "type": "link", "x": 0.5,
        "y": -2.25, "width": 100.5, "height": 50, "url": "https://example.com/a"}]}).encode())
    canvas_service.validate(json.dumps({"nodes": [{"id": "link", "type": "link", "x": 0,
        "y": 0, "width": 100, "height": 50, "url": "https://[::1]/a"}]}).encode())
    with pytest.raises(ApiError):
        canvas_service.validate(json.dumps({"nodes": [{"id": "link", "type": "link", "x": 0,
            "y": 0, "width": 100, "height": 50, "url": "https://example.com:0"}]}).encode())
    with pytest.raises(ApiError):
        canvas_service.validate(json.dumps({"nodes": [{"id": "link", "type": "link", "x": 0,
            "y": 0, "width": 100, "height": 50, "url": "https://user:pass@example.com"}]}).encode())


def test_canvas_path_is_vault_scoped() -> None:
    vault = get_settings().vault_path
    vault.mkdir(parents=True)
    for path in ("../outside.canvas", "/../../outside.canvas", "C:/outside.canvas", ".ainote/hidden.canvas"):
        with pytest.raises(ApiError):
            asyncio.run(canvas_service.write(canvas_service.CanvasWriteRequest(path=path, content=_content())))
