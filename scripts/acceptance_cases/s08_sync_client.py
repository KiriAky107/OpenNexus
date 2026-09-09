"""S-08：同步分类与逻辑记录兼容性验收驱动。"""

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
SERVICE_TEST = "s08_actual_service_enforces_classification_and_roundtrips_selected_records"
SCHEMA_TEST = "preference_records::tests::s08_optional_schemas_are_strict_and_installations_cannot_authorize"
QUEUE_TEST = "records::tests::s08_incompatible_remote_schema_preserves_local_file_and_upload_queue"
MIGRATION_TEST = "workspace::tests::schema_twelve_upgrade_adds_optional_categories_without_consent"


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
    evidence = f"cargo exact tests {SERVICE_TEST}, {SCHEMA_TEST}, {QUEUE_TEST}, and {MIGRATION_TEST}"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "frontend/src-tauri/src/records.rs",
        "frontend/src-tauri/src/preference_records.rs",
        "frontend/src-tauri/src/sync_discovery.rs",
        "frontend/src-tauri/src/sync_scope.rs",
        "frontend/src-tauri/src/workspace.rs",
        "frontend/src-tauri/tests/sync_classification.rs",
        "frontend/src/features/settings/SyncSettings.vue",
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
            {"scope": "actual two-client HTTP classification roundtrip", "default_records": 6, "all_selected_records": 12},
            {"scope": "logical schema", "version": 1, "workspace_schema": 13},
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
        {"name": "classification fixtures cover default, optional, forbidden, and unknown data"},
        {"name": "the complete default set roundtrips through the actual Sync service"},
        {"name": "unselected optional records produce zero remote revisions"},
        {"name": "secrets, indexes, vectors, logs, models, and caches produce zero remote revisions"},
        {"name": "selected optional records roundtrip through the actual Sync service"},
        {"name": "target extension installation records contain no permission or trust state"},
        {"name": "an incompatible record schema rejects the write and preserves the local upload queue"},
        {"name": "schema 12 upgrades with a readable backup and no inferred consent"},
    ]
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    passed = case_id == "S-08" and cargo is not None
    if passed:
        commands = [
            [cargo, "test", "--manifest-path", str(MANIFEST), "--locked", "--features", "desktop", "--test", "sync_classification", SERVICE_TEST, "--", "--exact", "--nocapture"],
            [cargo, "test", "--manifest-path", str(MANIFEST), "--locked", "--lib", SCHEMA_TEST, "--", "--exact", "--nocapture"],
            [cargo, "test", "--manifest-path", str(MANIFEST), "--locked", "--lib", QUEUE_TEST, "--", "--exact", "--nocapture"],
            [cargo, "test", "--manifest-path", str(MANIFEST), "--locked", "--lib", MIGRATION_TEST, "--", "--exact", "--nocapture"],
        ]
        for command in commands:
            passed = run_exact(command) and passed
    payload = result(
        case_id,
        "PASSED" if passed else "FAILED",
        "" if passed else "An exact S-08 classification, roundtrip, compatibility, or migration oracle failed.",
        assertions,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
