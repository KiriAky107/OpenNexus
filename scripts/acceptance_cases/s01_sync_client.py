"""S-01：双客户端离线链与响应丢失幂等性验收驱动。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from sync_test_service import configure_service, source_receipts, run_exact

ROOT = Path(__file__).resolve().parents[2]
TEST = "s01_actual_service_preserves_offline_chains_and_response_loss_idempotency"


def result(case_id: str, status: str, reason: str, assertions: list[dict]) -> dict:
    evidence = f"cargo exact integration test {TEST}"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = source_receipts(ROOT, ("frontend/src-tauri/tests/sync_push.rs", "sync-service/tests/host_fixture.py"),
        allow_missing=status != 'PASSED')
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "isolated ephemeral Vault", "offline_chain_count": 20, "idempotent_replays": 100},
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
        {"name": "both Rust clients retain twenty offline edits without silent pending loss"},
        {"name": "server revisions form a contiguous base and sequence chain"},
        {"name": "client kill after commit preserves pending work for reopen"},
        {"name": "one hundred identical operation retries return the original revision"},
        {"name": "both clients converge on content hash and stable file identity"},
    ]
    cargo = shutil.which("cargo")
    if case_id != "S-01" or cargo is None:
        payload = result(case_id, "FAILED", "S-01 requires the registered case ID and Cargo.", assertions)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    try:
        configure_service()
    except RuntimeError as error:
        payload = result(case_id, 'FAILED', str(error), assertions)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return 1
    command = [
        cargo,
        "test",
        "--manifest-path",
        str(ROOT / "frontend" / "src-tauri" / "Cargo.toml"),
        "--locked",
        "--features",
        "desktop",
        "--test",
        "sync_push",
        TEST,
        "--",
        "--exact",
        "--nocapture",
    ]
    passed = run_exact(command, cwd=ROOT)
    payload = result(
        case_id,
        "PASSED" if passed else "FAILED",
        "" if passed else "The exact Rust S-01 acceptance oracle failed.",
        assertions,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
