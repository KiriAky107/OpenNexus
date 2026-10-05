"""Synthetic native sandbox fixture; never runs against a real vault."""
import csv
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

scratch = Path(os.environ["TEMP"])


def denied(operation):
    try:
        operation()
        return {"denied": False}
    except OSError as error:
        return {"denied": True, "winerror": error.winerror, "errno": error.errno,
                "error_class": type(error).__name__, "message": str(error)}


def main():
    mode = sys.argv[1]
    if mode == "logs":
        assert sys.stdin.buffer.read(1) == b""
        sys.stdout.buffer.write("中文输出\nstdin-eof\n".encode("utf-8"))
        sys.stderr.buffer.write(b"\xff")
        for _ in range(256):
            sys.stdout.buffer.write(b"x" * 8192)
            sys.stderr.buffer.write(b"y" * 8192)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.flush()
        return
    if mode == "log-child":
        (scratch / "log-child-ready").write_text(str(os.getpid()), encoding="utf-8")
        while True:
            sys.stdout.buffer.write(b"x" * 8192)
            sys.stderr.buffer.write(b"y" * 8192)
            sys.stdout.buffer.flush()
            sys.stderr.buffer.flush()
    if mode == "log-cancel":
        subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", __file__, "log-child"],
                         stdout=sys.stdout, stderr=sys.stderr, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(120)
        return
    if mode == "child":
        (scratch / "child-ready").write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(120)
        return
    if mode == "cancel":
        subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", __file__, "child"], close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(120)
        return
    if mode == "memory":
        chunks = []
        while True:
            chunks.append(bytearray(32 * 1024 * 1024))
    if mode == "cpu":
        while True:
            pass
    if mode == "processes":
        while True:
            subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", __file__, "child"], close_fds=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    if mode == "scratch":
        with (scratch / "large.bin").open("wb") as output:
            for _ in range(18):
                output.write(b"\x00" * (16 * 1024 * 1024))
            output.flush()
        time.sleep(120)
    if mode in ("outside", "file-stream", "directory-stream"):
        target = Path("outside-large.bin")
        if mode == "file-stream":
            target.write_bytes(b"main")
            target = Path("outside-large.bin:hidden")
        elif mode == "directory-stream":
            target = Path("stream-directory")
            target.mkdir()
            target = Path("stream-directory:hidden")
        with target.open("wb") as output:
            for _ in range(3):
                output.write(b"\x00" * (8 * 1024 * 1024))
            output.flush()
        time.sleep(120)
    inputs, private, port = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
    rows = list(csv.DictReader(io.StringIO((inputs / "input.csv").read_text(encoding="utf-8"))))
    report = {
        "scratch_visible": str(scratch),
        "cwd": os.getcwd(),
        "total": sum(int(row["value"]) for row in rows),
        "names": [row["name"] for row in rows],
        "runtime": sys.version.split()[0],
        "executable": sys.executable,
        "isolated": sys.flags.isolated,
        "site_loaded": "site" in sys.modules,
        "input_write": denied(lambda: (inputs / "input.csv").write_text("changed", encoding="utf-8")),
        "outside_read": denied(lambda: private.read_bytes()),
        "parent_environment_absent": "OPENNEXUS_PROBE_PARENT_TOKEN" not in os.environ,
    }
    with socket.socket() as connection:
        connection.settimeout(2)
        report["network"] = denied(lambda: connection.connect(("127.0.0.1", port)))
    # Prove that writable container storage is broader than scratch, so the
    # production executor cannot reuse scratch-only byte accounting.
    Path("probe-outside-scratch.txt").write_text("synthetic", encoding="utf-8")
    (scratch / "probe.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


try:
    main()
except BaseException:
    (scratch / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    raise
