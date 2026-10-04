"""Prepare the locked, app-local interpreter without running downloaded code.

The Host's experiment executor supplies isolation; an embedded interpreter alone
does not provide a sandbox. Output is kept under this checkout's ignored .build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "scripts/experiment-runtime-lock.json"
BUILD = ROOT / ".build/experiment-runtime"
MAX_FILES = 128
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
RESERVED_NAMES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in "123456789¹²³"
}


def safe_leaf(name: str) -> bool:
    return (bool(name) and len(PurePosixPath(name).parts) == 1
            and not name.startswith(".") and not name.endswith((".", " "))
            and not any(char in '/\\:*?"<>|' or ord(char) < 32 for char in name)
            and name.split(".")[0].casefold() not in RESERVED_NAMES)


def regular(path: Path) -> bool:
    return (not path.is_symlink() and not path.is_junction() and path.is_file()
            and path.stat().st_nlink == 1)


def checked_directory(path: Path) -> None:
    # Reject redirected build/cache trees before creating, extracting or cleaning.
    if not path.is_relative_to(ROOT):
        raise ValueError("Runtime output must remain inside the checkout")
    for ancestor in [ROOT, *reversed(path.relative_to(ROOT).parents), path]:
        candidate = ancestor if ancestor.is_absolute() else ROOT / ancestor
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("Runtime output cannot traverse a redirected directory")
        if candidate.exists() and not candidate.is_dir():
            raise ValueError("Runtime output ancestor is not a directory")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, target: Path, expected_hash: str, expected_size: int | None) -> None:
    if target.exists():
        if not regular(target) or digest(target) != expected_hash:
            raise ValueError(f"Cached upstream artifact differs from lock: {target.name}")
        if expected_size is not None and target.stat().st_size != expected_size:
            raise ValueError("Cached upstream artifact has unexpected size")
        return
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "www.python.org":
        raise ValueError("Runtime downloads require the locked official HTTPS origin")
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as output:
        temporary = Path(output.name)
        try:
            size = 0
            with urlopen(url, timeout=60) as response:
                if (urlsplit(response.url).scheme != "https"
                        or urlsplit(response.url).hostname != "www.python.org"):
                    raise ValueError("Unexpected runtime download redirect")
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > (expected_size if expected_size is not None else 1024 * 1024):
                        raise ValueError("Upstream runtime artifact exceeds locked size")
                    output.write(chunk)
            output.flush()
            if expected_size is not None and size != expected_size:
                raise ValueError("Upstream runtime artifact is incomplete")
            if digest(temporary) != expected_hash:
                raise ValueError("Upstream runtime artifact SHA-256 differs from lock")
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(target)


def archive_files(archive: Path, destination: Path | None = None) -> dict[str, dict[str, str | int]]:
    files: dict[str, dict[str, str | int]] = {}
    names: set[str] = set()
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if not 1 <= len(entries) <= MAX_FILES:
            raise ValueError("Runtime archive has unexpected file count")
        total = 0
        for entry in entries:
            name = entry.filename
            mode = entry.external_attr >> 16
            if (not safe_leaf(name) or entry.is_dir()
                    or stat.S_ISLNK(mode) or name.casefold() in names
                    or entry.flag_bits & 1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise ValueError("Runtime archive contains an unsafe or duplicate entry")
            total += entry.file_size
            if total > MAX_EXPANDED_BYTES:
                raise ValueError("Runtime archive exceeds expansion limit")
            names.add(name.casefold())
            hashed = hashlib.sha256()
            size = 0
            with package.open(entry) as source:
                output = (destination / name).open("xb") if destination is not None else None
                try:
                    while chunk := source.read(64 * 1024):
                        size += len(chunk)
                        if size > entry.file_size:
                            raise ValueError("Runtime archive entry exceeds declared size")
                        hashed.update(chunk)
                        if output is not None:
                            output.write(chunk)
                finally:
                    if output is not None:
                        output.close()
            if size != entry.file_size:
                raise ValueError("Runtime archive entry is incomplete")
            files[name] = {"sha256": hashed.hexdigest(), "bytes": size}
    return files


def verify_tree(destination: Path, manifest: dict) -> None:
    expected = manifest["files"]
    actual = {p.name for p in destination.iterdir() if p.name != "runtime.json"}
    if actual != set(expected):
        raise ValueError("Prepared runtime file inventory differs")
    for name, receipt in expected.items():
        path = destination / name
        if not safe_leaf(name) or not regular(path) or path.stat().st_size != receipt["bytes"]:
            raise ValueError("Prepared runtime contains an unsafe or changed file")
        if digest(path) != receipt["sha256"]:
            raise ValueError("Prepared runtime file SHA-256 differs")


def main() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    checked_directory(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)
    cache = BUILD / "downloads"
    checked_directory(cache)
    cache.mkdir(exist_ok=True)
    source_manifest = cache / Path(urlsplit(lock["source_manifest"]).path).name
    download(lock["source_manifest"], source_manifest, lock["source_manifest_sha256"], None)
    upstream = json.loads(source_manifest.read_text(encoding="utf-8"))
    releases = [item for item in upstream["versions"] if item["url"] == lock["source"]]
    if (len(releases) != 1 or releases[0]["sort-version"] != lock["version"]
            or releases[0]["hash"]["sha256"] != lock["archive_sha256"]):
        raise ValueError("Runtime lock differs from the pinned upstream release manifest")
    archive = cache / Path(urlsplit(lock["source"]).path).name
    download(lock["source"], archive, lock["archive_sha256"], lock["archive_bytes"])
    destination = BUILD / lock["runtime_id"]
    if not safe_leaf(lock["runtime_id"]):
        raise ValueError("Runtime ID must be a single safe directory name")
    checked_directory(destination)
    if destination.exists():
        # A receipt beside mutable files cannot vouch for its own hashes. Rebuild
        # expected hashes from the archive already verified against the source lock.
        manifest = {"schema_version": 1, "lock": lock, "files": archive_files(archive)}
        receipt = destination / "runtime.json"
        if not regular(receipt) or json.loads(receipt.read_text(encoding="utf-8")) != manifest:
            raise ValueError("Prepared runtime receipt differs from the locked archive")
        verify_tree(destination, manifest)
    else:
        with tempfile.TemporaryDirectory(dir=BUILD, prefix="prepare-") as temporary:
            staging = Path(temporary) / "runtime"
            staging.mkdir()
            files = archive_files(archive, staging)
            if not {lock["entrypoint"], lock["path_configuration"], "LICENSE.txt"} <= set(files):
                raise ValueError("Runtime entrypoint, isolated path configuration or license is missing")
            configuration = (staging / lock["path_configuration"]).read_text(encoding="utf-8")
            active_paths = {line.strip() for line in configuration.splitlines()
                            if line.strip() and not line.lstrip().startswith("#")}
            if active_paths != {"python313.zip", "."}:
                raise ValueError("Runtime path configuration must remain isolated")
            manifest = {"schema_version": 1, "lock": lock, "files": files}
            (staging / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            verify_tree(staging, manifest)
            staging.rename(destination)
    print(json.dumps({"runtime": str(destination), "runtime_id": lock["runtime_id"],
                      "files": len(manifest["files"]),
                      "expanded_bytes": sum(item["bytes"] for item in manifest["files"].values())}))


if __name__ == "__main__":
    main()
