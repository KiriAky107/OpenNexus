"""Isolated PostgreSQL, MinIO, and multi-worker Sync acceptance stack."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "server sync"
MINIO_RELEASE = "RELEASE.2025-09-07T16-13-09Z"
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


def call(method: str, url: str, *, headers=None, body=None, timeout=30):
    request_headers = dict(headers or {})
    payload = body
    if isinstance(body, (dict, list)):
        payload = json.dumps(body, separators=(",", ":")).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        response = urlopen(request, timeout=timeout)
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


class SyncProductionStack:
    """Own a disposable production dependency stack for one acceptance case."""

    def __init__(self, config: dict, data_root: Path, case_tag: str):
        self.config = config
        self.data_root = data_root
        self.case_tag = case_tag.lower()
        self.postgres_started = False
        self.minio = None
        self.sync = None
        self.handles = []

        initdb = Path(config["artifacts"]["postgres_initdb"]).resolve()
        self.pg_bin = initdb.parent
        executable = lambda name: self.pg_bin / (name + ".exe" if os.name == "nt" else name)
        self.initdb = initdb
        self.pg_ctl = executable("pg_ctl")
        self.createdb = executable("createdb")
        self.postgres = executable("postgres")
        self.psql = executable("psql")
        self.minio_server = Path(config["artifacts"]["minio_server"]).resolve()
        self.server_python = SERVICE / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        required = (
            self.initdb,
            self.pg_ctl,
            self.createdb,
            self.postgres,
            self.psql,
            self.minio_server,
            self.server_python,
        )
        if not all(path.is_file() for path in required):
            raise RuntimeError("PRODUCTION_RUNTIME_MISSING")

        self.postgres_version = subprocess.check_output(
            [str(self.postgres), "--version"], text=True, timeout=10
        ).strip()
        self.minio_version = subprocess.check_output(
            [str(self.minio_server), "--version"], text=True, timeout=10
        ).splitlines()[0]
        if " 17." not in self.postgres_version or MINIO_RELEASE not in self.minio_version:
            raise RuntimeError("PRODUCTION_RUNTIME_VERSION_MISMATCH")
        if sha256(self.minio_server) != MINIO_SHA256:
            raise RuntimeError("MINIO_ARTIFACT_INTEGRITY")

        self.stack = data_root / f"{self.case_tag}-production-stack"
        self.postgres_data = self.stack / "postgres"
        self.minio_data = self.stack / "objects"
        self.staging = self.stack / "staging"
        self.pg_port, self.minio_port, self.console_port, self.sync_port = (
            free_port() for _ in range(4)
        )
        self.database_url = f"postgresql+psycopg://postgres@127.0.0.1:{self.pg_port}/opennexus"
        self.origin = f"http://127.0.0.1:{self.sync_port}"
        self.username = self.case_tag + "-" + secrets.token_hex(8)
        self.password = secrets.token_urlsafe(32)
        self.minio_user = self.case_tag + secrets.token_hex(8)
        self.minio_password = secrets.token_urlsafe(32)
        self.bucket = "opennexus-" + self.case_tag
        self.service_env = {}

    def start(self) -> "SyncProductionStack":
        self.stack.mkdir()
        self.minio_data.mkdir()
        self.staging.mkdir()
        pg_log = (self.stack / "postgres.log").open("wb")
        minio_log = (self.stack / "minio.log").open("wb")
        sync_log = (self.stack / "sync.log").open("wb")
        self.handles.extend([pg_log, minio_log, sync_log])

        subprocess.run(
            [
                str(self.initdb),
                "-D",
                str(self.postgres_data),
                "-U",
                "postgres",
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=120,
        )
        subprocess.run(
            [
                str(self.pg_ctl),
                "-D",
                str(self.postgres_data),
                "-l",
                str(self.stack / "postgres-server.log"),
                "-o",
                f"-p {self.pg_port} -h 127.0.0.1",
                "-w",
                "start",
            ],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
        self.postgres_started = True
        subprocess.run(
            [
                str(self.createdb),
                "-h",
                "127.0.0.1",
                "-p",
                str(self.pg_port),
                "-U",
                "postgres",
                "opennexus",
            ],
            stdout=pg_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )

        minio_env = os.environ.copy()
        minio_env.update(
            {"MINIO_ROOT_USER": self.minio_user, "MINIO_ROOT_PASSWORD": self.minio_password}
        )
        self.minio = subprocess.Popen(
            [
                str(self.minio_server),
                "server",
                str(self.minio_data),
                "--address",
                f"127.0.0.1:{self.minio_port}",
                "--console-address",
                f"127.0.0.1:{self.console_port}",
            ],
            stdout=minio_log,
            stderr=subprocess.STDOUT,
            env=minio_env,
        )
        wait_http(f"http://127.0.0.1:{self.minio_port}/minio/health/ready")

        self.service_env = os.environ.copy()
        self.service_env.update(
            {
                "SYNC_DATABASE_URL": self.database_url,
                "SYNC_S3_ENDPOINT": f"http://127.0.0.1:{self.minio_port}",
                "SYNC_S3_BUCKET": self.bucket,
                "SYNC_STAGING_DIR": str(self.staging),
                "SYNC_HOST": "127.0.0.1",
                "SYNC_PORT": str(self.sync_port),
                "AWS_ACCESS_KEY_ID": self.minio_user,
                "AWS_SECRET_ACCESS_KEY": self.minio_password,
                "AWS_DEFAULT_REGION": "us-east-1",
                "ACCEPTANCE_USERNAME": self.username,
                "ACCEPTANCE_PASSWORD": self.password,
            }
        )
        subprocess.run(
            [
                str(self.server_python),
                "-c",
                "import os,boto3; from sync_server.database import Database; "
                "d=Database(os.environ['SYNC_DATABASE_URL']); d.migrate(); "
                "d.add_user(os.environ['ACCEPTANCE_USERNAME'],os.environ['ACCEPTANCE_PASSWORD']); "
                "boto3.client('s3',endpoint_url=os.environ['SYNC_S3_ENDPOINT']).create_bucket(Bucket=os.environ['SYNC_S3_BUCKET'])",
            ],
            cwd=SERVICE,
            env=self.service_env,
            stdout=sync_log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
        self.sync = subprocess.Popen(
            [str(self.server_python), "-m", "sync_server", "serve", "--workers", "2"],
            cwd=SERVICE,
            env=self.service_env,
            stdout=sync_log,
            stderr=subprocess.STDOUT,
        )
        wait_http(self.origin + "/ready", timeout=60)
        return self

    def create_vault(self, label: str):
        session = None
        for _ in range(5):
            try:
                code, candidate, _ = call(
                    "POST",
                    self.origin + "/sync/v1/auth/sessions",
                    body={
                        "username": self.username,
                        "password": self.password,
                        "device_name": label,
                    },
                    timeout=10,
                )
                if code == 200:
                    session = candidate
                    break
            except (OSError, TimeoutError, URLError):
                pass
            time.sleep(0.2)
        if session is None:
            raise RuntimeError("ACCEPTANCE_LOGIN_FAILED")
        auth = {"Authorization": "Bearer " + session["access_token"]}
        vault = None
        for _ in range(5):
            try:
                code, candidate, _ = call(
                    "POST",
                    self.origin + "/sync/v1/vaults",
                    headers=auth,
                    body={"name": label},
                    timeout=10,
                )
                if code == 200:
                    vault = candidate
                    break
            except (OSError, TimeoutError, URLError):
                pass
            time.sleep(0.2)
        if vault is None:
            raise RuntimeError("ACCEPTANCE_VAULT_FAILED")
        return auth, self.origin + "/sync/v1/vaults/" + vault["vault_id"], vault["vault_id"]

    def sql_scalar(self, statement: str) -> int:
        output = subprocess.check_output(
            [
                str(self.psql),
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.pg_port),
                "-U",
                "postgres",
                "-d",
                "opennexus",
                "-c",
                statement,
            ],
            text=True,
            timeout=30,
        ).strip()
        return int(output)

    def worker_ids(self, attempts=200) -> set[str]:
        from concurrent.futures import ThreadPoolExecutor

        def probe(_):
            try:
                return call(
                    "GET",
                    self.origin + "/health",
                    headers={"Connection": "close"},
                    timeout=10,
                )[2].get("x-opennexus-worker")
            except (OSError, TimeoutError, URLError):
                return None

        # A small pool is enough to reach both workers without exhausting the
        # Windows ephemeral-port/backlog budget before the fault matrix starts.
        with ThreadPoolExecutor(max_workers=8) as pool:
            return {value for value in pool.map(probe, range(attempts)) if value}

    def assert_running(self) -> None:
        if self.sync is None or self.minio is None:
            raise RuntimeError("PRODUCTION_STACK_NOT_STARTED")
        if self.sync.poll() is not None or self.minio.poll() is not None:
            raise RuntimeError("PRODUCTION_STACK_EXITED")

    def stop(self) -> None:
        stop_tree(self.sync)
        stop_tree(self.minio)
        if self.postgres_started:
            subprocess.run(
                [
                    str(self.pg_ctl),
                    "-D",
                    str(self.postgres_data),
                    "-m",
                    "fast",
                    "-w",
                    "stop",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        for handle in self.handles:
            handle.close()
