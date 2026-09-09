"""A-03：协议、传输限制、取消与提交验收驱动。"""

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
FRONTEND = ROOT / "frontend"
RUST_TESTS = (
    (("--lib",), "core::tests::protocol_incompatibility_rejects_pre_ready_broker_requests"),
    (("--bin", "notesagent-desktop"), "core_proxy_tests::a03_frozen_transfer_limits_and_failure_semantics"),
    (("--lib",), "request_lifecycle::tests::cancellation_before_dispatch_and_replay_never_run_work"),
    (("--lib",), "request_lifecycle::tests::cancelling_a_real_response_closes_its_socket"),
    (("--lib",), "workspace::tests::operation_receipt_survives_reopen_and_replay_after_later_edit"),
    (("--test", "core_workspace"), "real_core_notes_roundtrip_only_through_bound_host_and_confirm_commits"),
)
FRONTEND_TESTS = (
    "src/services/apiClient.cancellation.spec.ts",
    "src/services/platform/coreStream.spec.ts",
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
        {"name": "an incompatible protocol returns PROTOCOL_INCOMPATIBLE before any broker business call"},
        {"name": "JSON and binary requests accept exactly 64 MiB and reject the next byte"},
        {"name": "responses accept exactly 64 MiB while declared oversize and truncated bodies fail closed"},
        {"name": "pre-dispatch cancellation runs no work and in-flight cancellation closes the real socket"},
        {"name": "WebView stream abort, reader failure, and native cancellation retain frozen error semantics"},
        {"name": "a committed mutation remains queryable and exactly replayable by operation_id after reopen"},
    ]
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    passed = case_id == "A-03" and cargo is not None and pnpm is not None
    if passed:
        for selector, test in RUST_TESTS:
            command = [
                cargo,
                "test",
                "--manifest-path",
                str(MANIFEST),
                "--locked",
                "--features",
                "desktop",
                *selector,
                test,
                "--",
                "--exact",
                "--nocapture",
            ]
            passed = run(command, ROOT, "1 passed; 0 failed") and passed
        passed = run(
            [pnpm, "exec", "vitest", "run", *FRONTEND_TESTS, "--reporter=dot"],
            FRONTEND,
            "9 passed",
        ) and passed
    status = "PASSED" if passed else "FAILED"
    evidence = "six exact Rust transport/Workspace oracles and two exact frontend transport suites"
    for assertion in assertions:
        assertion.update({"status": status, "evidence": evidence})
    files = []
    for relative in (
        "frontend/src-tauri/src/core.rs",
        "frontend/src-tauri/src/main.rs",
        "frontend/src-tauri/src/request_lifecycle.rs",
        "frontend/src-tauri/src/workspace.rs",
        "frontend/src/services/platform/coreRequest.ts",
        "frontend/src/services/platform/coreStream.ts",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "An A-03 protocol, limit, cancellation, or commit oracle failed.",
        "assertions": assertions,
        "metrics": {"peak_rss_bytes": None, "max_process_count": None, "denied_access_count": None},
        "files": files,
        "revisions": [
            {"scope": "frozen transport bytes", "accepted": 67108864, "rejected": 67108865},
            {"scope": "committed operation replay", "replays": 100},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
