"""B-02 Fernet-to-Stronghold migration acceptance driver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = "credentials::tests::b02_fernet_migration_matrix_is_atomic_verified_and_idempotent"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cargo = shutil.which("cargo")
    if case_id != "B-02" or cargo is None:
        result = {
            "schema": 1,
            "case_id": case_id,
            "status": "FAILED",
            "reason": "B-02 requires the registered case ID and Cargo.",
            "assertions": [{"name": "driver prerequisites", "status": "FAILED", "evidence": "case ID or Cargo missing"}],
            "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
            "files": [],
            "revisions": [],
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    command = [
        cargo,
        "test",
        "--manifest-path",
        str(ROOT / "frontend" / "src-tauri" / "Cargo.toml"),
        "--locked",
        "--lib",
        TEST,
        "--",
        "--exact",
        "--nocapture",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    passed = completed.returncode == 0 and "1 passed; 0 failed" in completed.stdout
    status = "PASSED" if passed else "FAILED"
    evidence = f"cargo exact test {TEST}"
    assertions = [
        {"name": "100 records preserve IDs and decrypted values", "status": status, "evidence": evidence},
        {"name": "local and environment master keys are handled without source mutation", "status": status, "evidence": evidence},
        {"name": "three repeat imports add no records", "status": status, "evidence": evidence},
        {"name": "empty source switches with zero records", "status": status, "evidence": evidence},
        {"name": "missing key and bad token do not switch or mutate source/target", "status": status, "evidence": evidence},
        {"name": "same target ID with a different value rejects the full migration", "status": status, "evidence": evidence},
    ]
    fixture = ROOT / "frontend" / "src-tauri" / "tests" / "fixtures" / "fernet-python.json"
    result = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "The exact Rust B-02 acceptance oracle failed.",
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": [{"path": "frontend/src-tauri/tests/fixtures/fernet-python.json", "sha256": sha256(fixture)}],
        "revisions": [],
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
