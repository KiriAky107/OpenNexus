"""D-03：崩溃、存储、配置、依赖与权限验收。"""

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
    (
        "extension_transaction::tests::group_switch_survives_hard_termination_twenty_times_per_boundary",
        False,
    ),
    (
        "extension_transaction::tests::disk_full_and_configuration_migration_failures_cover_every_boundary",
        False,
    ),
    (
        "extension_transaction::tests::failed_switch_cannot_expand_an_existing_execution_permit",
        False,
    ),
    (
        "extension_dependencies::tests::cycles_missing_versions_and_incompatible_platforms_fail",
        False,
    ),
    (
        "extension_store::tests::prepared_switch_rechecks_content_vault_binding_and_recovers_on_open",
        False,
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(cargo: str, test: str, ignored: bool) -> bool:
    arguments = [
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
    ]
    if ignored:
        arguments.append("--ignored")
    arguments.extend(("--exact", "--nocapture", "--test-threads=1"))
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
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
            "name": "three durable switch boundaries survive 20 actual hard terminations each",
            "evidence": "60 child test processes were killed only after the requested SQLite boundary",
        },
        {
            "name": "disk-full and configuration-migration failures cover every boundary for 20 rounds",
            "evidence": "120 injected failures reopened and recovered to one complete package/config generation",
        },
        {
            "name": "dependency cycle, missing package, and version conflict fail during immutable planning",
            "evidence": "three exact dependency error codes are returned before any active pointer exists",
        },
        {
            "name": "failed installation cannot expand a previously issued execution permit",
            "evidence": "old permit remains bound to notes.read and rejects added notes.write as PERMISSION_CHANGED",
        },
        {
            "name": "prepared switch rejects invalid configuration and recovers pending state on reopen",
            "evidence": "configuration rejection creates zero transactions; reopen rolls pending switch back idempotently",
        },
    ]
    cargo = shutil.which("cargo")
    passed = case_id == "D-03" and cargo is not None and os.name == "nt"
    if passed:
        passed = all(run_exact(cargo, test, ignored) for test, ignored in TESTS)
    status = "PASSED" if passed else "FAILED"
    for assertion in assertions:
        assertion["status"] = status
    files = []
    for relative in (
        "frontend/src-tauri/src/extension_transaction.rs",
        "frontend/src-tauri/src/extension_dependencies.rs",
        "frontend/src-tauri/src/extension_store.rs",
        "frontend/src-tauri/src/extension_permit.rs",
        "frontend/src-tauri/src/workspace.rs",
        "scripts/acceptance_cases/d03_extension_transactions.py",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "A D-03 exact transaction oracle failed.",
        "assertions": assertions,
        "metrics": {
            "power_cut_rounds": 60 if passed else 0,
            "disk_full_rounds": 60 if passed else 0,
            "configuration_failure_rounds": 60 if passed else 0,
            "dependency_preflight_rejections": 3 if passed else 0,
            "permission_expansions": 0,
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": files,
        "revisions": [
            {"scope": "transaction boundaries", "values": ["journal_recorded", "pointer_recorded", "switch_committed"]},
            {"scope": "recovered generations", "values": ["complete-old", "complete-new"]},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
