"""Windows 组件测量，不是 S-09 服务/发布基准。构建当前的 Rust 库测试可执行文件，然后在其自己的进程中运行每个固定工作负载。报告观察到OS累积峰值，具有采样覆盖率。无需第三方 Python 软件包。用法： python 脚本/measure-sync-memory.py"""
from __future__ import annotations
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = {
    "stream_verify": "payloads::tests::hundred_mib_verification_is_bounded_and_rejects_growth_or_truncation",
    "write_recovery": "workspace::tests::hundred_mib_spooled_writes_and_journal_recovery_keep_receipts_exactly_once",
    "rename_delete": "workspace::tests::hundred_mib_file_operations_recover_each_copy_stage_without_duplicate_events",
    "conflicts": "sync_resolution::tests::hundred_mib_conflicts_resolve_all_choices_and_reopen_without_duplicate_jobs",
    "discovery_rebind": "sync_initial::tests::hundred_mib_discovery_preview_and_rebinding_preserve_all_current_files",
}

class Counters(c.Structure):
    _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
        (name, c.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

class Monitor:
    def __init__(self):
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.psapi = c.WinDLL("psapi", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
        self.psapi.GetProcessMemoryInfo.restype = w.BOOL

    def run(self, command, log: Path, timeout=120):
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA"}}
        env["PYTHONUTF8"] = "1"
        start = time.perf_counter()
        samples = errors = 0
        first = last = None
        peaks = {"working_set_bytes": 0, "commit_bytes": 0, "sampled_private_bytes": 0}
        with log.open("wb") as output:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
            handle = self.kernel.OpenProcess(0x1000 | 0x0010, False, process.pid)
            try:
                if not handle:
                    raise OSError("MEMORY_PROCESS_OPEN_FAILED")
                while process.poll() is None:
                    if time.perf_counter() - start > timeout:
                        raise TimeoutError("MEMORY_WORKLOAD_TIMEOUT")
                    counters = Counters()
                    counters.cb = c.sizeof(counters)
                    if self.psapi.GetProcessMemoryInfo(handle, c.byref(counters), counters.cb):
                        samples += 1
                        last = time.perf_counter() - start
                        if first is None:
                            first = last
                        peaks["working_set_bytes"] = max(peaks["working_set_bytes"], counters.PeakWorkingSetSize)
                        peaks["commit_bytes"] = max(peaks["commit_bytes"], counters.PeakPagefileUsage)
                        peaks["sampled_private_bytes"] = max(peaks["sampled_private_bytes"], counters.PrivateUsage)
                    else:
                        errors += 1
                    time.sleep(0.01)
                elapsed = time.perf_counter() - start
                return dict(exit_code=process.returncode, elapsed_seconds=elapsed,
                    samples=samples, read_errors=errors, first_sample_seconds=first,
                    last_sample_seconds=last, unobserved_tail_seconds=None if last is None else elapsed-last,
                    peaks=peaks, log=log.name)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if handle:
                    self.kernel.CloseHandle(handle)

def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()

def build(directory):
    command = ["cargo", "test", "--manifest-path", "frontend/src-tauri/Cargo.toml",
        "--features", "desktop", "--lib", "--no-run", "--message-format=json"]
    with (directory / "build.log").open("wb") as error:
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=error, check=True)
    executables = []
    for line in result.stdout.decode("utf-8").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("reason") == "compiler-artifact" and message.get("profile", {}).get("test") and message.get("target", {}).get("name") == "notesagent_host" and message.get("executable"):
            executables.append(Path(message["executable"]))
    if len(executables) != 1:
        raise RuntimeError("MEMORY_TEST_EXECUTABLE_AMBIGUOUS")
    return executables[0]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / ".build/sync-memory")
    args = parser.parse_args()
    if os.name != "nt" or not 1 <= args.runs <= 10:
        parser.error("Windows required; runs must be 1..10")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(schema=1, created_at=datetime.now(timezone.utc).isoformat(),
        scope="Single Rust component test process; excludes WebView, Core, service and system file cache",
        acceptance="NOT_ASSESSED", polling_interval_seconds=0.01, runs=[],
        limitations=["OS cumulative peaks observed before exit; final sampling gap is reported",
            "Patterns and local hardware differ from the complete release benchmark",
            "No network/load/RSS acceptance threshold is inferred from these measurements"])
    try:
        exe = build(directory)
        source = hashlib.sha256()
        paths = sorted((ROOT / "frontend/src-tauri/src").rglob("*.rs")) + sorted((ROOT / "frontend/src-tauri/tests").rglob("*.rs"))
        for path in paths:
            source.update(str(path.relative_to(ROOT)).replace(chr(92), "/").encode())
            source.update(bytes.fromhex(digest(path)))
        report["build"] = dict(executable=exe.name, executable_sha256=digest(exe),
            sources_sha256=source.hexdigest(), cargo_lock_sha256=digest(ROOT / "frontend/src-tauri/Cargo.lock"),
            git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            rustc=subprocess.check_output(["rustc", "--version"], text=True).strip())
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        report["machine"] = dict(os=platform.platform(), cpu=cpu, logical_processors=os.cpu_count(),
            python=platform.python_version(), disk="not measured", network="not exercised")
        monitor = Monitor()
        calibration = monitor.run([sys.executable, "-c",
            "import time; data=bytearray(64*1024*1024); time.sleep(0.3)"], directory / "calibration.log")
        report["calibration"] = calibration
        if calibration["exit_code"] != 0 or calibration["peaks"]["working_set_bytes"] < 64*1024*1024 or calibration["peaks"]["commit_bytes"] < 64*1024*1024:
            raise RuntimeError("MEMORY_CALIBRATION_FAILED")
        for name, test in WORKLOADS.items():
            for iteration in range(1, args.runs + 1):
                log = directory / f"{name}-{iteration}.log"
                value = monitor.run([str(exe), "--exact", test, "--nocapture", "--test-threads=1"], log)
                value.update(workload=name, test=test, iteration=iteration)
                output = log.read_text(encoding="utf-8", errors="replace")
                value["passed"] = value["exit_code"] == 0 and value["samples"] > 0 and bool(re.search(r"test result: ok\. 1 passed; 0 failed; 0 ignored;", output))
                report["runs"].append(value)
                print(json.dumps(dict(workload=name, iteration=iteration, passed=value["passed"], peaks=value["peaks"])), flush=True)
                if not value["passed"]:
                    raise RuntimeError("MEMORY_WORKLOAD_FAILED")
        report["summary"] = {name: {
            metric: dict(max=max(values), median=statistics.median(values), min=min(values))
            for metric in ("working_set_bytes", "commit_bytes")
            for values in [[item["peaks"][metric] for item in report["runs"] if item["workload"] == name]]
        } for name in WORKLOADS}
        report["measurement_completed"] = True
    finally:
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

if __name__ == "__main__":
    main()
