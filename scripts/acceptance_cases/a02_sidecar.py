"""A-02 authenticated Core transport and secret-exposure acceptance driver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
MANIFEST = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
PYTHON = BACKEND / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
PYTHON_TESTS = (
    "tests/test_sidecar_auth.py::test_session_auth_covers_every_route_and_rejects_duplicate_headers",
    "tests/test_sidecar_auth.py::test_session_auth_rejects_missing_wrong_and_old_generation_100_times_without_side_effects",
    "tests/test_sidecar_auth.py::test_real_sidecar_bootstrap_auth_and_parent_eof",
)
RUST_TESTS = (
    (
        "--test",
        "core_process",
        "real_python_core_authenticates_and_rotates_generation",
    ),
    (
        "--bin",
        "notesagent-desktop",
        "core_proxy_tests::request_dto_accepts_camel_case_and_rejects_unowned_headers",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path, expected: str) -> bool:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    return completed.returncode == 0 and expected in completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assertions = [
        {"name": "outermost authentication covers health, API, SSE, binary, docs, and unknown routes"},
        {"name": "one hundred missing, wrong, and old-generation requests each return 401 with zero business calls"},
        {"name": "duplicate authorization, browser Origin, and wrong Host are denied"},
        {"name": "real Core binds a random port, rotates generations, and keeps docs disabled under valid auth"},
        {"name": "session material is absent from URL, argv, environment, logs, data files, and WebView-owned DTO fields"},
    ]
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    passed = case_id == "A-02" and PYTHON.is_file() and cargo is not None
    if passed:
        passed = run([str(PYTHON), "-m", "pytest", *PYTHON_TESTS, "-q"], BACKEND, "3 passed")
        for selector, target, test in RUST_TESTS:
            command = [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--features",
                "desktop",
                selector,
                target,
                test,
                "--",
                "--exact",
                "--nocapture",
            ]
            passed = run(command, ROOT, "1 passed; 0 failed") and passed
    status = "PASSED" if passed else "FAILED"
    evidence = "three exact Python Sidecar tests and two exact Rust Host transport tests"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "backend/app/sidecar.py",
        "backend/tests/test_sidecar_auth.py",
        "frontend/src-tauri/src/core.rs",
        "frontend/src-tauri/src/main.rs",
        "frontend/src-tauri/tests/core_process.rs",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "An A-02 Sidecar authentication or Host transport oracle failed.",
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "unauthenticated rejection matrix", "missing": 100, "wrong": 100, "old_generation": 100},
            {"scope": "disabled documentation routes", "route_count": 4},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
