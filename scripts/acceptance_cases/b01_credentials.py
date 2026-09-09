"""B-01 scoped Stronghold and plaintext-exposure acceptance driver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
MATRIX_TEST = "credentials::tests::b01_domain_matrix_never_exposes_or_cross_resolves_secrets"
CORE_TEST = "real_core_credential_api_uses_host_stronghold_without_plaintext_response"
SYNC_TEST = "sync_auth::tests::lock_cancels_inflight_and_scopes_do_not_expose_tokens"
PLANTED_VALUES = (
    "b01-planted-",
    "fixture-credential-via-host-pipe",
    "private-access",
    "private-refresh",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(command: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    transcript = completed.stdout + completed.stderr
    print(transcript, end="")
    passed = completed.returncode == 0 and "1 passed; 0 failed" in completed.stdout
    return passed, transcript


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assertions = [
        {"name": "Provider, Plugin, MCP, and Sync each retain three same-domain secrets"},
        {"name": "all thirty-six cross-domain and nine cross-owner resolutions are denied"},
        {"name": "real Core credential write and status responses expose no plaintext"},
        {"name": "Stronghold bytes, public metadata, and sync payloads contain no planted secret"},
        {"name": "captured acceptance logs contain no planted secret or Sync token"},
    ]
    cargo = shutil.which("cargo")
    transcripts: list[str] = []
    passed = case_id == "B-01" and cargo is not None
    if passed:
        commands = [
            [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--lib",
                MATRIX_TEST,
                "--",
                "--exact",
                "--nocapture",
            ],
            [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--features",
                "desktop",
                "--lib",
                SYNC_TEST,
                "--",
                "--exact",
                "--nocapture",
            ],
            [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--features",
                "desktop",
                "--test",
                "core_process",
                CORE_TEST,
                "--",
                "--exact",
                "--nocapture",
            ],
        ]
        for command in commands:
            command_passed, transcript = run_exact(command)
            transcripts.append(transcript)
            passed = command_passed and passed
    leaked = any(value in "".join(transcripts) for value in PLANTED_VALUES)
    passed = passed and not leaked
    status = "PASSED" if passed else "FAILED"
    evidence = f"cargo exact tests {MATRIX_TEST}, {SYNC_TEST}, and {CORE_TEST}"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "frontend/src-tauri/src/credentials.rs",
        "frontend/src-tauri/src/sync_auth.rs",
        "frontend/src-tauri/tests/core_process.rs",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "A B-01 exact oracle failed or its transcript exposed a planted value.",
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "Stronghold domains", "domain_count": 4, "secrets_per_domain": 3},
            {"scope": "resolver denial matrix", "cross_domain_checks": 36, "cross_owner_checks": 9},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
