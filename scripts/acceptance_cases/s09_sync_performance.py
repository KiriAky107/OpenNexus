"""S-09 sustained load, bounded upload RSS, and initial-sync acceptance."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from threading import Event, Thread
import time

from sync_production_stack import ROOT, SERVICE, SyncProductionStack, call, sha256

LOAD_SECONDS = 30 * 60
LOAD_RATE = 20
LOAD_CLIENTS = 10
INITIAL_NOTES = 10_000
NOTE_BYTES = 4 * 1024
CHUNK = 1024 * 1024
UPLOAD_BYTES = 100 * CHUNK
RSS_LIMIT = 2 * 1024**3
NETWORK_RTT_SECONDS = 0.020


def percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * proportion) - 1)]


def stable_id(kind: str, index: int, length: int = 32) -> str:
    return hashlib.sha256(f"OpenNexus:S-09:{kind}:{index}".encode()).hexdigest()[:length]


def note_data(index: int) -> bytes:
    line = f"# OpenNexus S-09 note {index:05d}\nseed=20260908\n".encode()
    return (line * (NOTE_BYTES // len(line) + 1))[:NOTE_BYTES]


def transfer_block(index: int, offset: int, length: int = CHUNK) -> bytes:
    seed = hashlib.sha256(f"OpenNexus:S-09:upload:{index}:{offset}".encode()).digest()
    return (seed * (length // len(seed) + 1))[:length]


def transfer_hash(index: int) -> str:
    digest = hashlib.sha256()
    for offset in range(0, UPLOAD_BYTES, CHUNK):
        digest.update(transfer_block(index, offset))
    return digest.hexdigest()


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t)
        for name in (
            "PeakWorkingSetSize",
            "WorkingSetSize",
            "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage",
            "PagefileUsage",
            "PeakPagefileUsage",
            "PrivateUsage",
        )
    ]


class ProcessTreeMemory:
    def __init__(self, root_pid: int):
        if os.name != "nt":
            raise RuntimeError("S09_WINDOWS_MEMORY_COLLECTOR_REQUIRED")
        self.root_pid = root_pid
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        self.kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(MemoryCounters),
            wintypes.DWORD,
        ]

    def process_ids(self) -> set[int]:
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise OSError("PROCESS_SNAPSHOT_FAILED")
        parents = {}
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            current = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while current:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                current = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        selected = {self.root_pid}
        changed = True
        while changed:
            before = len(selected)
            selected.update(pid for pid, parent in parents.items() if parent in selected)
            changed = len(selected) != before
        return selected

    def working_set(self) -> tuple[int, int]:
        total = observed = 0
        for pid in self.process_ids():
            handle = self.kernel.OpenProcess(0x1000 | 0x0010, False, pid)
            if not handle:
                continue
            try:
                counters = MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if self.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    total += int(counters.WorkingSetSize)
                    observed += 1
            finally:
                self.kernel.CloseHandle(handle)
        return total, observed


def start_memory_sampler(root_pid: int):
    stopping = Event()
    state = {"peak": 0, "samples": 0, "errors": 0, "processes": 0}
    collector = ProcessTreeMemory(root_pid)

    def sample():
        while not stopping.is_set():
            try:
                value, processes = collector.working_set()
                state["peak"] = max(state["peak"], value)
                state["processes"] = max(state["processes"], processes)
                state["samples"] += 1
            except OSError:
                state["errors"] += 1
            time.sleep(0.01)

    thread = Thread(target=sample, daemon=True)
    thread.start()
    return stopping, thread, state


def upload_small(base: str, auth: dict, data: bytes) -> str:
    content_hash = hashlib.sha256(data).hexdigest()
    created = call(
        "POST", base + "/uploads", headers=auth, body={"content_hash": content_hash, "size": len(data)}
    )
    assert created[0] == 200
    if not created[1]["complete"]:
        path = base + "/uploads/" + created[1]["upload_id"]
        assert call("PUT", path + "?offset=0", headers=auth, body=data)[0] == 200
        assert call("POST", path + "/complete", headers=auth)[0] == 200
    return content_hash


def create_client_vault(stack: SyncProductionStack, username: str, password: str, label: str):
    session = stack.login(username, password, label)
    auth = {"Authorization": "Bearer " + session["access_token"]}
    _, base, vault_id = stack.create_vault_for_auth(auth, label)
    return {"username": username, "password": password, "auth": auth, "base": base, "vault": vault_id}


def run_memory_uploads(stack: SyncProductionStack, clients: list[dict]) -> dict:
    from threading import Barrier
    from urllib.request import Request, urlopen

    selected = clients[:4]
    hashes = [transfer_hash(index) for index in range(4)]
    gate = Barrier(5)

    def transfer(index: int):
        client = selected[index]
        created = call(
            "POST",
            client["base"] + "/uploads",
            headers=client["auth"],
            body={"content_hash": hashes[index], "size": UPLOAD_BYTES},
        )
        assert created[0] == 200 and created[1]["complete"] is False
        upload = client["base"] + "/uploads/" + created[1]["upload_id"]
        gate.wait(timeout=60)
        for offset in range(0, UPLOAD_BYTES, CHUNK):
            started = time.monotonic()
            time.sleep(NETWORK_RTT_SECONDS / 2)
            response = call(
                "PUT",
                upload + f"?offset={offset}",
                headers=client["auth"],
                body=transfer_block(index, offset),
            )
            time.sleep(NETWORK_RTT_SECONDS / 2)
            assert response[0] == 200 and response[1]["offset"] == offset + CHUNK
            # Four equal streams share an aggregate 100 Mbps client-side ceiling.
            delay = (CHUNK * 8 * 4 / 100_000_000) - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
        assert call("POST", upload + "/complete", headers=client["auth"])[0] == 200
        body = {
            "operation_id": stable_id("memory-operation", index),
            "file_id": stable_id("memory-file", index),
            "base_revision": 0,
            "path": f"memory/{index}.bin",
            "operation": "put",
            "content_hash": hashes[index],
            "size": UPLOAD_BYTES,
        }
        assert call("POST", client["base"] + "/revisions", headers=client["auth"], body=body)[0] == 200
        return index

    stopping, monitor, memory = start_memory_sampler(stack.sync.pid)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(transfer, index) for index in range(4)]
            gate.wait(timeout=60)
            completed = [future.result(timeout=300) for future in futures]
    finally:
        stopping.set()
        monitor.join(timeout=10)

    verified = 0
    for index, client in enumerate(selected):
        request = Request(
            client["base"] + "/objects/" + hashes[index],
            headers=client["auth"],
        )
        digest = hashlib.sha256()
        size = 0
        with urlopen(request, timeout=120) as response:
            for chunk in iter(lambda: response.read(CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
        verified += int(size == UPLOAD_BYTES and digest.hexdigest() == hashes[index])
    return {"completed": len(completed), "verified": verified, **memory}


async def initial_sync_worker() -> int:
    import httpx

    origin = os.environ["S09_ORIGIN"]
    base = os.environ["S09_INITIAL_BASE"]
    token = os.environ["S09_ACCESS_TOKEN"]
    note_count = int(os.environ["S09_NOTE_COUNT"])
    headers = {"Authorization": "Bearer " + token}
    timeout = httpx.Timeout(120, connect=10)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    semaphore = asyncio.Semaphore(32)
    started = time.monotonic()

    async with httpx.AsyncClient(
        base_url=origin,
        headers=headers,
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        async def request(method: str, path: str, **kwargs):
            await asyncio.sleep(NETWORK_RTT_SECONDS / 2)
            response = await client.request(method, path, **kwargs)
            await asyncio.sleep(NETWORK_RTT_SECONDS / 2)
            if response.status_code != 200:
                raise RuntimeError("S09_INITIAL_HTTP_ERROR")
            return response

        async def put_note(index: int):
            async with semaphore:
                data = note_data(index)
                content_hash = hashlib.sha256(data).hexdigest()
                created = (
                    await request(
                        "POST",
                        base + "/uploads",
                        json={"content_hash": content_hash, "size": len(data)},
                    )
                ).json()
                if not created["complete"]:
                    upload = base + "/uploads/" + created["upload_id"]
                    await request("PUT", upload, params={"offset": 0}, content=data)
                    await request("POST", upload + "/complete")
                revision = {
                    "operation_id": stable_id("initial-operation", index),
                    "file_id": stable_id("initial-file", index),
                    "base_revision": 0,
                    "path": f"notes/{index:05d}.md",
                    "operation": "put",
                    "content_hash": content_hash,
                    "size": len(data),
                }
                await request("POST", base + "/revisions", json=revision)
                return content_hash

        async def put_attachment():
            index = 99
            content_hash = transfer_hash(index)
            created = (
                await request(
                    "POST",
                    base + "/uploads",
                    json={"content_hash": content_hash, "size": UPLOAD_BYTES},
                )
            ).json()
            upload = base + "/uploads/" + created["upload_id"]
            for offset in range(0, UPLOAD_BYTES, CHUNK):
                chunk_started = time.monotonic()
                await request(
                    "PUT", upload, params={"offset": offset}, content=transfer_block(index, offset)
                )
                delay = (CHUNK * 8 / 100_000_000) - (time.monotonic() - chunk_started)
                if delay > 0:
                    await asyncio.sleep(delay)
            await request("POST", upload + "/complete")
            await request(
                "POST",
                base + "/revisions",
                json={
                    "operation_id": stable_id("initial-attachment-operation", 0),
                    "file_id": stable_id("initial-attachment-file", 0),
                    "base_revision": 0,
                    "path": "attachments/initial.bin",
                    "operation": "put",
                    "content_hash": content_hash,
                    "size": UPLOAD_BYTES,
                },
            )
            return content_hash

        hashes, attachment_hash = await asyncio.gather(
            asyncio.gather(*(put_note(index) for index in range(note_count))),
            put_attachment(),
        )

        items = []
        cursor = 0
        boundary = None
        while boundary is None or cursor < boundary:
            params = {"cursor": cursor, "limit": 500}
            if boundary is not None:
                params["boundary"] = boundary
            page = (await request("GET", base + "/changes", params=params)).json()
            boundary = page["boundary"]
            items.extend(page["items"])
            if not page["items"]:
                break
            cursor = page["items"][-1]["sequence"]
        if len(items) != note_count + 1:
            raise RuntimeError("S09_INITIAL_REVISION_COUNT")

        async def verify_note(index: int):
            async with semaphore:
                response = await request("GET", base + "/objects/" + hashes[index])
                return hashlib.sha256(response.content).hexdigest() == hashes[index]

        verified = sum(
            await asyncio.gather(*(verify_note(index) for index in range(note_count)))
        )
        digest = hashlib.sha256()
        size = 0
        async with client.stream("GET", base + "/objects/" + attachment_hash) as response:
            if response.status_code != 200:
                raise RuntimeError("S09_INITIAL_ATTACHMENT_HTTP")
            async for chunk in response.aiter_bytes(CHUNK):
                digest.update(chunk)
                size += len(chunk)
        if size != UPLOAD_BYTES or digest.hexdigest() != attachment_hash:
            raise RuntimeError("S09_INITIAL_ATTACHMENT_DIGEST")
    print(
        json.dumps(
            {
                "notes": note_count,
                "attachment_bytes": UPLOAD_BYTES,
                "verified_files": verified + 1,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "first_note_hash": hashes[0],
                "boundary": note_count + 1,
            }
        )
    )
    return 0


def validate_load_worker() -> int:
    sys.path.insert(0, str(SERVICE))
    from sqlalchemy import text

    from sync_server.database import Database

    db = Database(os.environ["SYNC_DATABASE_URL"])
    clients = json.loads(os.environ["S09_LOAD_CATALOG"])
    validated = 0
    with db.engine.connect() as conn:
        for index, client in enumerate(clients):
            rows = list(conn.execute(
                text(
                    "SELECT sequence,file_id,path,hash,size,operation_id FROM revisions "
                    "WHERE vault_id=:vault ORDER BY sequence"
                ),
                {"vault": client["vault"]},
            ).mappings())
            if [row["sequence"] for row in rows] != list(range(1, len(rows) + 1)):
                raise RuntimeError("S09_LOAD_SEQUENCE_GAP")
            observed_rounds = set()
            for row in rows:
                try:
                    round_index = int(row["path"].removeprefix("load/").removesuffix(".md"))
                except ValueError as error:
                    raise RuntimeError("S09_LOAD_PATH_INVALID") from error
                if dict(row) != {
                    "sequence": row["sequence"],
                    "file_id": stable_id(f"load-file-{index}", round_index),
                    "path": f"load/{round_index:06d}.md",
                    "hash": client["hash"],
                    "size": NOTE_BYTES,
                    "operation_id": stable_id(f"load-operation-{index}", round_index),
                }:
                    raise RuntimeError("S09_LOAD_DATA_MISMATCH")
                if round_index in observed_rounds:
                    raise RuntimeError("S09_LOAD_DUPLICATE")
                observed_rounds.add(round_index)
                validated += 1
            if observed_rounds != set(range(len(rows))):
                raise RuntimeError("S09_LOAD_ROUND_GAP")
    print(json.dumps({"validated": validated}))
    return 0


def worker_entry() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=["initial", "validate-load"], required=True)
    args = parser.parse_args()
    if args.worker == "initial":
        return asyncio.run(initial_sync_worker())
    return validate_load_worker()


def run_worker(stack: SyncProductionStack, worker: str, **environment: str) -> dict:
    child_env = dict(stack.service_env)
    child_env.update(environment)
    completed = subprocess.run(
        [str(stack.server_python), str(Path(__file__).resolve()), "--worker", worker],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=1200,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def run_initial_sync(stack: SyncProductionStack, client: dict, note_count: int) -> dict:
    return run_worker(
        stack,
        "initial",
        S09_ORIGIN=stack.origin,
        S09_INITIAL_BASE=client["base"].removeprefix(stack.origin),
        S09_ACCESS_TOKEN=client["auth"]["Authorization"].removeprefix("Bearer "),
        S09_NOTE_COUNT=str(note_count),
    )


def run_convergence(client: dict, content_hash: str, cursor: int) -> dict:
    latencies = []
    for index in range(100):
        started = time.monotonic()
        body = {
            "operation_id": stable_id("convergence-operation", index),
            "file_id": stable_id("convergence-file", index),
            "base_revision": 0,
            "path": f"incremental/{index:03d}.md",
            "operation": "put",
            "content_hash": content_hash,
            "size": NOTE_BYTES,
        }
        committed = call("POST", client["base"] + "/revisions", headers=client["auth"], body=body)
        assert committed[0] == 200
        sequence = committed[1]["sequence"]
        deadline = started + 10
        while True:
            changes = call(
                "GET", client["base"] + f"/changes?cursor={cursor}", headers=client["auth"]
            )
            assert changes[0] == 200
            if any(item["sequence"] == sequence for item in changes[1]["items"]):
                cursor = sequence
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("S09_CONVERGENCE_TIMEOUT")
            time.sleep(0.05)
        latencies.append((time.monotonic() - started) * 1000)
    return {"samples": len(latencies), "p95_ms": percentile(latencies, 0.95)}


def run_sustained_load(stack: SyncProductionStack, clients: list[dict], duration: int) -> dict:
    total = duration * LOAD_RATE
    started = time.monotonic()
    starts = []

    def commit(global_index: int):
        client_index = global_index % LOAD_CLIENTS
        round_index = global_index // LOAD_CLIENTS
        client = clients[client_index]
        begin = time.monotonic()
        time.sleep(NETWORK_RTT_SECONDS / 2)
        response = call(
            "POST",
            client["base"] + "/revisions",
            headers=client["auth"],
            body={
                "operation_id": stable_id(f"load-operation-{client_index}", round_index),
                "file_id": stable_id(f"load-file-{client_index}", round_index),
                "base_revision": 0,
                "path": f"load/{round_index:06d}.md",
                "operation": "put",
                "content_hash": client["hash"],
                "size": NOTE_BYTES,
            },
        )
        time.sleep(NETWORK_RTT_SECONDS / 2)
        return response[0], (time.monotonic() - begin) * 1000

    def rotate_sessions():
        for index, client in enumerate(clients):
            session = stack.login(
                client["username"], client["password"], f"S-09 load rotate {index}"
            )
            client["auth"] = {"Authorization": "Bearer " + session["access_token"]}

    refreshes = []
    futures = []
    with ThreadPoolExecutor(max_workers=48) as pool, ThreadPoolExecutor(max_workers=1) as manager:
        next_refresh = 600
        for global_index in range(total):
            target = started + global_index / LOAD_RATE
            while True:
                now = time.monotonic()
                if now >= target:
                    break
                time.sleep(min(0.01, target - now))
            starts.append(time.monotonic())
            futures.append(pool.submit(commit, global_index))
            elapsed = starts[-1] - started
            if elapsed >= next_refresh and len(refreshes) < 2:
                if refreshes:
                    refreshes[-1].result(timeout=60)
                refreshes.append(manager.submit(rotate_sessions))
                next_refresh += 600
        remaining = started + duration - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        results = [future.result(timeout=180) for future in futures]
        for refresh in refreshes:
            refresh.result(timeout=60)
    elapsed = time.monotonic() - started
    codes = [item[0] for item in results]
    latencies = [item[1] for item in results]
    return {
        "duration_seconds": elapsed,
        "requests": len(results),
        "successes": codes.count(200),
        "unexpected_5xx": sum(500 <= code < 600 for code in codes),
        "other_errors": sum(code != 200 and not 500 <= code < 600 for code in codes),
        "p95_ms": percentile(latencies, 0.95),
        "rate_per_second": len(starts) / ((starts[-1] - starts[0]) + 1 / LOAD_RATE),
        "max_schedule_lag_ms": max(
            (actual - (started + index / LOAD_RATE)) * 1000
            for index, actual in enumerate(starts)
        ),
        "refreshes": len(refreshes),
    }


def result(case_id: str, status: str, reason: str, facts: dict) -> dict:
    passed = status == "PASSED"
    assertions = [
        (
            "the oracle uses real PostgreSQL, MinIO, two workers, and ten clients",
            facts.get("dependencies") is True
            and facts.get("worker_count") == 2
            and facts.get("client_count") == LOAD_CLIENTS,
        ),
        (
            "ten clients sustain 20 4 KiB commits per second for 30 minutes",
            facts.get("load_duration_seconds", 0) >= LOAD_SECONDS
            and facts.get("load_requests") == LOAD_SECONDS * LOAD_RATE
            and facts.get("load_successes") == LOAD_SECONDS * LOAD_RATE
            and facts.get("session_rotations") == 2
            and 19.9 <= facts.get("load_rate_per_second", 0) <= 20.1,
        ),
        (
            "API p95 is at most 500 ms, unexpected 5xx is under 0.1%, and all rows validate",
            facts.get("api_p95_ms", 501) <= 500
            and facts.get("unexpected_5xx", 1) / max(1, facts.get("load_requests", 0)) < 0.001
            and facts.get("other_errors") == 0
            and facts.get("validated_commits") == LOAD_SECONDS * LOAD_RATE,
        ),
        (
            "four concurrent 100 MiB uploads verify with total service RSS at most 2 GiB",
            facts.get("upload_completed") == 4
            and facts.get("upload_verified") == 4
            and 0 < facts.get("service_peak_rss_bytes", 0) <= RSS_LIMIT
            and facts.get("rss_samples", 0) > 0,
        ),
        (
            "10000 distinct 4 KiB notes and a 100 MiB attachment initially sync within ten minutes",
            facts.get("initial_notes") == INITIAL_NOTES
            and facts.get("initial_verified_files") == INITIAL_NOTES + 1
            and facts.get("initial_sync_ms", 600_001) <= 600_000,
        ),
        (
            "100 fault-free incremental changes converge with p95 at most ten seconds",
            facts.get("convergence_samples") == 100
            and facts.get("convergence_p95_ms", 10_001) <= 10_000,
        ),
    ]
    evidence = (
        "real PostgreSQL 17, fixed MinIO, two production Uvicorn workers, "
        "100 Mbps/20 ms client profile, and black-box HTTP"
    )
    return {
        "schema": 1,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": [
            {
                "name": name,
                "status": "PASSED" if passed and actual else "FAILED",
                "evidence": evidence,
            }
            for name, actual in assertions
        ],
        "metrics": {
            "load_requests": facts.get("load_requests", 0),
            "api_p95_ms": facts.get("api_p95_ms", 0),
            "unexpected_5xx_rate": facts.get("unexpected_5xx", 0)
            / max(1, facts.get("load_requests", 0)),
            "validated_commits": facts.get("validated_commits", 0),
            "service_peak_rss_bytes": facts.get("service_peak_rss_bytes", 0),
            "upload_verified": facts.get("upload_verified", 0),
            "initial_sync_ms": facts.get("initial_sync_ms", 0),
            "initial_verified_files": facts.get("initial_verified_files", 0),
            "convergence_p95_ms": facts.get("convergence_p95_ms", 0),
            "worker_count": facts.get("worker_count", 0),
            "client_count": facts.get("client_count", 0),
        },
        "files": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in (
                "server sync/sync_server/app.py",
                "server sync/sync_server/database.py",
                "server sync/sync_server/storage.py",
                "scripts/acceptance_cases/sync_production_stack.py",
                "scripts/acceptance_cases/s09_sync_performance.py",
            )
        ],
        "revisions": [
            {
                "scope": "runtime",
                "postgres": facts.get("postgres_version"),
                "minio": facts.get("minio_version"),
                "os": platform.platform(),
                "logical_processors": os.cpu_count(),
            },
            {
                "scope": "network profile",
                "aggregate_bits_per_second": 100_000_000,
                "round_trip_ms": 20,
                "method": "client-side pacing over TCP loopback",
            },
            {
                "scope": "load",
                "duration_seconds": facts.get("load_duration_seconds", 0),
                "rate_per_second": facts.get("load_rate_per_second", 0),
                "max_schedule_lag_ms": facts.get("max_schedule_lag_ms", 0),
                "session_rotations": facts.get("session_rotations", 0),
            },
            {
                "scope": "memory",
                "samples": facts.get("rss_samples", 0),
                "read_errors": facts.get("rss_errors", 0),
                "max_processes": facts.get("rss_processes", 0),
            },
            {
                "scope": "last checkpoint",
                "stage": facts.get("active_stage"),
                "iteration": facts.get("active_iteration", -1),
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration-seconds", type=int, default=LOAD_SECONDS)
    parser.add_argument("--note-count", type=int, default=INITIAL_NOTES)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    case_id = os.environ.get("OPENNEXUS_ACCEPTANCE_CASE_ID", "")
    facts: dict = {}
    reason = ""
    status = "FAILED"
    stack = None
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        data_root = Path(os.environ["OPENNEXUS_ACCEPTANCE_DATA_ROOT"]).resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        stack = SyncProductionStack(config, data_root, "s09").start()
        facts.update(
            {
                "dependencies": True,
                "postgres_version": stack.postgres_version,
                "minio_version": stack.minio_version,
                "worker_count": len(stack.worker_ids()),
            }
        )
        assert facts["worker_count"] == 2
        second_username, second_password = stack.add_user("s09-second")

        facts["active_stage"] = "memory-uploads"
        memory_clients = [
            create_client_vault(
                stack,
                stack.username if index < 2 else second_username,
                stack.password if index < 2 else second_password,
                f"S-09 memory {index}",
            )
            for index in range(4)
        ]
        memory = run_memory_uploads(stack, memory_clients)
        facts.update(
            {
                "upload_completed": memory["completed"],
                "upload_verified": memory["verified"],
                "service_peak_rss_bytes": memory["peak"],
                "rss_samples": memory["samples"],
                "rss_errors": memory["errors"],
                "rss_processes": memory["processes"],
            }
        )
        assert memory["completed"] == 4 and memory["verified"] == 4
        assert 0 < memory["peak"] <= RSS_LIMIT and memory["samples"] > 0

        facts["active_stage"] = "initial-sync"
        initial_client = create_client_vault(
            stack, stack.username, stack.password, "S-09 initial sync"
        )
        initial = run_initial_sync(stack, initial_client, args.note_count)
        facts.update(
            {
                "initial_notes": initial["notes"],
                "initial_verified_files": initial["verified_files"],
                "initial_sync_ms": initial["elapsed_ms"],
            }
        )
        assert initial["verified_files"] == args.note_count + 1
        assert initial["elapsed_ms"] <= 600_000

        facts["active_stage"] = "incremental-convergence"
        convergence = run_convergence(
            initial_client, initial["first_note_hash"], initial["boundary"]
        )
        facts["convergence_samples"] = convergence["samples"]
        facts["convergence_p95_ms"] = convergence["p95_ms"]
        assert convergence["samples"] == 100 and convergence["p95_ms"] <= 10_000

        facts["active_stage"] = "sustained-load"
        load_clients = []
        for index in range(LOAD_CLIENTS):
            username, password = (
                (stack.username, stack.password)
                if index < LOAD_CLIENTS // 2
                else (second_username, second_password)
            )
            client = create_client_vault(stack, username, password, f"S-09 load {index}")
            client["hash"] = upload_small(
                client["base"], client["auth"], note_data(20_000 + index)
            )
            load_clients.append(client)
        facts["client_count"] = len(load_clients)
        load = run_sustained_load(stack, load_clients, args.duration_seconds)
        facts.update(
            {
                "load_duration_seconds": load["duration_seconds"],
                "load_requests": load["requests"],
                "load_successes": load["successes"],
                "unexpected_5xx": load["unexpected_5xx"],
                "other_errors": load["other_errors"],
                "api_p95_ms": load["p95_ms"],
                "load_rate_per_second": load["rate_per_second"],
                "max_schedule_lag_ms": load["max_schedule_lag_ms"],
                "session_rotations": load["refreshes"],
            }
        )
        expected_requests = args.duration_seconds * LOAD_RATE
        assert load["requests"] == expected_requests and load["successes"] == expected_requests
        assert load["p95_ms"] <= 500 and load["other_errors"] == 0
        assert load["unexpected_5xx"] / max(1, expected_requests) < 0.001

        facts["active_stage"] = "load-validation"
        validated = run_worker(
            stack,
            "validate-load",
            S09_LOAD_CATALOG=json.dumps(
                [{"vault": client["vault"], "hash": client["hash"]} for client in load_clients],
                separators=(",", ":"),
            ),
        )
        facts["validated_commits"] = validated["validated"]
        facts.update({"active_stage": "complete", "active_iteration": expected_requests})
        assert validated["validated"] == expected_requests
        stack.assert_running()
        if args.duration_seconds == LOAD_SECONDS and args.note_count == INITIAL_NOTES:
            status = "PASSED"
        else:
            reason = "DEVELOPMENT_PROFILE_COMPLETE"
    except BaseException as error:
        reason = "S09_ORACLE_FAILED:" + type(error).__name__
    finally:
        if stack is not None:
            stack.stop()
    payload = result(
        case_id,
        status if case_id == "S-09" else "FAILED",
        reason or ("" if case_id == "S-09" else "CASE_ID_MISMATCH"),
        facts,
    )
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASSED" else 1


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(worker_entry())
    raise SystemExit(main())
