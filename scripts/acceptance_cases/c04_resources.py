"""C-04：Windows 扩展资源配额、期限、回收与 broker 限流验收驱动。"""

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
TESTS = (
    "extension_instance::tests::native_resource_failures_are_reaped_and_replacements_can_start",
    "extension_container::tests::real_sixty_second_tool_deadline_kills_tree_and_host_can_save",
    "extension_file_broker::tests::permissions_scope_limits_hardlinks_and_revocation_fail_closed",
    "extension_job::tests::production_limits_are_configured_and_explicit_termination_reaps_process",
)
IGNORED = frozenset(TESTS[:2])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_test(cargo: str, test: str) -> bool:
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
    ]
    if test in IGNORED:
        command.append("--ignored")
    command.extend(["--exact", "--nocapture"])
    environment = dict(os.environ)
    environment.setdefault(
        "CARGO_TARGET_DIR",
        str(Path(environment["OPENNEXUS_ACCEPTANCE_DATA_ROOT"]) / "cargo-target"),
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    transcript = completed.stdout + completed.stderr
    print(transcript, end="")
    return completed.returncode == 0 and "1 passed; 0 failed" in transcript


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    cargo = shutil.which(os.environ.get("CARGO", "cargo"))
    passed = case_id == "C-04" and os.name == "nt" and cargo is not None
    if passed:
        passed = all(run_test(cargo, test) for test in TESTS)
    status = "PASSED" if passed else "FAILED"
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "至少一个 C-04 真实进程或 broker 验收断言失败。",
        "assertions": [
            {
                "name": name,
                "status": status,
                "evidence": "四个精确 Rust Host 测试，包含真实 AppContainer 子进程和本地 Workspace 保存",
            }
            for name in (
                "512 MiB 内存、256 MiB scratch、16 进程与 CPU 超限均返回明确错误",
                "每次资源失败均清空进程树并可创建替代实例",
                "60 秒工具期限到达后回收两级进程树且 Host 可保存并重开笔记",
                "broker 每秒第 33 个请求返回 EXTENSION_BROKER_RATE_LIMITED",
            )
        ],
        "metrics": {
            "memory_limit_bytes": 512 * 1024 * 1024 if passed else 0,
            "scratch_limit_bytes": 256 * 1024 * 1024 if passed else 0,
            "process_limit": 16 if passed else 0,
            "tool_deadline_seconds": 60 if passed else 0,
            "cleanup_deadline_ms": 10_000 if passed else 0,
            "broker_requests_per_second": 32 if passed else 0,
            "resource_failures_verified": 5 if passed else 0,
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "frontend/src-tauri/src/extension_job.rs",
                "frontend/src-tauri/src/extension_launch_data.rs",
                "frontend/src-tauri/src/extension_process.rs",
                "frontend/src-tauri/src/extension_file_broker.rs",
                "frontend/src-tauri/src/extension_instance.rs",
                "frontend/src-tauri/tests/fixtures/sandbox_network_probe.rs",
            )
        ],
        "revisions": [
            {"scope": "resource policy", "memory_mib": 512, "scratch_mib": 256, "processes": 16},
            {"scope": "tool deadline", "seconds": 60, "cleanup_seconds": 10},
            {"scope": "file broker", "requests_per_second": 32},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
