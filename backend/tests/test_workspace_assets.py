from fastapi.testclient import TestClient

from app.config import get_settings
from app.database.db import connect
from app.main import app


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image"


def test_ordinary_vault_image_is_readable_and_listed() -> None:
    with TestClient(app) as client:
        target = get_settings().vault_path / '附件' / '课程图片.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG)
        response = client.get('/api/workspace/assets/content', params={'path': '附件/课程图片.png'})
        assert response.status_code == 200
        assert response.content == PNG
        from app.services.workspace_service import _tree
        entries = _tree(get_settings().vault_path, {})
        assert '课程图片.png' in str(entries)
        target.write_bytes(PNG + b'replaced')
        replaced = client.get('/api/workspace/assets/content', params={'path': '附件/课程图片.png', 'note_path': '课程.md'})
        assert replaced.status_code == 200 and replaced.content == PNG + b'replaced'
        target.write_bytes(b'not a png')
        assert client.get('/api/workspace/assets/content', params={'path': '附件/课程图片.png'}).status_code == 415
        target.write_bytes(PNG + bytes(5 * 1024 * 1024))
        assert client.get('/api/workspace/assets/content', params={'path': '附件/课程图片.png'}).status_code == 413
        for path in ['../课程图片.png', '.ainote/secret.png', 'opennexus-records/secret.png', '/absolute.png', 'C:/secret.png']:
            assert client.get('/api/workspace/assets/content', params={'path': path}).status_code == 400


def test_managed_image_still_checks_content_hash() -> None:
    with TestClient(app) as client:
        asset = client.post('/api/workspace/assets', params={'filename': 'a.png', 'note_path': 'a.md', 'source': 'upload'}, content=PNG).json()
        (get_settings().vault_path / asset['path']).write_bytes(PNG + b'tampered')
        assert client.get('/api/workspace/assets/content', params={'path': asset['path']}).status_code == 409


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
