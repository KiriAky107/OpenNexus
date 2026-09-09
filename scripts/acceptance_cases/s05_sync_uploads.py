"""S-05 upload durability and cleanup races on the production stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from sync_production_stack import ROOT, SERVICE, SyncProductionStack, call, sha256, stop_tree

ROUNDS = 100


def result(case_id: str, status: str, reason: str, facts: dict) -> dict:
    passed = status == "PASSED"
    assertions = [
        (
            "the oracle uses real PostgreSQL, MinIO, and both production workers",
            facts.get("dependencies") is True and facts.get("worker_count") == 2,
        ),
        (
            "100 same-offset races never acknowledge more than durable staging bytes",
            facts.get("offset_races") == ROUNDS and facts.get("durable_offsets") == ROUNDS,
        ),
        (
            "100 disk-ahead and 100 disk-behind uploads reconcile or restart safely",
            facts.get("disk_ahead") == ROUNDS and facts.get("disk_behind") == ROUNDS,
        ),
        (
            "100 lost complete responses recover from durable receipts",
            facts.get("response_loss_recoveries") == ROUNDS,
        ),
        (
            "complete retries charge every unique object exactly once",
            facts.get("quota_exact") is True and facts.get("receipt_count_exact") is True,
        ),
        (
            "100 expiry cleanup races release every reservation within 15 minutes",
            facts.get("cleanup_races") == ROUNDS
            and facts.get("cleanup_split") == {"complete": 50, "cleanup": 50}
            and facts.get("remaining_uploads") == 0
            and facts.get("reserved_bytes") == 0
            and facts.get("max_cleanup_latency_ms", 900001) < 900000,
        ),
        (
            "all referenced objects remain readable and cleaned uploads are not referenced",
            facts.get("referenced_objects_verified") == facts.get("object_count")
            and facts.get("cleaned_objects_absent") == 50
            and facts.get("staging_files") == 0,
        ),
    ]
    evidence = "real PostgreSQL 17, MinIO S3, two Uvicorn workers, shared staging, and black-box HTTP"
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
            "offset_races": facts.get("offset_races", 0),
            "response_loss_recoveries": facts.get("response_loss_recoveries", 0),
            "cleanup_races": facts.get("cleanup_races", 0),
            "max_cleanup_latency_ms": facts.get("max_cleanup_latency_ms", 0),
            "worker_count": facts.get("worker_count", 0),
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "server sync/sync_server/app.py",
                "server sync/sync_server/database.py",
                "server sync/sync_server/maintenance.py",
                "server sync/sync_server/storage.py",
                "server sync/tests/test_production_storage.py",
                "scripts/acceptance_cases/sync_production_stack.py",
                "scripts/acceptance_cases/s05_sync_uploads.py",
            )
        ],
        "revisions": [
            {
                "scope": "runtime",
                "postgres": facts.get("postgres_version"),
                "minio": facts.get("minio_version"),
            },
            {
                "scope": "upload fault matrix",
                "same_offset": facts.get("offset_races", 0),
                "disk_ahead": facts.get("disk_ahead", 0),
                "disk_behind": facts.get("disk_behind", 0),
                "response_loss": facts.get("response_loss_recoveries", 0),
                "cleanup_races": facts.get("cleanup_races", 0),
            },
            {
                "scope": "cleanup outcomes",
                **facts.get("cleanup_split", {}),
                "max_latency_ms": facts.get("max_cleanup_latency_ms", 0),
            },
            {
                "scope": "final integrity",
                "objects": facts.get("object_count", 0),
                "used_bytes": facts.get("used_bytes", 0),
                "reserved_bytes": facts.get("reserved_bytes", -1),
            },
            {
                "scope": "last checkpoint",
                "stage": facts.get("active_stage"),
                "iteration": facts.get("active_iteration", -1),
            },
        ],
    }


def content(vector: str, index: int) -> bytes:
    return f"OpenNexus-S05-{vector}-{index:03d}".encode()


def begin(base: str, auth: dict, data: bytes):
    digest = hashlib.sha256(data).hexdigest()
    code, info, _ = call(
        "POST", base + "/uploads", headers=auth, body={"content_hash": digest, "size": len(data)}
    )
    assert code == 200 and info == {
        "complete": False,
        "upload_id": info["upload_id"],
        "offset": 0,
    }
    return digest, info["upload_id"], base + "/uploads/" + info["upload_id"]


def complete(path: str, auth: dict, digest: str) -> None:
    code, body, _ = call("POST", path + "/complete", headers=auth)
    assert code == 200 and body == {"complete": True, "content_hash": digest}


def drop_complete_response(url: str, authorization: str) -> None:
    parsed = urlsplit(url)
    target = parsed.path + (("?" + parsed.query) if parsed.query else "")
    request = (
        f"POST {target} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        f"Authorization: {authorization}\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as stream:
        stream.sendall(request)
        stream.shutdown(socket.SHUT_WR)
        # The request is complete, but the client deliberately never reads its response.
        time.sleep(0.01)


class CleanupClient:
    def __init__(self, stack: SyncProductionStack):
        program = (
            "import json,os,sys,time; from pathlib import Path; "
            "from sync_server.database import Database; "
            "from sync_server.maintenance import cleanup_expired_uploads; "
            "d=Database(os.environ['SYNC_DATABASE_URL']); s=Path(os.environ['SYNC_STAGING_DIR']); "
            "\nfor line in sys.stdin:\n"
            " r=json.loads(line); time.sleep(r['delay_ms']/1000); "
            " print(json.dumps(cleanup_expired_uploads(d,s,now=r['now'],limit=500)),flush=True)"
        )
        self.process = subprocess.Popen(
            [str(stack.server_python), "-u", "-c", program],
            cwd=SERVICE,
            env=stack.service_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stack.handles[2],
            text=True,
            bufsize=1,
        )
        self.lock = threading.Lock()

    def cleanup(self, *, now: int, delay_ms: int) -> dict:
        with self.lock:
            if self.process.poll() is not None or self.process.stdin is None or self.process.stdout is None:
                raise RuntimeError("CLEANUP_HELPER_EXITED")
            self.process.stdin.write(json.dumps({"now": now, "delay_ms": delay_ms}) + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("CLEANUP_HELPER_NO_RESPONSE")
            return json.loads(line)

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stop_tree(self.process)
        if self.process.stdout is not None:
            self.process.stdout.close()


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
    stack = cleanup_client = None
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        stack = SyncProductionStack(
            config, Path(os.environ["OPENNEXUS_ACCEPTANCE_DATA_ROOT"]), "s05"
        ).start()
        facts.update(
            {
                "dependencies": True,
                "postgres_version": stack.postgres_version,
                "minio_version": stack.minio_version,
            }
        )
        auth, base, vault_id = stack.create_vault("S-05 isolated")
        facts["worker_count"] = len(stack.worker_ids())
        assert facts["worker_count"] == 2
        committed: dict[str, bytes] = {}
        cleaned: dict[str, bytes] = {}
        expected_used = 0

        offset_races = durable_offsets = 0
        for index in range(ROUNDS):
            facts.update({"active_stage": "same-offset", "active_iteration": index})
            data = content("offset", index)
            digest, upload_id, path = begin(base, auth, data)
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = list(
                    pool.map(
                        lambda _: call("PUT", path + "?offset=0", headers=auth, body=data),
                        range(2),
                    )
                )
            codes = sorted(item[0] for item in attempts)
            rejected = next(item for item in attempts if item[0] == 409)
            assert codes == [200, 409]
            assert rejected[1]["error"] == {
                "code": "UPLOAD_OFFSET",
                "details": {"offset": len(data)},
            }
            state = call("GET", path, headers=auth)
            assert state[0] == 200 and state[1]["offset"] == len(data)
            assert (stack.staging / upload_id).stat().st_size == state[1]["offset"]
            complete(path, auth, digest)
            committed[digest] = data
            expected_used += len(data)
            offset_races += 1
            durable_offsets += 1
        facts.update({"offset_races": offset_races, "durable_offsets": durable_offsets})

        disk_ahead = 0
        for index in range(ROUNDS):
            facts.update({"active_stage": "disk-ahead", "active_iteration": index})
            data = content("ahead", index)
            digest, upload_id, path = begin(base, auth, data)
            local = stack.staging / upload_id
            local.write_bytes(data + b"-uncommitted-tail")
            state = call("GET", path, headers=auth)
            assert state[0] == 200 and state[1]["offset"] == 0 and local.stat().st_size == 0
            assert call("PUT", path + "?offset=0", headers=auth, body=data)[1] == {
                "offset": len(data)
            }
            complete(path, auth, digest)
            committed[digest] = data
            expected_used += len(data)
            disk_ahead += 1
        facts["disk_ahead"] = disk_ahead

        disk_behind = 0
        for index in range(ROUNDS):
            facts.update({"active_stage": "disk-behind", "active_iteration": index})
            data = content("behind", index)
            digest, upload_id, path = begin(base, auth, data)
            assert call("PUT", path + "?offset=0", headers=auth, body=data[:1])[1] == {
                "offset": 1
            }
            local = stack.staging / upload_id
            local.write_bytes(b"")
            damaged = call("GET", path, headers=auth)
            assert damaged[0] == 409 and damaged[1]["error"] == {
                "code": "UPLOAD_DAMAGED",
                "details": {"restart_required": True},
            }
            assert not local.exists()
            expired = call("GET", path, headers=auth)
            assert expired[0] == 404 and expired[1]["error"]["code"] == "UPLOAD_EXPIRED"
            replacement_digest, replacement_id, replacement = begin(base, auth, data)
            assert replacement_digest == digest and replacement_id != upload_id
            assert call("PUT", replacement + "?offset=0", headers=auth, body=data)[1] == {
                "offset": len(data)
            }
            complete(replacement, auth, digest)
            committed[digest] = data
            expected_used += len(data)
            disk_behind += 1
        facts["disk_behind"] = disk_behind

        response_loss_recoveries = 0
        for index in range(ROUNDS):
            facts.update({"active_stage": "lost-complete", "active_iteration": index})
            data = content("lost-complete", index)
            digest, upload_id, path = begin(base, auth, data)
            assert call("PUT", path + "?offset=0", headers=auth, body=data)[1] == {
                "offset": len(data)
            }
            drop_complete_response(path + "/complete", auth["Authorization"])
            deadline = time.monotonic() + 10
            while (stack.staging / upload_id).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not (stack.staging / upload_id).exists()
            complete(path, auth, digest)
            complete(path, auth, digest)
            committed[digest] = data
            expected_used += len(data)
            response_loss_recoveries += 1
        facts["response_loss_recoveries"] = response_loss_recoveries

        cleanup_client = CleanupClient(stack)
        cleanup_complete = cleanup_removed = 0
        cleanup_latencies = []
        for index in range(ROUNDS):
            facts.update({"active_stage": "expiry-race", "active_iteration": index})
            data = content("cleanup", index)
            digest, upload_id, path = begin(base, auth, data)
            assert call("PUT", path + "?offset=0", headers=auth, body=data)[1] == {
                "offset": len(data)
            }
            barrier = threading.Barrier(2)
            complete_delay = 0 if index % 2 == 0 else 250
            cleanup_delay = 250 if index % 2 == 0 else 0

            def race_complete():
                barrier.wait(timeout=5)
                time.sleep(complete_delay / 1000)
                return call("POST", path + "/complete", headers=auth)

            def race_cleanup():
                barrier.wait(timeout=5)
                started = time.monotonic()
                outcome = cleanup_client.cleanup(
                    now=int(time.time()) + 7200, delay_ms=cleanup_delay
                )
                return outcome, int((time.monotonic() - started) * 1000)

            with ThreadPoolExecutor(max_workers=2) as pool:
                complete_future = pool.submit(race_complete)
                cleanup_future = pool.submit(race_cleanup)
                complete_result = complete_future.result(timeout=30)
                cleanup_result, latency = cleanup_future.result(timeout=30)
            cleanup_latencies.append(latency)
            if index % 2 == 0:
                assert complete_result[0] == 200 and cleanup_result["expired_uploads_removed"] == 0
                cleanup_complete += 1
                committed[digest] = data
                expected_used += len(data)
            else:
                assert complete_result[0] == 404
                assert complete_result[1]["error"]["code"] == "UPLOAD_EXPIRED"
                assert cleanup_result["expired_uploads_removed"] == 1
                cleanup_removed += 1
                cleaned[digest] = data
            assert not (stack.staging / upload_id).exists()
        cleanup_client.close()
        cleanup_client = None
        facts.update(
            {
                "cleanup_races": ROUNDS,
                "cleanup_split": {"complete": cleanup_complete, "cleanup": cleanup_removed},
                "max_cleanup_latency_ms": max(cleanup_latencies),
            }
        )

        vaults = call("GET", stack.origin + "/sync/v1/vaults", headers=auth)[1]["items"]
        current = next(item for item in vaults if item["id"] == vault_id)
        facts["used_bytes"] = current["used"]
        facts["quota_exact"] = current["used"] == expected_used
        facts["remaining_uploads"] = stack.sql_scalar(
            f"SELECT COUNT(*) FROM uploads WHERE vault_id='{vault_id}'"
        )
        facts["reserved_bytes"] = stack.sql_scalar(
            f"SELECT COALESCE(SUM(size),0) FROM uploads WHERE vault_id='{vault_id}'"
        )
        facts["object_count"] = stack.sql_scalar(
            f"SELECT COUNT(*) FROM objects WHERE vault_id='{vault_id}'"
        )
        receipt_count = stack.sql_scalar(
            f"SELECT COUNT(*) FROM upload_receipts WHERE vault_id='{vault_id}'"
        )
        facts["receipt_count_exact"] = receipt_count == len(committed)
        assert facts["object_count"] == len(committed)

        verified = 0
        for index, (digest, data) in enumerate(committed.items()):
            facts.update({"active_stage": "referenced-integrity", "active_iteration": index})
            response = call("GET", base + "/objects/" + digest, headers=auth)
            assert response[0] == 200 and response[1] == data
            verified += 1
        absent = 0
        for index, digest in enumerate(cleaned):
            facts.update({"active_stage": "cleaned-absence", "active_iteration": index})
            response = call("GET", base + "/objects/" + digest, headers=auth)
            assert response[0] == 404 and response[1]["error"]["code"] == "OBJECT_NOT_FOUND"
            absent += 1
        facts["referenced_objects_verified"] = verified
        facts["cleaned_objects_absent"] = absent
        facts["staging_files"] = len(list(stack.staging.iterdir()))
        stack.assert_running()
        assert len(stack.worker_ids()) == 2
        assert facts["quota_exact"] and facts["receipt_count_exact"]
        assert facts["remaining_uploads"] == facts["reserved_bytes"] == facts["staging_files"] == 0
        facts.update({"active_stage": "complete", "active_iteration": ROUNDS})
        status = "PASSED"
    except BaseException as error:
        reason = "S05_ORACLE_FAILED:" + type(error).__name__
    finally:
        if cleanup_client is not None:
            cleanup_client.close()
        if stack is not None:
            stack.stop()
    payload = result(
        case_id,
        status if case_id == "S-05" else "FAILED",
        reason or ("" if case_id == "S-05" else "CASE_ID_MISMATCH"),
        facts,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
