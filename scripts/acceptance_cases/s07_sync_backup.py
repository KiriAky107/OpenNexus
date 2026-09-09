"""S-07 empty deployment, backup/restore, and migration safety acceptance."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from sync_production_stack import (
    ROOT,
    SERVICE,
    SyncProductionStack,
    call,
    sha256,
    wait_http,
)

FILE_COUNT = 10_000
NOTE_COUNT = 9_990
NOTE_BYTES = 4 * 1024
ATTACHMENT_COUNT = 10
ATTACHMENT_BYTES = 100 * 1024 * 1024
ONE_GIB = 1024**3


def result(case_id: str, status: str, reason: str, facts: dict) -> dict:
    passed = status == "PASSED"
    assertions = [
        (
            "an empty PostgreSQL and MinIO instance initializes automatically",
            facts.get("automatic_initialization") is True,
        ),
        (
            "repeated initialization preserves every existing database row and object",
            facts.get("initialization_runs") == 2
            and facts.get("initialization_digest_preserved") is True,
        ),
        (
            "the backup contains at least one GiB and exactly 10000 historical files",
            facts.get("file_count") == FILE_COUNT
            and facts.get("object_count") == FILE_COUNT
            and facts.get("object_bytes", 0) >= ONE_GIB,
        ),
        (
            "the source instance is deleted and all restored object hashes verify",
            facts.get("source_deleted") is True
            and facts.get("verified_objects") == FILE_COUNT
            and facts.get("restored_digest_matches") is True,
        ),
        (
            "restore reaches a ready service within 30 minutes from a backup under 24 hours old",
            facts.get("rto_ms", 1_800_001) <= 1_800_000
            and facts.get("backup_age_seconds", 86_401) <= 86_400,
        ),
        (
            "a rejected migration leaves the complete old-data digest unchanged",
            facts.get("migration_failures") == 1
            and facts.get("migration_digest_preserved") is True,
        ),
    ]
    evidence = (
        "real PostgreSQL 17, fixed MinIO, production backup/restore commands, "
        "and full SHA-256 verification"
    )
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": [
            {
                "name": name,
                "status": "PASSED" if passed and actual else "FAILED",
                "evidence": evidence,
            }
            for name, actual in assertions
        ],
        "metrics": {
            "file_count": facts.get("file_count", 0),
            "object_count": facts.get("object_count", 0),
            "object_bytes": facts.get("object_bytes", 0),
            "verified_objects": facts.get("verified_objects", 0),
            "rto_ms": facts.get("rto_ms", 0),
            "backup_age_seconds": facts.get("backup_age_seconds", 0),
            "initialization_runs": facts.get("initialization_runs", 0),
            "migration_failures": facts.get("migration_failures", 0),
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "server sync/compose.yaml",
                "server sync/sync_server/__main__.py",
                "server sync/sync_server/database.py",
                "server sync/sync_server/operations.py",
                "server sync/sync_server/storage.py",
                "scripts/acceptance_cases/sync_production_stack.py",
                "scripts/acceptance_cases/s07_sync_backup.py",
            )
        ],
        "revisions": [
            {
                "scope": "runtime",
                "postgres": facts.get("postgres_version"),
                "minio": facts.get("minio_version"),
            },
            {
                "scope": "dataset",
                "files": facts.get("file_count", 0),
                "objects": facts.get("object_count", 0),
                "bytes": facts.get("object_bytes", 0),
            },
            {
                "scope": "recovery",
                "rto_ms": facts.get("rto_ms", 0),
                "backup_age_seconds": facts.get("backup_age_seconds", 0),
                "verified_objects": facts.get("verified_objects", 0),
            },
            {
                "scope": "last checkpoint",
                "stage": facts.get("active_stage"),
                "iteration": facts.get("active_iteration", -1),
            },
        ],
    }


def _note_data(index: int) -> bytes:
    line = f"# OpenNexus S-07 note {index:05d}\nseed=20260908\n".encode()
    return (line * (NOTE_BYTES // len(line) + 1))[:NOTE_BYTES]


def _stable_id(kind: str, index: int, length: int = 32) -> str:
    return hashlib.sha256(f"OpenNexus:S-07:{kind}:{index}".encode()).hexdigest()[:length]


def seed_worker() -> int:
    sys.path.insert(0, str(SERVICE))
    from sqlalchemy import text

    from sync_server.database import Database
    from sync_server.storage import S3Objects

    db = Database(os.environ["SYNC_DATABASE_URL"])
    objects = S3Objects(os.environ["SYNC_S3_ENDPOINT"], os.environ["SYNC_S3_BUCKET"])
    vault_id = os.environ["S07_VAULT_ID"]
    device_id = os.environ["S07_DEVICE_ID"]
    created = int(time.time())

    def put_note(index: int) -> dict:
        data = _note_data(index)
        digest = hashlib.sha256(data).hexdigest()
        objects.put(f"{vault_id}/{digest}", data)
        return {
            "hash": digest,
            "size": len(data),
            "path": f"notes/{index:05d}.md",
        }

    with ThreadPoolExecutor(max_workers=16) as pool:
        records = list(pool.map(put_note, range(NOTE_COUNT)))

    scratch = Path(os.environ["S07_SCRATCH_FILE"])
    try:
        for index in range(ATTACHMENT_COUNT):
            block = hashlib.sha256(f"OpenNexus:S-07:attachment:{index}".encode()).digest()
            block = block * (1024 * 1024 // len(block))
            digest = hashlib.sha256()
            with scratch.open("wb") as output:
                for _ in range(ATTACHMENT_BYTES // len(block)):
                    output.write(block)
                    digest.update(block)
            content_hash = digest.hexdigest()
            objects.put_file(f"{vault_id}/{content_hash}", scratch, content_hash)
            records.append(
                {
                    "hash": content_hash,
                    "size": ATTACHMENT_BYTES,
                    "path": f"attachments/{index:02d}.bin",
                }
            )
    finally:
        scratch.unlink(missing_ok=True)

    object_rows = [
        {"vault": vault_id, "hash": item["hash"], "size": item["size"], "created": created}
        for item in records
    ]
    revision_rows = []
    file_rows = []
    for sequence, item in enumerate(records, start=1):
        file_id = _stable_id("file", sequence)
        operation_id = _stable_id("operation", sequence)
        revision_rows.append(
            {
                "vault": vault_id,
                "sequence": sequence,
                "file": file_id,
                "base": 0,
                "path": item["path"],
                "path_key": item["path"].casefold(),
                "hash": item["hash"],
                "size": item["size"],
                "device": device_id,
                "operation": operation_id,
                "fingerprint": _stable_id("fingerprint", sequence, 64),
            }
        )
        file_rows.append(
            {
                "vault": vault_id,
                "file": file_id,
                "sequence": sequence,
                "path_key": item["path"].casefold(),
            }
        )
    total_bytes = sum(item["size"] for item in records)
    with db.transaction() as conn:
        existing = conn.execute(
            text("SELECT COUNT(*) FROM objects WHERE vault_id=:vault"), {"vault": vault_id}
        ).scalar_one()
        if existing:
            raise RuntimeError("S07_DATASET_NOT_EMPTY")
        for start in range(0, len(records), 1000):
            conn.execute(
                text(
                    "INSERT INTO objects(vault_id,hash,size,created) "
                    "VALUES (:vault,:hash,:size,:created)"
                ),
                object_rows[start : start + 1000],
            )
            conn.execute(
                text(
                    "INSERT INTO revisions(vault_id,sequence,file_id,base_revision,path,path_key,"
                    "operation,hash,size,device_id,operation_id,fingerprint) VALUES "
                    "(:vault,:sequence,:file,:base,:path,:path_key,'put',:hash,:size,:device,"
                    ":operation,:fingerprint)"
                ),
                revision_rows[start : start + 1000],
            )
            conn.execute(
                text(
                    "INSERT INTO files(vault_id,file_id,sequence,path_key,deleted) "
                    "VALUES (:vault,:file,:sequence,:path_key,0)"
                ),
                file_rows[start : start + 1000],
            )
        conn.execute(
            text("UPDATE vaults SET sequence=:sequence,used=:used,quota=:quota WHERE id=:vault"),
            {
                "sequence": FILE_COUNT,
                "used": total_bytes,
                "quota": total_bytes + ONE_GIB,
                "vault": vault_id,
            },
        )
    print(json.dumps({"files": len(records), "objects": len(records), "bytes": total_bytes}))
    return 0


def digest_worker(mode: str) -> int:
    sys.path.insert(0, str(SERVICE))
    from sqlalchemy import text

    from sync_server.database import Database
    from sync_server.storage import S3Objects

    db = Database(os.environ["SYNC_DATABASE_URL"])
    objects = S3Objects(os.environ["SYNC_S3_ENDPOINT"], os.environ["SYNC_S3_BUCKET"])
    database_digest = hashlib.sha256()
    with db.engine.connect() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
            )
        ]
        for table in tables:
            if not table.replace("_", "").isalnum():
                raise RuntimeError("S07_TABLE_NAME_INVALID")
            rows = [
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for row in conn.execute(text(f'SELECT * FROM "{table}"')).mappings()
            ]
            database_digest.update(table.encode())
            for encoded in sorted(rows):
                database_digest.update(encoded.encode())
        catalog = [
            {"vault_id": row.vault_id, "hash": row.hash, "size": int(row.size)}
            for row in conn.execute(
                text("SELECT vault_id,hash,size FROM objects ORDER BY vault_id,hash")
            )
        ]

    def verify(item: dict) -> str:
        key = f"{item['vault_id']}/{item['hash']}"
        if mode == "full":
            response = objects.client.get_object(Bucket=objects.bucket, Key=key)
            digest = hashlib.sha256()
            size = 0
            with response["Body"] as body:
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if digest.hexdigest() != item["hash"] or size != item["size"]:
                raise RuntimeError("S07_OBJECT_DIGEST_MISMATCH")
        else:
            response = objects.client.head_object(Bucket=objects.bucket, Key=key)
            if (
                int(response["ContentLength"]) != item["size"]
                or response.get("Metadata", {}).get("sha256") != item["hash"]
            ):
                raise RuntimeError("S07_OBJECT_HEAD_MISMATCH")
        return f"{key}:{item['size']}"

    with ThreadPoolExecutor(max_workers=16) as pool:
        verified = list(pool.map(verify, catalog))
    catalog_digest = hashlib.sha256("\n".join(verified).encode()).hexdigest()
    combined = hashlib.sha256(
        (database_digest.hexdigest() + ":" + catalog_digest).encode()
    ).hexdigest()
    print(
        json.dumps(
            {
                "digest": combined,
                "database_digest": database_digest.hexdigest(),
                "catalog_digest": catalog_digest,
                "verified_objects": len(verified),
                "object_bytes": sum(item["size"] for item in catalog),
            }
        )
    )
    return 0


def worker_entry() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=["seed", "digest-head", "digest-full"], required=True)
    args = parser.parse_args()
    if args.worker == "seed":
        return seed_worker()
    return digest_worker("full" if args.worker == "digest-full" else "head")


def run_worker(stack: SyncProductionStack, worker: str, **environment: str) -> dict:
    child_env = dict(stack.service_env)
    child_env.update(environment)
    completed = subprocess.run(
        [str(stack.server_python), str(Path(__file__).resolve()), "--worker", worker],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=1800,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def run_operation(stack: SyncProductionStack, command: str, directory: Path) -> dict:
    completed = subprocess.run(
        [
            str(stack.server_python),
            "-m",
            "sync_server",
            command,
            "--directory",
            str(directory),
            "--io-workers",
            "16",
        ],
        cwd=SERVICE,
        env=stack.service_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=2400,
    )
    return json.loads(completed.stdout.splitlines()[-1])


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
    source_stack = None
    restored_stack = None
    source_username = ""
    source_password = ""
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        data_root = Path(os.environ["OPENNEXUS_ACCEPTANCE_DATA_ROOT"]).resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        backup = data_root / "s07-backup"

        facts["active_stage"] = "initialize-empty"
        source_stack = SyncProductionStack(config, data_root, "s07-source").start_dependencies()
        source_stack.initialize()
        source_username = source_stack.username
        source_password = source_stack.password
        facts.update(
            {
                "automatic_initialization": source_stack.sql_scalar(
                    "SELECT version FROM schema_version"
                )
                == 1,
                "postgres_version": source_stack.postgres_version,
                "minio_version": source_stack.minio_version,
                "initialization_runs": 1,
            }
        )
        source_stack.add_user_with_password(source_stack.username, source_stack.password)
        source_stack.start_sync()
        wait_http(source_stack.origin + "/ready", timeout=60)
        owner = source_stack.login(source_stack.username, source_stack.password, "S-07 owner")
        owner_auth = {"Authorization": "Bearer " + owner["access_token"]}
        _, _, vault_id = source_stack.create_vault_for_auth(owner_auth, "S-07 dataset")
        source_stack.stop_sync()

        facts["active_stage"] = "seed-dataset"
        seeded = run_worker(
            source_stack,
            "seed",
            S07_VAULT_ID=vault_id,
            S07_DEVICE_ID=owner["device_id"],
            S07_SCRATCH_FILE=str(source_stack.stack / "s07-seed-object.bin"),
        )
        facts.update(
            {
                "file_count": seeded["files"],
                "object_count": seeded["objects"],
                "object_bytes": seeded["bytes"],
            }
        )
        assert seeded["files"] == FILE_COUNT and seeded["objects"] == FILE_COUNT
        assert seeded["bytes"] >= ONE_GIB

        facts["active_stage"] = "repeat-initialize"
        before_initialize = run_worker(source_stack, "digest-head")
        source_stack.initialize()
        facts["initialization_runs"] = 2
        after_initialize = run_worker(source_stack, "digest-head")
        facts["initialization_digest_preserved"] = (
            before_initialize["digest"] == after_initialize["digest"]
        )
        assert facts["initialization_digest_preserved"]

        facts["active_stage"] = "backup"
        backup_info = run_operation(source_stack, "backup", backup)
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        assert backup_info["status"] == "BACKUP_COMPLETE"
        assert manifest["object_count"] == FILE_COUNT and manifest["object_bytes"] >= ONE_GIB

        facts["active_stage"] = "delete-source"
        source_root = source_stack.stack.resolve()
        source_stack.stop()
        source_stack = None
        if source_root.parent != data_root or source_root.name != "s07-source-production-stack":
            raise RuntimeError("S07_SOURCE_DELETE_BOUNDARY")
        shutil.rmtree(source_root)
        facts["source_deleted"] = not source_root.exists()
        assert facts["source_deleted"]

        facts["active_stage"] = "restore-empty"
        rto_started = time.monotonic()
        restored_stack = SyncProductionStack(
            config, data_root, "s07-restored"
        ).start_dependencies()
        restore_info = run_operation(restored_stack, "restore", backup)
        restored_stack.start_sync()
        wait_http(restored_stack.origin + "/ready", timeout=60)
        facts["rto_ms"] = int((time.monotonic() - rto_started) * 1000)
        facts["backup_age_seconds"] = restore_info["backup_age_seconds"]
        facts["verified_objects"] = restore_info["verified_objects"]
        assert facts["rto_ms"] <= 1_800_000 and facts["backup_age_seconds"] <= 86_400
        assert facts["verified_objects"] == FILE_COUNT
        restored_digest = run_worker(restored_stack, "digest-head")
        facts["restored_digest_matches"] = (
            restored_digest["digest"] == before_initialize["digest"]
        )
        assert facts["restored_digest_matches"]

        restored_owner = restored_stack.login(
            source_username, source_password, "S-07 restored owner"
        )
        restored_auth = {"Authorization": "Bearer " + restored_owner["access_token"]}
        restored_base = restored_stack.origin + "/sync/v1/vaults/" + vault_id
        sample = next(item for item in manifest["objects"] if item["size"] == NOTE_BYTES)
        downloaded = call(
            "GET", restored_base + "/objects/" + sample["hash"], headers=restored_auth
        )
        assert downloaded[0] == 200 and hashlib.sha256(downloaded[1]).hexdigest() == sample["hash"]

        facts["active_stage"] = "migration-failure"
        restored_stack.stop_sync()
        changed = restored_stack.sql_scalar(
            "WITH changed AS (UPDATE schema_version SET version=999 RETURNING 1) "
            "SELECT COUNT(*) FROM changed"
        )
        assert changed == 1
        before_migration = run_worker(restored_stack, "digest-full")
        migration = subprocess.run(
            [str(restored_stack.server_python), "-m", "sync_server", "migrate"],
            cwd=SERVICE,
            env=restored_stack.service_env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        facts["migration_failures"] = int(migration.returncode != 0)
        after_migration = run_worker(restored_stack, "digest-head")
        facts["migration_digest_preserved"] = (
            before_migration["digest"] == after_migration["digest"]
        )
        assert facts["migration_failures"] == 1 and facts["migration_digest_preserved"]
        restored_stack.sql_scalar(
            "WITH changed AS (UPDATE schema_version SET version=1 RETURNING 1) "
            "SELECT COUNT(*) FROM changed"
        )
        restored_stack.start_sync()
        wait_http(restored_stack.origin + "/ready", timeout=60)
        facts.update({"active_stage": "complete", "active_iteration": FILE_COUNT})
        status = "PASSED"
    except BaseException as error:
        reason = "S07_ORACLE_FAILED:" + type(error).__name__
    finally:
        if source_stack is not None:
            source_stack.stop()
        if restored_stack is not None:
            restored_stack.stop()
    payload = result(
        case_id,
        status if case_id == "S-07" else "FAILED",
        reason or ("" if case_id == "S-07" else "CASE_ID_MISMATCH"),
        facts,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(worker_entry())
    raise SystemExit(main())
