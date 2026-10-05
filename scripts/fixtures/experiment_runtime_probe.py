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


def storage_audit(inputs, private, expected_profile):
    import ctypes
    from ctypes import wintypes
    import winreg

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    set_acl = advapi.SetNamedSecurityInfoW
    set_acl.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_acl.restype = wintypes.DWORD
    # Only synthetic files created by this test. Never attempt to modify the
    # prepared interpreter or real user data, even for a negative test.
    report = {"input_acl_error": set_acl(str(inputs / "input.csv"), 1, 4, None, None, None, None),
              "runtime": sys.version.split()[0], "executable": sys.executable, "isolated": sys.flags.isolated,
              "private_acl_error": set_acl(str(private), 1, 4, None, None, None, None),
              "input_write": denied(lambda: (inputs / "input.csv").write_bytes(b"changed")),
              "private_read": denied(lambda: private.read_bytes())}

    location = ctypes.WinDLL("userenv", use_last_error=True).GetAppContainerRegistryLocation
    location.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    location.restype = ctypes.c_long
    root = wintypes.HANDLE()
    status = location(winreg.KEY_READ, ctypes.byref(root))
    if status != 0:
        raise ctypes.WinError(status & 0xffffffff)
    key = root.value
    try:
        query = ctypes.WinDLL("ntdll").NtQueryKey
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = ctypes.c_long
        length = wintypes.ULONG()
        query(int(key), 3, None, 0, ctypes.byref(length))
        if not 4 < length.value <= 65536:
            raise RuntimeError("Unexpected private registry name size")
        data = ctypes.create_string_buffer(length.value)
        if query(int(key), 3, data, len(data), ctypes.byref(length)) != 0:
            raise RuntimeError("Cannot verify private registry location")
        name_length = int.from_bytes(data.raw[:4], "little")
        if name_length > len(data) - 4 or name_length % 2:
            raise RuntimeError("Invalid private registry name")
        name = data.raw[4:4 + name_length].decode("utf-16-le")
        # Prove that writes target only the new profile owned by this test.
        if name.split("\\")[-1].casefold() != expected_profile.casefold():
            raise RuntimeError("Private registry does not match the owned profile")
        report["registry_profile_verified"] = True
        report["registry_dacl_before"] = registry_dacl(key, advapi)
        report["registry_write"] = denied(lambda: write_registry(key, winreg))
        report["registry_child_write"] = denied(lambda: write_existing_registry_child(key, winreg))
        report["registry_acl"] = denied(lambda: rewrite_registry_acl(key, winreg, advapi))
        report["registry_parent_write"] = denied(lambda: open_registry_parent_for_write(winreg))
    finally:
        winreg.CloseKey(key)
    return report


def registry_dacl(root, advapi):
    import ctypes
    from ctypes import wintypes
    descriptor = ctypes.c_void_p()
    get = advapi.GetSecurityInfo
    get.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.POINTER(ctypes.c_void_p)]
    get.restype = wintypes.DWORD
    status = get(root, 4, 4, None, None, None, None, ctypes.byref(descriptor))
    if status:
        raise ctypes.WinError(status)
    local_free = ctypes.WinDLL("kernel32").LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    text = wintypes.LPWSTR()
    try:
        convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
        convert.restype = wintypes.BOOL
        if not convert(descriptor, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        local_free(text)
        local_free(descriptor)


def write_registry(root, winreg):
    with winreg.CreateKeyEx(root, "OpenNexusAudit", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "synthetic", 0, winreg.REG_BINARY, b"x" * (16 * 1024 * 1024))
        assert len(winreg.QueryValueEx(key, "synthetic")[0]) == 16 * 1024 * 1024


def write_existing_registry_child(root, winreg):
    with winreg.OpenKey(root, "Children", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "synthetic", 0, winreg.REG_BINARY, b"owned audit data")


def rewrite_registry_acl(root, winreg, advapi):
    import ctypes
    from ctypes import wintypes
    with winreg.OpenKey(root, "", 0, 0x40000) as key:
        change = advapi.SetSecurityInfo
        change.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        change.restype = wintypes.DWORD
        status = change(int(key), 4, 4, None, None, None, None)
        if status:
            raise ctypes.WinError(status)


def open_registry_parent_for_write(winreg):
    path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Storage"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE | 0x40000):
        pass


def main():
    mode = sys.argv[1]
    if mode == "storage-audit":
        report = storage_audit(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[5])
        (scratch / "storage-audit.json").write_text(json.dumps(report), encoding="utf-8")
        return
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
