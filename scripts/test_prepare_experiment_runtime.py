"""Negative tests for the archive/cache trust boundary, without network access."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("runtime_builder", Path(__file__).with_name("prepare-experiment-runtime.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class RuntimePreparationTests(unittest.TestCase):
    def archive(self, root, entries):
        archive = root / "runtime.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name, value in entries:
                output.writestr(name, value)
        return archive

    def test_rejects_windows_aliases_traversal_and_symlinks(self):
        symlink = zipfile.ZipInfo("python.exe")
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ["../python.exe", "C:/python.exe", "python.exe:stream", "NUL.dll",
                     "COM¹.txt", "lpt9", "python.exe.", "python.exe ", "bad\x01.py", symlink]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = self.archive(root, [(name, b"content")])
                with self.assertRaises(ValueError):
                    builder.archive_files(archive)

    def test_case_duplicate_and_expansion_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.archive(root, [("python.exe", b"a"), ("PYTHON.EXE", b"b")])
            with self.assertRaises(ValueError):
                builder.archive_files(archive)
            archive = self.archive(root, [("python.exe", b"12345")])
            with patch.object(builder, "MAX_EXPANDED_BYTES", 4), self.assertRaises(ValueError):
                builder.archive_files(archive)

    def test_changed_files_cannot_be_blessed_by_local_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.archive(root, [("python.exe", b"locked")])
            output = root / "output"
            output.mkdir()
            expected = {"files": builder.archive_files(archive, output)}
            (output / "python.exe").write_bytes(b"edited")
            (output / "runtime.json").write_text(json.dumps({"files": {"python.exe": {
                "sha256": builder.digest(output / "python.exe"), "bytes": 6}}}))
            with self.assertRaises(ValueError):
                builder.verify_tree(output, expected)

    def test_extra_file_and_hardlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.archive(root, [("python.exe", b"locked")])
            output = root / "output"
            output.mkdir()
            expected = {"files": builder.archive_files(archive, output)}
            (output / "extra.dll").write_bytes(b"injection")
            with self.assertRaises(ValueError):
                builder.verify_tree(output, expected)
            (output / "extra.dll").unlink()
            (root / "alias.exe").hardlink_to(output / "python.exe")
            with self.assertRaises(ValueError):
                builder.verify_tree(output, expected)

    def test_cached_hash_mismatch_and_insecure_origin_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "runtime.zip"
            artifact.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                builder.download("https://www.python.org/fixed", artifact, "0" * 64, 7)
            artifact.unlink()
            with self.assertRaises(ValueError):
                builder.download("http://www.python.org/fixed", artifact, "0" * 64, 7)

    def test_main_rejects_forged_cached_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / ".build/experiment-runtime"
            cache = build / "downloads"
            cache.mkdir(parents=True)
            archive = self.archive(cache, [("python.exe", b"locked"), ("LICENSE.txt", b"license"),
                                            ("python313._pth", b"python313.zip\n.\n")])
            source = "https://www.python.org/runtime.zip"
            upstream = cache / "manifest.json"
            upstream.write_text(json.dumps({"versions": [{"url": source, "sort-version": "3.13.16",
                "hash": {"sha256": builder.digest(archive)}}]}), encoding="utf-8")
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps({"runtime_id": "python-test", "version": "3.13.16",
                "source": source, "source_manifest": "https://www.python.org/manifest.json",
                "source_manifest_sha256": builder.digest(upstream), "archive_sha256": builder.digest(archive),
                "archive_bytes": archive.stat().st_size, "entrypoint": "python.exe", "path_configuration": "python313._pth"}), encoding="utf-8")
            with patch.object(builder, "ROOT", root), patch.object(builder, "BUILD", build), patch.object(builder, "LOCK", lock_path), contextlib.redirect_stdout(io.StringIO()):
                builder.main()
                runtime = build / "python-test"
                (runtime / "python.exe").write_bytes(b"edited")
                receipt_path = runtime / "runtime.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt["files"]["python.exe"]["sha256"] = builder.digest(runtime / "python.exe")
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                with self.assertRaises(ValueError):
                    builder.main()


if __name__ == "__main__":
    unittest.main()
