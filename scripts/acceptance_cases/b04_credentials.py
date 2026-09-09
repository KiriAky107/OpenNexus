"""B-04 transactional credential migration and confirmed cleanup oracle."""

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
RUST_TESTS = (
    "credentials::tests::b04_migration_survives_twenty_hard_terminations_per_boundary",
    "credentials::tests::b04_cleanup_is_confirmed_scoped_and_resumable",
)
PYTHON_TEST = (
    ROOT / "backend" / "tests" / "test_credentials.py",
    "test_migrated_fernet_owner_blocks_old_reads_and_writes",
)
UI_TEST = ROOT / "frontend" / "src" / "features" / "settings" / "CredentialVaultSettings.spec.ts"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], expected: str) -> bool:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    transcript = completed.stdout + completed.stderr
    print(transcript, end="")
    return completed.returncode == 0 and expected in transcript


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
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    passed = (
        case_id == "B-04"
        and os.name == "nt"
        and cargo is not None
        and python.is_file()
        and pnpm is not None
    )
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
                    "--test-threads=1",
                ],
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
            "1 passed",
        ) and passed
        passed = run(
            [
                pnpm,
                "--dir",
                "frontend",
                "exec",
                "vitest",
                "run",
                str(UI_TEST.relative_to(ROOT / "frontend")),
                "-t",
                "runs native-confirmed cleanup",
            ],
            "1 passed",
        ) and passed
    status = "PASSED" if passed else "FAILED"
    evidence = "exact Rust process-kill and cleanup oracles, Python legacy-owner oracle, and WebView boundary test"
    assertions = (
        "five migration persistence boundaries survive twenty hard process terminations each",
        "every recovery exposes a complete old or new state and preserves three exact records without duplicates",
        "legacy source, managed key, and encrypted backup remain until an exact native-confirmed source hash is supplied",
        "confirmed cleanup resumes across six boundaries, removes managed artifacts, and leaves an external environment key unchanged",
        "the old Python credential store rejects reads and writes after desktop ownership switches",
        "the WebView supplies neither a cleanup path nor confirmation and only invokes the native Host command",
    )
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "A B-04 transactional migration, cleanup, legacy-owner, or UI boundary oracle failed.",
        "assertions": [
            {"name": name, "status": status, "evidence": evidence} for name in assertions
        ],
        "metrics": {
            "migration_boundaries": 5 if passed else 0,
            "hard_terminations": 100 if passed else 0,
            "recovered_records": 300 if passed else 0,
            "duplicate_records": 0,
            "cleanup_boundaries": 6 if passed else 0,
            "managed_artifacts_remaining": 0 if passed else None,
            "external_key_changes": 0 if passed else None,
            "old_writer_rejections": 4 if passed else 0,
            "native_confirmation_boundary": 1 if passed else 0,
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "frontend/src-tauri/src/credentials.rs",
                "frontend/src-tauri/src/main.rs",
                "frontend/src/features/settings/CredentialVaultSettings.vue",
                "frontend/src/features/settings/CredentialVaultSettings.spec.ts",
                "backend/app/providers/credentials.py",
                "backend/tests/test_credentials.py",
                "scripts/acceptance_cases/b04_credentials.py",
            )
        ],
        "revisions": [
            {
                "scope": "migration",
                "states": ["backed_up", "copied", "verified", "switched"],
                "ownership_commit": "source .opennexus-owner.json",
            },
            {
                "scope": "cleanup",
                "states": ["cleanup_authorized", "cleanup_confirmed"],
                "external_environment_key_managed": False,
            },
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
