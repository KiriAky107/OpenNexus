"""在冻结依赖的打包环境中构建 onedir Core，并生成文件清单。

运行：uv run --directory backend --group packaging python ../scripts/build-core.py
输出保留在当前工作树中已忽略的 .build 目录内。
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / ".build" / "sidecar"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--name", "opennexus-core", "--distpath", str(output / "dist"),
        "--workpath", str(output / "work"), "--specpath", str(output),
        "--collect-submodules", "app", "--collect-all", "sqlite_vec",
        "--add-data", str(ROOT / "backend" / "extensions" / "plugins" / "text-tools") + ":extensions/plugins/text-tools",
        "--add-data", str(ROOT / "backend" / "extensions" / "plugins" / "chat-policy") + ":extensions/plugins/chat-policy",
        "--add-data", str(ROOT / "backend" / "extensions" / "skills" / "knowledge-assistant") + ":extensions/skills/knowledge-assistant",
        "--add-data", str(ROOT / "backend" / "extensions" / "skills" / "chat-operator") + ":extensions/skills/chat-operator",
        "--copy-metadata", "cryptography", "--copy-metadata", "uvicorn",
        str(ROOT / "backend" / "sidecar_entry.py"),
    ], cwd=ROOT / "backend", check=True)
    bundle = output / "dist" / "opennexus-core"
    files = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Core bundle contains a link")
        if path.is_file():
            with path.open("rb") as stream:
                files[path.relative_to(bundle).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {"protocol": 1, "product": "OpenNexus", "files": files,
                "lock_sha256": hashlib.sha256((ROOT / "backend" / "uv.lock").read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(f"Core built: {len(files)} files; manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    main()
