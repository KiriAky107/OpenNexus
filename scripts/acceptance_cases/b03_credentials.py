"""B-03：凭据锁定、密码轮换、恢复与编辑可用性验收。"""

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
TESTS = (
    ("lib", "session_lock::tests::b03_native_and_manual_lock_reject_new_resolves_within_one_second"),
    ("bin", "lifecycle_tests::manual_lock_revokes_before_waiting_for_credential_mutex"),
    ("lib", "credentials::tests::stronghold_roundtrip_scope_lock_and_password_rotation"),
    ("lib", "credentials::tests::encrypted_backup_restores_corrupt_store_without_overwrite_on_failure"),
    ("lib", "credentials::tests::b03_unrecoverable_store_does_not_block_local_editing"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(cargo: str, target: str, test: str) -> bool:
    command = [
        cargo,
        "test",
        "--manifest-path",
        str(MANIFEST),
        "--locked",
        "--features",
        "desktop",
    ]
    command.extend(("--lib",) if target == "lib" else ("--bin", "notesagent-desktop"))
    command.extend((test, "--", "--exact", "--nocapture", "--test-threads=1"))
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    return completed.returncode == 0 and "1 passed; 0 failed" in completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assertions = [
        {
            "name": "Windows session-lock and manual-lock events reject a new resolve within one second",
            "evidence": "native hidden-window WTS_SESSION_LOCK and direct broker lock use the production epoch signal",
        },
        {
            "name": "Host manual lock revokes credentials before waiting for a contended broker mutex",
            "evidence": "desktop Host exact oracle observes both credential and extension generations within one second",
        },
        {
            "name": "wrong password fails and password rotation preserves the encrypted record",
            "evidence": "old password fails after rotation; new password reopens the same one-record Stronghold",
        },
        {
            "name": "a corrupt store restores from an encrypted backup without destructive failed attempts",
            "evidence": "wrong backup password preserves corrupt bytes; valid restore recovers the exact record and remains locked",
        },
        {
            "name": "an unrecoverable credential store leaves local note editing available",
            "evidence": "corrupt credential bytes remain untouched while a separate Workspace writes and reads a note",
        },
    ]
    cargo = shutil.which("cargo")
    passed = case_id == "B-03" and cargo is not None and os.name == "nt"
    if passed:
        passed = all(run_exact(cargo, target, test) for target, test in TESTS)
    status = "PASSED" if passed else "FAILED"
    for assertion in assertions:
        assertion["status"] = status
    files = []
    for relative in (
        "frontend/src-tauri/src/credentials.rs",
        "frontend/src-tauri/src/session_lock.rs",
        "frontend/src-tauri/src/main.rs",
        "frontend/src-tauri/src/workspace.rs",
        "scripts/acceptance_cases/b03_credentials.py",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    result = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "A B-03 exact credential lifecycle oracle failed.",
        "assertions": assertions,
        "metrics": {
            "lock_deadline_ms": 1000,
            "native_lock_events": 1 if passed else 0,
            "manual_lock_events": 1 if passed else 0,
            "restored_records": 1 if passed else 0,
            "local_edit_successes": 1 if passed else 0,
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": files,
        "revisions": [
            {"scope": "credential states", "values": ["unlocked", "locked", "restored-locked"]},
            {"scope": "password generations", "values": ["old-rejected", "new-accepted"]},
        ],
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
