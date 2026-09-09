"""S-06：授权、撤销、速率限制与就绪状态验收。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sync_production_stack import ROOT, SyncProductionStack, call, sha256

ROUNDS = 100


def result(case_id: str, status: str, reason: str, facts: dict) -> dict:
    passed = status == "PASSED"
    assertions = [
        (
            "the oracle uses real PostgreSQL, MinIO, and both production workers",
            facts.get("dependencies") is True and facts.get("worker_count") == 2,
        ),
        (
            "100 cross-account or cross-vault hash guesses reveal no object",
            facts.get("hash_rejections") == ROUNDS and facts.get("content_leaks") == 0,
        ),
        (
            "a revoked device is rejected on the next request and for 100 requests",
            facts.get("immediate_revocation") is True
            and facts.get("revoked_rejections") == ROUNDS
            and facts.get("revoked_refresh_rejections") == ROUNDS,
        ),
        (
            "100 expired access-token requests are rejected and refresh rotates once",
            facts.get("expired_token_rejections") == ROUNDS
            and facts.get("refresh_replay_rejections") == ROUNDS
            and facts.get("rotated_access_works") is True,
        ),
        (
            "foreign-device and expired upload links reject 100 requests each",
            facts.get("foreign_upload_rejections") == ROUNDS
            and facts.get("expired_upload_rejections") == ROUNDS,
        ),
        (
            "database, S3, and staging faults and recoveries are visible within eight seconds",
            facts.get("readiness_transitions") == 6
            and facts.get("ready_failure_max_ms", 8001) < 8000
            and facts.get("ready_recovery_max_ms", 8001) < 8000,
        ),
        (
            "100 limited logins return 429 with Retry-After after the fixed threshold",
            facts.get("prelimit_rejections") == 10
            and facts.get("rate_limited_responses") == ROUNDS
            and facts.get("retry_after_responses") == ROUNDS,
        ),
    ]
    evidence = "real PostgreSQL 17, MinIO S3, two Uvicorn workers, and black-box HTTP"
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
            "hash_rejections": facts.get("hash_rejections", 0),
            "revoked_rejections": facts.get("revoked_rejections", 0),
            "expired_token_rejections": facts.get("expired_token_rejections", 0),
            "upload_link_rejections": facts.get("foreign_upload_rejections", 0)
            + facts.get("expired_upload_rejections", 0),
            "rate_limited_responses": facts.get("rate_limited_responses", 0),
            "ready_failure_max_ms": facts.get("ready_failure_max_ms", 0),
            "ready_recovery_max_ms": facts.get("ready_recovery_max_ms", 0),
            "worker_count": facts.get("worker_count", 0),
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "server sync/sync_server/app.py",
                "server sync/sync_server/database.py",
                "server sync/sync_server/readiness.py",
                "server sync/sync_server/storage.py",
                "server sync/tests/test_protocol.py",
                "server sync/tests/test_production_storage.py",
                "server sync/tests/test_readiness.py",
                "scripts/acceptance_cases/sync_production_stack.py",
                "scripts/acceptance_cases/s06_sync_security.py",
            )
        ],
        "revisions": [
            {
                "scope": "runtime",
                "postgres": facts.get("postgres_version"),
                "minio": facts.get("minio_version"),
            },
            {
                "scope": "authorization matrix",
                "hash": facts.get("hash_rejections", 0),
                "revoked": facts.get("revoked_rejections", 0),
                "revoked_refresh": facts.get("revoked_refresh_rejections", 0),
                "expired_token": facts.get("expired_token_rejections", 0),
                "foreign_upload": facts.get("foreign_upload_rejections", 0),
                "expired_upload": facts.get("expired_upload_rejections", 0),
            },
            {
                "scope": "readiness",
                "transitions": facts.get("readiness_transitions", 0),
                "failure_max_ms": facts.get("ready_failure_max_ms", 0),
                "recovery_max_ms": facts.get("ready_recovery_max_ms", 0),
                "dependencies": facts.get("readiness_dependencies", {}),
            },
            {
                "scope": "login limiting",
                "prelimit_401": facts.get("prelimit_rejections", 0),
                "limited_429": facts.get("rate_limited_responses", 0),
                "retry_after": facts.get("retry_after_responses", 0),
            },
            {
                "scope": "last checkpoint",
                "stage": facts.get("active_stage"),
                "iteration": facts.get("active_iteration", -1),
            },
        ],
    }


def auth(session: dict) -> dict:
    return {"Authorization": "Bearer " + session["access_token"]}


def upload(base: str, headers: dict, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    code, pending, _ = call(
        "POST", base + "/uploads", headers=headers, body={"content_hash": digest, "size": len(data)}
    )
    assert code == 200 and pending["complete"] is False
    path = base + "/uploads/" + pending["upload_id"]
    assert call("PUT", path + "?offset=0", headers=headers, body=data)[1] == {
        "offset": len(data)
    }
    completed = call("POST", path + "/complete", headers=headers)
    assert completed[0] == 200 and completed[1]["content_hash"] == digest
    return digest


def await_cluster_ready(origin: str, expected: int, *, timeout=8) -> int:
    started = time.monotonic()
    deadline = started + timeout
    observed: set[str] = set()

    def probe(_):
        try:
            return call(
                "GET",
                origin + "/ready",
                headers={"Connection": "close"},
                timeout=5,
            )
        except (OSError, TimeoutError):
            return None

    while time.monotonic() < deadline:
        with ThreadPoolExecutor(max_workers=8) as pool:
            samples = list(pool.map(probe, range(8)))
        for sample in samples:
            if sample is not None and sample[0] == expected:
                worker = sample[2].get("x-opennexus-worker")
                if worker:
                    observed.add(worker)
        if len(observed) == 2:
            return int((time.monotonic() - started) * 1000)
        time.sleep(0.05)
    raise RuntimeError("READINESS_TRANSITION_TIMEOUT")


def assert_error(response, status: int, code: str) -> None:
    assert response[0] == status and response[1]["error"]["code"] == code


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
    stack = None
    staging_available = None
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        stack = SyncProductionStack(
            config, Path(os.environ["OPENNEXUS_ACCEPTANCE_DATA_ROOT"]), "s06"
        ).start()
        facts.update(
            {
                "dependencies": True,
                "postgres_version": stack.postgres_version,
                "minio_version": stack.minio_version,
            }
        )

        owner_session = stack.login(stack.username, stack.password, "S-06 owner")
        owner_auth = auth(owner_session)
        _, owner_base, owner_vault = stack.create_vault_for_auth(owner_auth, "S-06 owner")
        _, sibling_base, _ = stack.create_vault_for_auth(owner_auth, "S-06 sibling")
        other_username, other_password = stack.add_user("s06-other")
        other_session = stack.login(other_username, other_password, "S-06 other account")
        other_auth = auth(other_session)
        stack.create_vault_for_auth(other_auth, "S-06 other vault")
        object_data = b"OpenNexus S-06 private object"
        object_hash = upload(owner_base, owner_auth, object_data)

        facts["active_stage"] = "hash-isolation"
        hash_rejections = content_leaks = 0
        for index in range(ROUNDS):
            facts["active_iteration"] = index
            if index % 2:
                response = call("GET", owner_base + "/objects/" + object_hash, headers=other_auth)
                assert_error(response, 404, "VAULT_NOT_FOUND")
            else:
                response = call("GET", sibling_base + "/objects/" + object_hash, headers=owner_auth)
                assert_error(response, 404, "OBJECT_NOT_FOUND")
            hash_rejections += 1
            content_leaks += int(object_data in json.dumps(response[1]).encode())
        facts.update({"hash_rejections": hash_rejections, "content_leaks": content_leaks})

        facts["active_stage"] = "device-revocation"
        revoked = stack.login(stack.username, stack.password, "S-06 revoked device")
        revoke = call(
            "DELETE",
            stack.origin + "/sync/v1/devices/" + revoked["device_id"],
            headers=owner_auth,
        )
        assert revoke[0] == 204
        revoked_auth = auth(revoked)
        rejected = call("GET", owner_base + "/changes", headers=revoked_auth)
        assert_error(rejected, 401, "SESSION_EXPIRED")
        facts["immediate_revocation"] = True
        revoked_rejections = 1
        for index in range(1, ROUNDS):
            facts["active_iteration"] = index
            response = call("GET", owner_base + "/changes", headers=revoked_auth)
            assert_error(response, 401, "SESSION_EXPIRED")
            revoked_rejections += 1
        revoked_refresh_rejections = 0
        for index in range(ROUNDS):
            response = call(
                "POST",
                stack.origin + "/sync/v1/auth/refresh",
                body={"refresh_token": revoked["refresh_token"]},
            )
            assert_error(response, 401, "SESSION_EXPIRED")
            revoked_refresh_rejections += 1
        facts.update(
            {
                "revoked_rejections": revoked_rejections,
                "revoked_refresh_rejections": revoked_refresh_rejections,
            }
        )

        facts["active_stage"] = "expired-access"
        expired = stack.login(stack.username, stack.password, "S-06 expired access")
        access_digest = hashlib.sha256(expired["access_token"].encode()).hexdigest()
        changed = stack.sql_scalar(
            "WITH changed AS (UPDATE sessions SET expires=0 "
            f"WHERE token='{access_digest}' RETURNING 1) SELECT COUNT(*) FROM changed"
        )
        assert changed == 1
        expired_rejections = 0
        expired_auth = auth(expired)
        for index in range(ROUNDS):
            facts["active_iteration"] = index
            response = call("GET", owner_base + "/changes", headers=expired_auth)
            assert_error(response, 401, "SESSION_EXPIRED")
            expired_rejections += 1
        facts["expired_token_rejections"] = expired_rejections

        facts["active_stage"] = "refresh-rotation"
        rotating = stack.login(stack.username, stack.password, "S-06 refresh rotation")
        rotated = call(
            "POST",
            stack.origin + "/sync/v1/auth/refresh",
            body={"refresh_token": rotating["refresh_token"]},
        )
        assert rotated[0] == 200
        refresh_replays = 0
        for index in range(ROUNDS):
            response = call(
                "POST",
                stack.origin + "/sync/v1/auth/refresh",
                body={"refresh_token": rotating["refresh_token"]},
            )
            assert_error(response, 401, "SESSION_EXPIRED")
            refresh_replays += 1
        facts["refresh_replay_rejections"] = refresh_replays
        rotated_access = call("GET", owner_base + "/changes", headers=auth(rotated[1]))
        facts["rotated_access_works"] = rotated_access[0] == 200

        facts["active_stage"] = "upload-link-isolation"
        upload_data = b"OpenNexus S-06 pending upload"
        upload_hash = hashlib.sha256(upload_data).hexdigest()
        pending = call(
            "POST",
            owner_base + "/uploads",
            headers=owner_auth,
            body={"content_hash": upload_hash, "size": len(upload_data)},
        )[1]
        upload_id = pending["upload_id"]
        upload_path = owner_base + "/uploads/" + upload_id
        foreign = stack.login(stack.username, stack.password, "S-06 foreign device")

        def rejected_upload_request(index: int, headers: dict):
            kind = index % 4
            if kind == 0:
                return call("GET", upload_path, headers=headers)
            if kind == 1:
                return call("PUT", upload_path + "?offset=0", headers=headers, body=b"x")
            if kind == 2:
                return call("POST", upload_path + "/complete", headers=headers)
            return call("DELETE", upload_path, headers=headers)

        foreign_upload_rejections = 0
        for index in range(ROUNDS):
            facts["active_iteration"] = index
            response = rejected_upload_request(index, auth(foreign))
            assert_error(response, 404, "UPLOAD_EXPIRED")
            foreign_upload_rejections += 1
        expired_rows = stack.sql_scalar(
            "WITH changed AS (UPDATE uploads SET expires=0 "
            f"WHERE id='{upload_id}' RETURNING 1) SELECT COUNT(*) FROM changed"
        )
        assert expired_rows == 1
        expired_upload_rejections = 0
        for index in range(ROUNDS):
            facts["active_iteration"] = index
            response = rejected_upload_request(index, owner_auth)
            assert_error(response, 404, "UPLOAD_EXPIRED")
            expired_upload_rejections += 1
        facts.update(
            {
                "foreign_upload_rejections": foreign_upload_rejections,
                "expired_upload_rejections": expired_upload_rejections,
            }
        )
        (stack.staging / upload_id).unlink(missing_ok=True)
        stack.sql_scalar(
            "WITH removed AS (DELETE FROM uploads "
            f"WHERE id='{upload_id}' RETURNING 1) SELECT COUNT(*) FROM removed"
        )

        facts["active_stage"] = "login-rate-limit"
        limited_username = "s06-limit-" + secrets.token_hex(8)
        login_url = stack.origin + "/sync/v1/auth/sessions"
        login_body = {
            "username": limited_username,
            "password": "invalid-controlled-password",
            "device_name": "S-06 limited",
        }
        prelimit = 0
        for index in range(10):
            response = call("POST", login_url, body=login_body)
            assert_error(response, 401, "LOGIN_FAILED")
            prelimit += 1
        limited = retry_after = 0
        for index in range(ROUNDS):
            facts["active_iteration"] = index
            response = call("POST", login_url, body=login_body)
            assert_error(response, 429, "RATE_LIMITED")
            limited += 1
            retry_after += int(response[2].get("retry-after") == "60")
        facts.update(
            {
                "prelimit_rejections": prelimit,
                "rate_limited_responses": limited,
                "retry_after_responses": retry_after,
            }
        )

        facts["active_stage"] = "readiness-faults"
        assert await_cluster_ready(stack.origin, 200) < 8000
        failure_latencies = []
        recovery_latencies = []

        started = time.monotonic()
        stack.stop_postgres()
        failure_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 503)
        )
        started = time.monotonic()
        stack.start_postgres()
        recovery_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 200)
        )

        started = time.monotonic()
        stack.stop_minio()
        failure_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 503)
        )
        started = time.monotonic()
        stack.start_minio()
        recovery_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 200)
        )

        staging_available = stack.stack / "staging-available"
        started = time.monotonic()
        stack.staging.rename(staging_available)
        stack.staging.write_text("unavailable", encoding="utf-8")
        failure_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 503)
        )
        started = time.monotonic()
        stack.staging.unlink()
        staging_available.rename(stack.staging)
        staging_available = None
        recovery_latencies.append(
            int((time.monotonic() - started) * 1000) + await_cluster_ready(stack.origin, 200)
        )

        facts.update(
            {
                "readiness_transitions": 6,
                "ready_failure_max_ms": max(failure_latencies),
                "ready_recovery_max_ms": max(recovery_latencies),
                "readiness_dependencies": {
                    name: {"failure_ms": failure, "recovery_ms": recovery}
                    for name, failure, recovery in zip(
                        ("postgresql", "minio", "staging"),
                        failure_latencies,
                        recovery_latencies,
                        strict=True,
                    )
                },
                "worker_count": len(stack.worker_ids()),
                "active_stage": "complete",
                "active_iteration": ROUNDS,
            }
        )
        assert facts["worker_count"] == 2
        assert max(failure_latencies + recovery_latencies) < 8000
        assert call("GET", owner_base + "/objects/" + object_hash, headers=owner_auth)[1] == object_data
        stack.assert_running()
        status = "PASSED"
    except BaseException as error:
        reason = "S06_ORACLE_FAILED:" + type(error).__name__
    finally:
        if stack is not None and staging_available is not None:
            if stack.staging.is_file():
                stack.staging.unlink()
            if staging_available.exists():
                staging_available.rename(stack.staging)
        if stack is not None:
            stack.stop()
    payload = result(
        case_id,
        status if case_id == "S-06" else "FAILED",
        reason or ("" if case_id == "S-06" else "CASE_ID_MISMATCH"),
        facts,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
