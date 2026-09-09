"""S-03 conflict matrix, first-bind, and rebind isolation acceptance driver."""

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
SERVICE_TEST = "s03_actual_service_converges_five_conflict_classes_twenty_rounds"
TARGET_RENAME_TEST = (
    "sync_resolution::tests::same_target_renames_preserve_the_occupant_for_all_choices_twenty_rounds"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(command: list[str]) -> bool:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    return completed.returncode == 0 and "1 passed; 0 failed" in completed.stdout


def result(case_id: str, status: str, reason: str, assertions: list[dict]) -> dict:
    evidence = f"cargo exact tests {SERVICE_TEST} and {TARGET_RENAME_TEST}"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "frontend/src-tauri/src/workspace.rs",
        "frontend/src-tauri/src/sync_inbox.rs",
        "frontend/src-tauri/src/sync_resolution.rs",
        "frontend/src-tauri/tests/sync_conflicts.rs",
        "server sync/tests/host_fixture.py",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {
                "scope": "actual two-client HTTP conflict matrix",
                "classes": 5,
                "rounds_per_class": 20,
                "resolution": "copy",
            },
            {
                "scope": "same-target rename durable choices",
                "rounds": 20,
                "choices": ["local", "remote", "copy"],
            },
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
        {"name": "same-edit conflicts preserve both contents and converge for twenty rounds"},
        {"name": "edit-delete conflicts preserve edited bytes and converge for twenty rounds"},
        {"name": "edit-rename conflicts preserve both contents and converge for twenty rounds"},
        {"name": "same-target renames restore the displaced server head and converge for twenty rounds"},
        {"name": "history restores preserve both versions and converge for twenty rounds"},
        {"name": "first binding to an empty Vault emits zero server delete revisions"},
        {"name": "unbind and rebind reject and never transmit the archived operation ID"},
        {"name": "all same-target choices remain complete after Workspace reopen"},
    ]
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    passed = case_id == "S-03" and cargo is not None
    if passed:
        passed = run_exact(
            [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--features",
                "desktop",
                "--test",
                "sync_conflicts",
                SERVICE_TEST,
                "--",
                "--exact",
                "--nocapture",
            ]
        )
        passed = run_exact(
            [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--lib",
                TARGET_RENAME_TEST,
                "--",
                "--exact",
                "--nocapture",
            ]
        ) and passed
    payload = result(
        case_id,
        "PASSED" if passed else "FAILED",
        "" if passed else "An exact S-03 conflict, convergence, or binding-isolation oracle failed.",
        assertions,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
