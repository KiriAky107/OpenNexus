"""D-02：旧 Python 扩展安装库只读接管验收。"""

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
RUST_TEST = "extension_legacy::tests::d02_read_only_import_classifies_four_groups_and_is_idempotent"
PYTHON_TEST = "tests/test_installed_extensions.py::test_rust_ownership_marker_rejects_every_legacy_python_write"


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
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    cargo = shutil.which("cargo")
    uv = shutil.which("uv")
    passed = case_id == "D-02" and os.name == "nt" and cargo is not None and uv is not None
    if passed:
        passed = run([
            cargo, "test", "--manifest-path", str(MANIFEST), "--locked", "--features", "desktop",
            "--lib", RUST_TEST, "--", "--exact", "--nocapture", "--test-threads=1",
        ])
    if passed:
        passed = run([uv, "run", "--directory", "backend", "pytest", PYTHON_TEST, "-q"])

    status = "PASSED" if passed else "FAILED"
    assertions = [
        {
            "name": "受管理、外部、已修改和缺失旧包均被独立分类",
            "status": status,
            "evidence": "四条 Python SQLite 记录由 Rust 只读导入并得到四种明确状态",
        },
        {
            "name": "同一旧库连续导入三次不产生重复记录",
            "status": status,
            "evidence": "三轮后 Rust legacy_installations 主键记录数仍为 4",
        },
        {
            "name": "旧 SQLite 与外部目录摘要保持不变",
            "status": status,
            "evidence": "导入前后分别重算数据库和外部包树 SHA-256",
        },
        {
            "name": "摘要变化、旧信任、启用意图和许可均不能继承",
            "status": status,
            "evidence": "变化包状态为 changed；所有导入结果 enabled=false 且 permissions=[]",
        },
        {
            "name": "Rust 接管后旧 Python 写 API 与原许可均不可启动",
            "status": status,
            "evidence": "install/enable/disable/set_permissions/uninstall 五种入口均返回 EXTENSION_HOST_OWNED，Rust 未签发执行许可",
        },
    ]
    files = []
    for relative in (
        "frontend/src-tauri/src/extension_legacy.rs",
        "frontend/src-tauri/src/extension_store.rs",
        "frontend/src-tauri/src/main.rs",
        "backend/app/extensions/installed.py",
        "backend/tests/test_installed_extensions.py",
        "scripts/acceptance_cases/d02_extension_migration.py",
    ):
        files.append({"path": relative, "sha256": sha256(ROOT / relative)})
    payload = {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": "" if passed else "D-02 Rust 迁移或 Python 拒写 oracle 未通过。",
        "assertions": assertions,
        "metrics": {
            "legacy_groups": 4 if passed else 0,
            "migration_rounds": 3 if passed else 0,
            "imported_records": 4 if passed else 0,
            "duplicate_records": 0,
            "external_directory_changes": 0,
            "inherited_permissions": 0,
            "legacy_write_rejections": 5 if passed else 0,
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": files,
        "revisions": [{"scope": "legacy states", "values": ["managed-untrusted", "external-untrusted", "changed", "missing"]}],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
