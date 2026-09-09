"""S-04 real PostgreSQL, MinIO, and two-worker transaction acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "server sync"
MINIO_SHA256 = "af709e6ba68488404e85acdd22a3030d0f5e56a108d4b27d744f18ceb50861b4"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def call(method: str, url: str, *, headers=None, body=None):
    request_headers = dict(headers or {})
    payload = body
    if isinstance(body, (dict, list)):
        payload = json.dumps(body, separators=(",", ":")).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    data = response.read()
    media_type = response.headers.get("Content-Type", "")
    value = json.loads(data) if data and "json" in media_type else data
    return response.status, value, {key.lower(): item for key, item in response.headers.items()}


def wait_http(url: str, *, expected=200, timeout=30) -> None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if call("GET", url)[0] == expected:
                return
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("SERVICE_START_TIMEOUT")


def stop_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def result(case_id: str, status: str, reason: str, facts: dict) -> dict:
    passed = status == "PASSED"
    assertions = [
        ("the production entry uses real PostgreSQL and MinIO", facts.get("dependencies") is True),
        ("both configured Uvicorn workers serve requests", facts.get("worker_count") == 2),
        ("one of 100 same-base commits succeeds and the other 99 conflict", facts.get("race") == {"ok": 1, "conflict": 99}),
        ("100 same-payload idempotent retries return the original revision", facts.get("idempotent_retries") == 100),
        ("reusing the idempotency key with a different payload is rejected", facts.get("idempotency_reused") is True),
        ("a frozen pagination boundary has no gaps or duplicates while a later commit is added", facts.get("pagination") is True),
        ("the object is readable before its first revision becomes visible", facts.get("object_before_revision") is True),
    ]
    evidence = "real PostgreSQL 17, MinIO S3, two Uvicorn workers, and black-box HTTP"
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": [
            {"name": name, "status": "PASSED" if passed and actual else "FAILED", "evidence": evidence}
            for name, actual in assertions
        ],
        "metrics": {
            "successful_commits": facts.get("race", {}).get("ok", 0),
            "conflict_responses": facts.get("race", {}).get("conflict", 0),
            "worker_count": facts.get("worker_count", 0),
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "server sync/sync_server/app.py",
                "server sync/sync_server/database.py",
                "server sync/sync_server/storage.py",
                "server sync/sync_server/__main__.py",
                "server sync/compose.yaml",
                "server sync/uv.lock",
            )
        ],
        "revisions": [
            {"scope": "runtime", "postgres": facts.get("postgres_version"), "minio": facts.get("minio_version")},
            {"scope": "same-base race", "attempts": 100, **facts.get("race", {})},
            {"scope": "fixed-boundary pagination", "boundary": facts.get("boundary", 0), "late_sequence": facts.get("late_sequence", 0)},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    facts: dict = {}
    reason = ""
    status = "FAILED"
    postgres_started = False
    minio = sync = None
    handles = []
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        initdb = Path(config["artifacts"]["postgres_initdb"]).resolve()
        pg_bin = initdb.parent
        pg_ctl = pg_bin / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
        createdb = pg_bin / ("createdb.exe" if os.name == "nt" else "createdb")
        postgres = pg_bin / ("postgres.exe" if os.name == "nt" else "postgres")
        minio_server = Path(config["artifacts"]["minio_server"]).resolve()
        server_python = SERVICE / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        if not all(path.is_file() for path in (initdb, pg_ctl, createdb, postgres, minio_server, server_python)):
            raise RuntimeError("PRODUCTION_RUNTIME_MISSING")
        postgres_version = subprocess.check_output(
            [str(postgres), "--version"], text=True, timeout=10
        ).strip()
        minio_version = subprocess.check_output(
            [str(minio_server), "--version"], text=True, timeout=10
        ).splitlines()[0]
        if " 17." not in postgres_version or "RELEASE.2025-09-07T16-13-09Z" not in minio_version:
            raise RuntimeError("PRODUCTION_RUNTIME_VERSION_MISMATCH")
        if sha256(minio_server) != MINIO_SHA256:
            raise RuntimeError("MINIO_ARTIFACT_INTEGRITY")
        facts.update({"postgres_version": postgres_version, "minio_version": minio_version})

        data_root = Path(os.environ["OPENNEXUS_ACCEPTANCE_DATA_ROOT"])
        stack = data_root / "s04-production-stack"
        stack.mkdir()
        postgres_data = stack / "postgres"
        minio_data = stack / "objects"
        staging = stack / "staging"
        minio_data.mkdir()
        staging.mkdir()
        pg_port, minio_port, console_port, sync_port = (free_port() for _ in range(4))

        pg_log = (stack / "postgres.log").open("wb")
        minio_log = (stack / "minio.log").open("wb")
        sync_log = (stack / "sync.log").open("wb")
        handles.extend([pg_log, minio_log, sync_log])
        subprocess.run(
            [str(initdb), "-D", str(postgres_data), "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
        subprocess.run(
            [str(pg_ctl), "-D", str(postgres_data), "-l", str(stack / "postgres-server.log"), "-o", f"-p {pg_port} -h 127.0.0.1", "-w", "start"],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
        postgres_started = True
        subprocess.run(
            [str(createdb), "-h", "127.0.0.1", "-p", str(pg_port), "-U", "postgres", "opennexus"],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )

        username = "s04-" + secrets.token_hex(8)
        password = secrets.token_urlsafe(32)
        minio_user = "s04" + secrets.token_hex(8)
        minio_password = secrets.token_urlsafe(32)
        database_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/opennexus"
        minio_env = os.environ.copy()
        minio_env.update({"MINIO_ROOT_USER": minio_user, "MINIO_ROOT_PASSWORD": minio_password})
        minio = subprocess.Popen(
            [str(minio_server), "server", str(minio_data), "--address", f"127.0.0.1:{minio_port}", "--console-address", f"127.0.0.1:{console_port}"],
            stdout=minio_log,
            stderr=subprocess.STDOUT,
            env=minio_env,
        )
        wait_http(f"http://127.0.0.1:{minio_port}/minio/health/ready")

        service_env = os.environ.copy()
        service_env.update(
            {
                "SYNC_DATABASE_URL": database_url,
                "SYNC_S3_ENDPOINT": f"http://127.0.0.1:{minio_port}",
                "SYNC_S3_BUCKET": "opennexus-s04",
                "SYNC_STAGING_DIR": str(staging),
                "SYNC_HOST": "127.0.0.1",
                "SYNC_PORT": str(sync_port),
                "AWS_ACCESS_KEY_ID": minio_user,
                "AWS_SECRET_ACCESS_KEY": minio_password,
                "AWS_DEFAULT_REGION": "us-east-1",
                "S04_USERNAME": username,
                "S04_PASSWORD": password,
            }
        )
        subprocess.run(
            [
                str(server_python),
                "-c",
                "import os,boto3; from sync_server.database import Database; d=Database(os.environ['SYNC_DATABASE_URL']); d.migrate(); d.add_user(os.environ['S04_USERNAME'],os.environ['S04_PASSWORD']); boto3.client('s3',endpoint_url=os.environ['SYNC_S3_ENDPOINT']).create_bucket(Bucket=os.environ['SYNC_S3_BUCKET'])",
            ],
            cwd=SERVICE,
            env=service_env,
            stdout=sync_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
        sync = subprocess.Popen(
            [str(server_python), "-m", "sync_server", "serve", "--workers", "2"],
            cwd=SERVICE,
            env=service_env,
            stdout=sync_log,
            stderr=subprocess.STDOUT,
        )
        origin = f"http://127.0.0.1:{sync_port}"
        wait_http(origin + "/ready", timeout=60)
        facts["dependencies"] = True

        code, session, _ = call(
            "POST",
            origin + "/sync/v1/auth/sessions",
            body={"username": username, "password": password, "device_name": "S-04 acceptance"},
        )
        assert code == 200
        auth = {"Authorization": "Bearer " + session["access_token"]}
        code, vault, _ = call("POST", origin + "/sync/v1/vaults", headers=auth, body={"name": "S-04 isolated"})
        assert code == 200
        base = origin + "/sync/v1/vaults/" + vault["vault_id"]

        content = b"S-04 real object"
        content_hash = hashlib.sha256(content).hexdigest()
        missing = {
            "operation_id": uuid.uuid4().hex,
            "file_id": uuid.uuid4().hex,
            "base_revision": 0,
            "path": "missing.md",
            "operation": "put",
            "content_hash": content_hash,
            "size": len(content),
        }
        assert call("POST", base + "/revisions", headers=auth, body=missing)[1]["error"]["code"] == "OBJECT_NOT_READY"
        assert call("GET", base + "/changes", headers=auth)[1]["boundary"] == 0
        upload = call("POST", base + "/uploads", headers=auth, body={"content_hash": content_hash, "size": len(content)})[1]
        upload_path = base + "/uploads/" + upload["upload_id"]
        assert call("PUT", upload_path + "?offset=0", headers=auth, body=content)[0] == 200
        assert call("POST", upload_path + "/complete", headers=auth)[0] == 200
        assert call("GET", base + "/objects/" + content_hash, headers=auth)[1] == content
        facts["object_before_revision"] = call("GET", base + "/changes", headers=auth)[1]["boundary"] == 0

        file_id = uuid.uuid4().hex
        race_bodies = [
            {
                "operation_id": uuid.uuid4().hex,
                "file_id": file_id,
                "base_revision": 0,
                "path": f"race/{index:03}.md",
                "operation": "put",
                "content_hash": content_hash,
                "size": len(content),
            }
            for index in range(100)
        ]
        with ThreadPoolExecutor(max_workers=100) as pool:
            race_results = list(pool.map(lambda body: call("POST", base + "/revisions", headers=auth, body=body), race_bodies))
        codes = [item[0] for item in race_results]
        facts["race"] = {"ok": codes.count(200), "conflict": codes.count(409)}
        assert facts["race"] == {"ok": 1, "conflict": 99}
        winner_index = codes.index(200)
        winner_body = race_bodies[winner_index]
        winner = race_results[winner_index][1]
        with ThreadPoolExecutor(max_workers=50) as pool:
            retries = list(pool.map(lambda _: call("POST", base + "/revisions", headers=auth, body=winner_body), range(100)))
        facts["idempotent_retries"] = sum(item[0] == 200 and item[1] == winner for item in retries)
        changed = {**winner_body, "path": "different.md"}
        reused = call("POST", base + "/revisions", headers=auth, body=changed)
        facts["idempotency_reused"] = reused[0] == 409 and reused[1]["error"]["code"] == "IDEMPOTENCY_REUSED"

        for index in range(99):
            body = {
                "operation_id": uuid.uuid4().hex,
                "file_id": uuid.uuid4().hex,
                "base_revision": 0,
                "path": f"bulk/{index:03}.md",
                "operation": "put",
                "content_hash": content_hash,
                "size": len(content),
            }
            assert call("POST", base + "/revisions", headers=auth, body=body)[0] == 200
        first = call("GET", base + "/changes?cursor=0&limit=7", headers=auth)[1]
        boundary = first["boundary"]
        late = {
            "operation_id": uuid.uuid4().hex,
            "file_id": uuid.uuid4().hex,
            "base_revision": 0,
            "path": "late.md",
            "operation": "put",
            "content_hash": content_hash,
            "size": len(content),
        }
        late_sequence = call("POST", base + "/revisions", headers=auth, body=late)[1]["sequence"]
        items = list(first["items"])
        cursor = items[-1]["sequence"]
        while cursor < boundary:
            page = call("GET", f"{base}/changes?cursor={cursor}&limit=7&boundary={boundary}", headers=auth)[1]
            items.extend(page["items"])
            cursor = items[-1]["sequence"]
        sequences = [item["sequence"] for item in items]
        facts["boundary"] = boundary
        facts["late_sequence"] = late_sequence
        facts["pagination"] = sequences == list(range(1, boundary + 1)) and late_sequence == boundary + 1

        def health_worker(_):
            return call("GET", origin + "/health", headers={"Connection": "close"})[2].get("x-opennexus-worker")

        with ThreadPoolExecutor(max_workers=40) as pool:
            worker_ids = {value for value in pool.map(health_worker, range(200)) if value}
        facts["worker_count"] = len(worker_ids)
        assert facts["worker_count"] == 2
        assert sync.poll() is None and minio.poll() is None
        status = "PASSED"
    except BaseException as error:
        reason = "S04_ORACLE_FAILED:" + type(error).__name__
    finally:
        stop_tree(sync)
        stop_tree(minio)
        if postgres_started:
            subprocess.run(
                [str(pg_ctl), "-D", str(postgres_data), "-m", "fast", "-w", "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        for handle in handles:
            handle.close()
    payload = result(case_id, status if case_id == "S-04" else "FAILED", reason or ("" if case_id == "S-04" else "CASE_ID_MISMATCH"), facts)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
