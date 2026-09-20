"""在冻结依赖的打包环境中构建 onedir Core，并生成文件清单。

运行：uv run --directory backend --group packaging python ../scripts/build-core.py
输出保留在当前工作树中已忽略的 .build 目录内。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release",
        action="store_true",
        help="生成必须带 Ed25519 签名的生产 Core 发布包",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="保留可重建的 PyInstaller 中间目录用于诊断",
    )
    return parser.parse_args()


def sign_release(manifest: bytes, output: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_file = os.environ.get("OPENNEXUS_CORE_SIGNING_KEY_FILE", "")
    if not key_file:
        raise RuntimeError("release build requires OPENNEXUS_CORE_SIGNING_KEY_FILE")
    key_path = Path(key_file).resolve(strict=True)
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise RuntimeError("Core signing key must be an Ed25519 PEM private key")
    signature = private.sign(manifest)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    (output / "manifest.sig").write_bytes(signature)
    (output / "public-key.hex").write_text(public.hex() + "\n", encoding="ascii")


def main():
    options = arguments()
    output = ROOT / ".build" / "sidecar"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--name", "opennexus-core", "--distpath", str(output / "dist"),
        "--workpath", str(output / "work"), "--specpath", str(output),
        "--runtime-hook", str(ROOT / "scripts" / "pyinstaller-runtime-hook.py"),
        "--collect-submodules", "app", "--collect-all", "sqlite_vec", "--collect-all", "tzdata",
        # 本地模型使用独立 Python 进程执行；冻结后的 Core 必须保留可直接运行的 Worker 源文件。
        "--add-data", str(ROOT / "backend" / "app" / "local_models" / "worker.py") + ":app/local_models",
        "--add-data", str(ROOT / "backend" / "app" / "local_models" / "protocol.py") + ":app/local_models",
        # 设置页可以在可写的数据目录中安装模型运行环境，安装器及其锁文件需随 Core 发布。
        "--add-data", str(ROOT / "backend" / "scripts" / "install-model-runtime.ps1") + ":scripts",
        "--add-data", str(ROOT / "backend" / "scripts" / "model-requirements.lock") + ":scripts",
        "--add-data", str(ROOT / "backend" / "scripts" / "model-requirements.txt") + ":scripts",
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
    cargo = tomllib.loads((ROOT / "frontend" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    version = cargo["package"]["version"]
    manifest = {
        "protocol": 1,
        "product": "OpenNexus",
        "host_version": version,
        "core_version": version,
        "files": files,
        "lock_sha256": hashlib.sha256((ROOT / "backend" / "uv.lock").read_bytes()).hexdigest(),
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (output / "manifest.json").write_bytes(encoded)
    if options.release:
        sign_release(encoded, output)
    else:
        for stale in (output / "manifest.sig", output / "public-key.hex"):
            stale.unlink(missing_ok=True)
    if not options.keep_work:
        shutil.rmtree(output / "work", ignore_errors=True)
    print(f"Core built: {len(files)} files; manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    main()
