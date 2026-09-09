"""在临时目录启动三个 ASGI 应用，不读取仓库 data 或调用外部服务。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="notesagent-phase3-") as temporary:
        root = Path(temporary)
        os.environ.update({
            "APP_DATA_DIR": str(root / "backend"),
            "APP_DB_PATH": str(root / "backend" / "app.db"),
            "APP_VAULT_PATH": str(root / "vault"),
            "APP_ATTACHMENTS_PATH": str(root / "backend" / "attachments"),
        })
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi.testclient import TestClient
        from app.main import app as core

        with TestClient(core) as client:
            assert client.get("/health").json()["status"] == "ok"
            assert client.get("/api/status").status_code == 200

        def project_python(project: Path) -> Path:
            candidates = [project / ".venv" / "Scripts" / "python.exe", project / ".venv" / "bin" / "python"]
            return next(path for path in candidates if path.exists())

        sync_code = """from pathlib import Path
from fastapi.testclient import TestClient
from sync_server.app import create_app
from sync_server.database import Database
from sync_server.storage import DiskObjects
root=Path(r'%s')
with TestClient(create_app(Database('sqlite:///'+str(root/'sync.db')),DiskObjects(root/'objects'),root/'staging')) as c:
 assert c.get('/ready').json()=={'status':'ready','schema':1}
 assert c.get('/sync/v1/handshake').json()['protocol']==1
""" % root
        subprocess.run([project_python(ROOT / "server sync"), "-c", sync_code], cwd=ROOT / "server sync", check=True)

        community_code = """from pathlib import Path
from fastapi.testclient import TestClient
from community.app import Registry,create_app
root=Path(r'%s')
with TestClient(create_app(Registry(root/'community.db'))) as c:
 assert c.get('/health').json()['status']=='ok'
 assert c.get('/catalog/v1/sources').json()['schema_version']==1
""" % root
        subprocess.run([project_python(ROOT / "community-server"), "-c", community_code], cwd=ROOT / "community-server", check=True)
        print("AI Core、Sync v1、Community v1 隔离冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
