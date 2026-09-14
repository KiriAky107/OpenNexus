from fastapi.testclient import TestClient

from app.config import get_settings
from app.database.db import connect
from app.main import app


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image"


def test_workspace_image_is_content_addressed_and_linked() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/workspace/assets",
            params={"filename": "截图.png", "note_id": "note-1", "note_path": "课程/笔记.md", "source": "paste"},
            content=PNG,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200
        asset = response.json()
        target = get_settings().vault_path / asset["path"]
        assert target.read_bytes() == PNG
        assert asset["path"].startswith("attachments/")

        content = client.get("/api/workspace/assets/content", params={"path": asset["path"]})
        assert content.status_code == 200
        assert content.content == PNG
        assert content.headers["content-type"] == "image/png"

        duplicate = client.post(
            "/api/workspace/assets",
            params={"filename": "same.png", "note_id": "note-2", "note_path": "另一篇.md", "source": "upload"},
            content=PNG,
        )
        assert duplicate.json()["asset_id"] == asset["asset_id"]
        conn = connect()
        try:
            assert conn.execute("SELECT count(*) FROM workspace_assets").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM workspace_asset_links").fetchone()[0] == 2
        finally:
            conn.close()

        # 模拟另一台设备只同步 Vault 文件；读取时会重建本机派生元数据。
        conn = connect()
        try:
            conn.execute("DELETE FROM workspace_asset_links")
            conn.execute("DELETE FROM workspace_assets")
        finally:
            conn.close()
        restored = client.get(
            "/api/workspace/assets/content",
            params={"path": asset["path"], "note_id": "synced-note", "note_path": "同步/笔记.md"},
        )
        assert restored.status_code == 200
        conn = connect()
        try:
            assert conn.execute("SELECT source FROM workspace_asset_links").fetchone()[0] == "sync"
        finally:
            conn.close()


def test_workspace_image_rejects_unknown_content_and_traversal() -> None:
    with TestClient(app) as client:
        unsupported = client.post(
            "/api/workspace/assets",
            params={"filename": "fake.png", "note_path": "笔记.md", "source": "upload"},
            content=b"not an image",
        )
        assert unsupported.status_code == 415
        traversal = client.get("/api/workspace/assets/content", params={"path": "../secret.png"})
        assert traversal.status_code == 400
