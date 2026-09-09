"""C-03 execution-permit binding and legacy-launch rejection oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
RUST_TESTS = (
    "extension_permit::tests::every_claim_is_bound_expiry_and_key_rotation_fail_closed",
    "extension_launch_authorization::tests::permit_entry_context_and_package_scoped_secrets_drive_launch_data",
)
PYTHON_TEST = (
    ROOT / "backend" / "tests" / "test_mcp_registry.py",
    "test_production_rejects_process_launch_even_after_approval",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path, expected: str) -> bool:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    transcript = completed.stdout + completed.stderr
    print(transcript, end="")
    return completed.returncode == 0 and expected in completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    python = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    passed = case_id == "C-03" and os.name == "nt" and cargo is not None and python.is_file()
    if passed:
        for test in RUST_TESTS:
            passed = run(
                [
                    cargo,
                    "test",
                    "--manifest-path",
                    str(MANIFEST),
                    "--locked",
                    "--features",
                    "desktop",
                    "--lib",
                    test,
                    "--",
                    "--exact",
                    "--nocapture",
                ],
                ROOT,
                "1 passed; 0 failed",
            ) and passed
        passed = run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                f"{PYTHON_TEST[0]}::{PYTHON_TEST[1]}",
            ],
            ROOT,
            "1 passed",
        ) and passed
    status = "PASSED" if passed else "FAILED"
    evidence = "two exact Rust Host permit oracles and the production-disabled Python launch oracle"
    assertions = [
        "entry, arguments, permissions, package digests, Vault, platform, policy, and every other claim are authenticated",
        "changed valid claims return PERMISSION_CHANGED and expired claims return EXTENSION_PERMIT_EXPIRED",
        "revocation invalidates existing leases and a fresh Host authority rejects pre-restart permits",
        "the legacy Python stdio launch path remains disabled after configuration approval",
        "package-scoped credentials and pinned entry identity are rechecked immediately before process creation",
    ]
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "A C-03 exact permit or legacy-launch oracle failed.",
        "assertions": [
            {"name": name, "status": status, "evidence": evidence} for name in assertions
        ],
        "metrics": {
            "bound_claim_fields": 16 if passed else 0,
            "python_bypass_rejections": 1 if passed else 0,
            "restart_rejections": 1 if passed else 0,
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "frontend/src-tauri/src/extension_permit.rs",
                "frontend/src-tauri/src/extension_launch_authorization.rs",
                "backend/app/extensions/mcp_registry.py",
                "backend/tests/test_mcp_registry.py",
            )
        ],
        "revisions": [
            {"scope": "permit", "format": "HMAC-SHA256 v2", "bound_claim_fields": 16},
            {"scope": "restart", "authority_key_persisted": False},
            {"scope": "legacy Python launch", "production_enabled": False},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
