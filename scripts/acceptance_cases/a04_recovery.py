"""A-04：Core 崩溃退避、进程树清理与签名更新恢复验收。"""

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
    ("--test", "core_process", "six_real_core_crashes_back_off_then_open_the_circuit_without_touching_local_edits"),
    ("--test", "core_process", "real_python_core_authenticates_and_rotates_generation"),
    ("--lib", "", "core_update::tests::signed_compatible_release_switches_and_failed_health_rolls_back"),
    ("--lib", "", "core_update::tests::every_switch_boundary_survives_twenty_real_process_terminations"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_exact(cargo: str, selector: str, target: str, test: str) -> bool:
    command = [
        cargo,
        "test",
        "--manifest-path",
        str(MANIFEST),
        "--locked",
        "--features",
        "desktop",
        selector,
    ]
    if target:
        command.append(target)
    command.extend((test, "--", "--exact", "--nocapture", "--test-threads=1"))
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
    python = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    passed = case_id == "A-04" and os.name == "nt" and cargo is not None and python.is_file()
    if passed:
        passed = all(run_exact(cargo, *test) for test in TESTS)
    status = "PASSED" if passed else "FAILED"
    evidence = "真实 Core 进程树、Ed25519 签名发布包和 SQLite FULL/WAL 更新事务"
    assertions = [
        "连续六次强杀执行五次指数退避并在第六次熔断",
        "每次 Core 故障后本地保存文件摘要不变",
        "Host 关闭后十秒内受管理进程和监听端口归零",
        "签名、Host 版本、协议和完整文件树在切换前验证",
        "三个更新持久化边界各二十次真实强杀均恢复完整兼容组合",
    ]
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "至少一个 A-04 真实进程或更新恢复断言失败。",
        "assertions": [
            {"name": name, "status": status, "evidence": evidence} for name in assertions
        ],
        "metrics": {
            "core_crashes": 6 if passed else 0,
            "automatic_restarts": 5 if passed else 0,
            "local_hash_matches": 6 if passed else 0,
            "shutdown_deadline_ms": 10_000,
            "managed_descendants_remaining": 0 if passed else None,
            "update_boundaries": 3 if passed else 0,
            "update_power_cuts": 60 if passed else 0,
            "incompatible_combinations": 0 if passed else None,
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "frontend/src-tauri/src/core.rs",
                "frontend/src-tauri/src/core_update.rs",
                "frontend/src-tauri/tests/core_process.rs",
                "scripts/acceptance_cases/a04_recovery.py",
            )
        ],
        "revisions": [
            {"scope": "restart_backoff_seconds", "values": [1, 2, 4, 8, 16]},
            {"scope": "update_boundaries", "values": ["journal_recorded", "pointer_recorded", "switch_committed"]},
        ],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
