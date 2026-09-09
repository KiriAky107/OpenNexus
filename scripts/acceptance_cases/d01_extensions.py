"""D-01：签名包、ZIP 限制与安全解压验收驱动。"""

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
    "extension_package::tests::python_signature_binds_all_metadata_archive_and_signer",
    "extension_package::tests::zip_rejects_traversal_aliases_links_duplicates_and_corrupt_local_headers",
    "extension_package::tests::compressed_limits_are_inclusive_for_both_categories",
    "extension_package::tests::category_entry_and_expanded_limits_accept_boundary_and_reject_next_byte",
    "extension_trust::tests::live_responses_require_pinned_key_exact_release_and_no_revocation",
    "extension_trust::tests::archive_download_verifies_real_fixture_and_enforces_stream_size",
    "extension_unpack::tests::signed_package_extracts_and_rejects_modified_extra_missing_and_linked_files",
    "extension_unpack::tests::junction_parent_cannot_redirect_output",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(cargo: str, test: str) -> bool:
    command = [
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
    ]
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
        {"name": "Python-produced Ed25519 vector verifies in Rust and metadata/archive/signer tampering fails"},
        {"name": "live key revocation, rotation, withdrawal, duplicate release, redirect, and stale trust fail closed"},
        {"name": "Theme and Plugin compressed, expanded, and entry limits accept the boundary only"},
        {"name": "traversal, aliases, links, duplicates, corrupt headers, encryption, and ZIP bombs are rejected"},
        {"name": "signed extraction detects extra, missing, modified, and hard-linked files"},
        {"name": "junction redirection creates zero files outside the installation root"},
    ]
    cargo = shutil.which("cargo")
    passed = case_id == "D-01" and cargo is not None and os.name == "nt"
    if passed:
        passed = all([run_exact(cargo, test) for test in TESTS])
    status = "PASSED" if passed else "FAILED"
    evidence = "eight registered exact Rust package/trust/unpack oracles"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "frontend/src-tauri/src/extension_package.rs",
        "frontend/src-tauri/src/extension_trust.rs",
        "frontend/src-tauri/src/extension_unpack.rs",
        "frontend/src/services/fixtures/community-python-vector.json",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "D-01 requires Windows P0, Cargo, and all eight exact oracles.",
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "archive classes", "classes": ["theme", "plugin"]},
            {"scope": "live trust responses", "cases": 8},
            {"scope": "archive download responses", "cases": 4},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
