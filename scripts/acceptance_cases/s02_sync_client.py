"""S-02：附件断点续传与拉取边界崩溃验收驱动。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from sync_test_service import configure_service, source_receipts, run_exact as run_service_exact

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
UPLOAD_TEST = "s01_actual_service_preserves_offline_chains_and_response_loss_idempotency"
PULL_TEST = "sync_inbox::tests::s02_pull_each_persistence_boundary_survives_twenty_process_kills"


def run_exact(command: list[str]) -> bool:
    return run_service_exact(command, cwd=ROOT)


def result(case_id: str, status: str, reason: str, assertions: list[dict]) -> dict:
    evidence = f"cargo exact tests {UPLOAD_TEST} and {PULL_TEST}"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = source_receipts(ROOT, (
        "frontend/src-tauri/src/sync_inbox.rs",
        "frontend/src-tauri/tests/sync_push.rs",
        "sync-service/tests/host_fixture.py",
    ), allow_missing=status != 'PASSED')
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "resumable attachment", "size_bytes": 104857600, "process_kills": 10},
            {"scope": "pull persistence boundaries", "boundary_count": 4, "kills_per_boundary": 20},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assertions = [
        {"name": "100 MiB upload resumes after a process kill at every 10 MiB durable offset"},
        {"name": "resumed attachment length and SHA-256 equal the source"},
        {"name": "pull spool, stage, file, and cursor boundaries each survive twenty process kills"},
        {"name": "pull cursor never advances beyond the materialized revision"},
        {"name": "remote materialization creates no local outbox operation"},
        {"name": "attachment bodies remain outside SQLite metadata storage"},
    ]
    cargo = shutil.which("cargo")
    if case_id != "S-02" or cargo is None:
        payload = result(case_id, "FAILED", "S-02 requires the registered case ID and Cargo.", assertions)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    try:
        configure_service()
    except RuntimeError as error:
        payload = result(case_id, 'FAILED', str(error), assertions)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return 1

    pull_passed = run_exact(
        [
            cargo,
            "test",
            "--manifest-path",
            str(MANIFEST),
            "--locked",
            "--lib",
            PULL_TEST,
            "--",
            "--ignored",
            "--exact",
            "--nocapture",
        ]
    )
    upload_passed = run_exact(
        [
            cargo,
            "test",
            "--manifest-path",
            str(MANIFEST),
            "--locked",
            "--features",
            "desktop",
            "--test",
            "sync_push",
            UPLOAD_TEST,
            "--",
            "--exact",
            "--nocapture",
        ]
    )
    passed = pull_passed and upload_passed
    payload = result(
        case_id,
        "PASSED" if passed else "FAILED",
        "" if passed else "At least one exact Rust S-02 acceptance oracle failed.",
        assertions,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
