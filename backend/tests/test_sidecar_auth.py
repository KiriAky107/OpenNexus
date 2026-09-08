import asyncio
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from app.sidecar import SessionAuth, bootstrap, proof


def test_bootstrap_is_bounded_and_requires_session_entropy(tmp_path):
    data = dict(protocol=1, secret="01" * 32, challenge="02" * 32,
                generation="03" * 32, data_dir=str(tmp_path), launcher_pid=123)
    assert bootstrap(json.dumps(data).encode() + b"\n") == data
    for invalid in [b"{}\n", b"x" * 16385, b"{}", b"null\n"]:
        with pytest.raises(ValueError):
            bootstrap(invalid)
    data["secret"] = "short"
    with pytest.raises(ValueError):
        bootstrap(json.dumps(data).encode() + b"\n")


def test_session_auth_covers_every_route_and_rejects_duplicate_headers():
    calls = []
    async def app(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
    auth = SessionAuth(app, "ab" * 32, "cd" * 32, 4567)
    valid = [(b"host", b"127.0.0.1:4567"),
             (b"authorization", ("Bearer " + "ab" * 32).encode()),
             (b"x-core-generation", ("cd" * 32).encode())]
    async def request(headers, path):
        messages = []
        async def send(message):
            messages.append(message)
        await auth({"type": "http", "headers": headers, "path": path}, None, send)
        return messages[0]["status"]
    for path in ["/health", "/api/status", "/api/events", "/api/export/file", "/docs", "/unknown"]:
        for bad in [[], valid[:2], valid + [valid[1]],
                    valid + [(b"origin", b"tauri://localhost")],
                    [(b"host", b"evil.test")] + valid[1:],
                    valid[:2] + [(b"x-core-generation", b"old")]]:
            assert asyncio.run(request(bad, path)) == 401
        assert asyncio.run(request(valid, path)) == 204
    assert len(calls) == 6


def test_handshake_proof_binds_port_pid_generation_and_challenge():
    args = ["01" * 32, "02" * 32, "03" * 32, 123, 4567, 123]
    expected = proof(*args)
    assert len(expected) == 64
    for i in range(1, len(args)):
        changed = args.copy()
        changed[i] = "04" * 32 if isinstance(args[i], str) else args[i] + 1
        assert proof(*changed) != expected


def test_real_sidecar_bootstrap_auth_and_parent_eof(tmp_path):
    config = dict(protocol=1, secret="01" * 32, challenge="02" * 32,
                  generation="03" * 32, data_dir=str(tmp_path / "core"))
    executable = os.environ.get("OPENNEXUS_CORE_TEST_BINARY")
    command = [executable] if executable else [sys.executable, "-m", "app.sidecar"]
    diagnostics = (tmp_path / "core-stderr.log").open("wb")
    process = subprocess.Popen(command,
                               cwd=Path(__file__).resolve().parents[1],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=diagnostics,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        config["launcher_pid"] = process.pid
        process.stdin.write(json.dumps(config).encode() + b"\n")
        process.stdin.flush()
        received = queue.Queue()
        threading.Thread(target=lambda: received.put(process.stdout.readline(16385)), daemon=True).start()
        line = received.get(timeout=30)
        assert line, (tmp_path / "core-stderr.log").read_text(encoding="utf-8", errors="replace")[-4000:]
        ready = json.loads(line)
        assert ready["launcher_pid"] == process.pid
        assert ready["pid"] > 0
        assert ready["proof"] == proof(config["secret"], config["challenge"], config["generation"],
                                        ready["pid"], ready["port"], process.pid)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f'http://127.0.0.1:{ready["port"]}'
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(url + "/health", timeout=5)
        assert error.value.code == 401
        request = urllib.request.Request(url + "/health", headers={
            "Authorization": "Bearer " + config["secret"],
            "X-Core-Generation": config["generation"],
        })
        with opener.open(request, timeout=5) as response:
            assert json.load(response)["status"] == "ok"
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        diagnostics.close()
