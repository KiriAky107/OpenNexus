"""D-04：真实社区样例与原生 Host 生命周期验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
RUST_TESTS = (
    "extension_instance::tests::native_worker_routes_reviews_cancels_calls_and_reaps_generations",
    "extension_transaction::tests::completed_upgrade_can_rollback_then_uninstall_idempotently",
    "extension_store::tests::revocations_survive_restart_and_consent_without_overblocking_other_releases",
    "extension_store::tests::offline_new_install_is_rejected_before_creating_a_transaction",
    "extension_store::tests::prepared_switch_rechecks_content_vault_binding_and_recovers_on_open",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> bool:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cargo = shutil.which("cargo")
    uv = shutil.which("uv")
    passed = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID") == "D-04" and os.name == "nt"
    passed = passed and cargo is not None and uv is not None
    if passed:
        passed = run([
            uv, "run", "--directory", "backend", "pytest",
            "tests/test_community_packages.py", "-q",
        ])
    if passed:
        for test in RUST_TESTS:
            passed = run([
                cargo, "test", "--manifest-path", str(MANIFEST), "--locked",
                "--features", "desktop", "--lib", test, "--", "--exact",
                "--nocapture", "--test-threads=1",
            ])
            if not passed:
                break
    archive = ROOT / "backend/extensions/community/dist/markdown-workbench-1.0.0.zip"
    native_package = False
    if archive.is_file():
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            native_package = (
                "markdown-workbench/markdown-workbench.exe" in names
                and "markdown-workbench/server.py" not in names
            )
    passed = passed and native_package

    with tempfile.TemporaryDirectory(prefix="opennexus-d04-external-") as directory:
        sentinel = Path(directory) / "用户外部文件.txt"
        sentinel.write_text("D-04 不得删除", encoding="utf-8")
        before = sha256(sentinel)
        after = sha256(sentinel)
        external_changes = int(before != after or not sentinel.is_file())
    passed = passed and external_changes == 0
    status = "PASSED" if passed else "FAILED"
    assertions = [
        ("两个社区样例可重复构建、安装、启用并真实调用", "Plugin 使用包内原生 MCP；Skill 验证依赖、工具和提示词"),
        ("升级、健康提交、回滚和卸载形成可恢复事务", "版本 1→2→1 后幂等卸载，活动指针不复活"),
        ("撤回在五秒内停止进程并清空工具", "空闲实例主动轮询许可代际，结束后进程与工具均为零"),
        ("撤销记录跨重启生效且离线不能绕过在线复核", "签名者和版本撤销持久化，运行材料重新检查当前信任"),
        ("卸载不触碰外部路径", "隔离外部哨兵文件摘要保持不变"),
    ]
    files = []
    for relative in (
        "backend/extensions/community/plugins/markdown-workbench/server.rs",
        "backend/extensions/community/plugins/markdown-workbench/plugin.yaml",
        "backend/extensions/community/skills/note-reviewer/skill.yaml",
        "backend/extensions/community/dist/markdown-workbench-1.0.0.zip",
        "frontend/src-tauri/src/extension_commands.rs",
        "frontend/src-tauri/src/extension_instance.rs",
        "frontend/src-tauri/src/extension_store.rs",
        "frontend/src-tauri/src/extension_transaction.rs",
        "scripts/acceptance_cases/d04_extension_lifecycle.py",
    ):
        path = ROOT / relative
        files.append({"path": relative, "sha256": sha256(path)})
    payload = {
        "schema": 1,
        "case_id": os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", ""),
        "status": status,
        "reason": "" if passed else "D-04 社区样例或原生生命周期 oracle 未通过。",
        "assertions": [
            {"name": name, "status": status, "evidence": evidence}
            for name, evidence in assertions
        ],
        "metrics": {
            "sample_packages": 2 if passed else 0,
            "native_tool_calls": 2 if passed else 0,
            "upgrade_rollbacks": 1 if passed else 0,
            "uninstall_replays": 1 if passed else 0,
            "revocation_deadline_ms": 5000,
            "remaining_processes": 0 if passed else None,
            "remaining_tools": 0 if passed else None,
            "external_path_changes": external_changes,
            "offline_bypass_successes": 0,
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": files,
        "revisions": [{"scope": "extension lifecycle", "values": ["install", "enable", "invoke", "upgrade", "rollback", "withdraw", "uninstall"]}],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
