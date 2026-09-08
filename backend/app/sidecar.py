"""Authenticated desktop entry point. Bootstrap secrets travel only over stdin.

stdout is reserved for the bounded handshake; application output goes to stderr.
The parent keeps stdin open for the lifetime of the Core. EOF shuts it down.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading

PROTOCOL = 1
MAX_BOOTSTRAP = 16384


def bootstrap(line: bytes) -> dict:
    if len(line) > MAX_BOOTSTRAP or not line.endswith(b"\n"):
        raise ValueError("CORE_BOOTSTRAP_INVALID")
    try:
        value = json.loads(line)
        if value["protocol"] != PROTOCOL:
            raise ValueError("PROTOCOL_INCOMPATIBLE")
        for key in ("secret", "challenge", "generation"):
            if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
                raise ValueError("CORE_BOOTSTRAP_INVALID")
        if not Path(value["data_dir"]).is_absolute():
            raise ValueError("CORE_BOOTSTRAP_INVALID")
        if type(value.get("launcher_pid")) is not int or not 0 < value["launcher_pid"] <= 0xFFFFFFFF:
            raise ValueError("CORE_BOOTSTRAP_INVALID")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("CORE_BOOTSTRAP_INVALID") from exc
    return value


def proof(secret: str, challenge: str, generation: str, pid: int, port: int, launcher_pid: int) -> str:
    message = f"{PROTOCOL}:{challenge}:{generation}:{launcher_pid}:{pid}:{port}".encode("ascii")
    return hmac.new(bytes.fromhex(secret), message, hashlib.sha256).hexdigest()


class SessionAuth:
    """Outermost ASGI layer: unauthenticated input never reaches business logs."""

    def __init__(self, app, secret: str, generation: str, port: int):
        self.app = app
        self.expected = f"Bearer {secret}".encode("ascii")
        self.generation = generation.encode("ascii")
        self.host = f"127.0.0.1:{port}".encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        def single(name):
            values = [v for k, v in headers if k.lower() == name]
            return values[0] if len(values) == 1 else b""
        authorized = (
            hmac.compare_digest(single(b"authorization"), self.expected)
            and hmac.compare_digest(single(b"x-core-generation"), self.generation)
            and single(b"host") == self.host
            # Host transport does not send Origin. Browser traffic is never trusted.
            and not any(k.lower() == b"origin" for k, _ in headers)
        )
        if not authorized:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                body = b'{"error":{"code":"AUTH_REQUIRED"}}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": body})
            return
        from app import host_bridge
        vault = single(b"x-opennexus-vault").decode("ascii", errors="replace")
        token = host_bridge.vault_id.set(vault if re.fullmatch(r"[0-9a-f-]{36}", vault) else None)
        operation = single(b"x-request-id").decode("ascii", errors="replace")
        operation_token = host_bridge.operation_id.set(operation if re.fullmatch(r"[0-9a-f-]{36}", operation) else None)
        try:
            await self.app(scope, receive, send)
        finally:
            host_bridge.vault_id.reset(token)
            host_bridge.operation_id.reset(operation_token)


def main() -> int:
    channel = sys.stdin.buffer
    try:
        config = bootstrap(channel.readline(MAX_BOOTSTRAP + 1))
    except (ValueError, OSError):
        print("CORE_BOOTSTRAP_INVALID", file=sys.stderr)
        return 2
    handshake = sys.stdout
    sys.stdout = sys.stderr
    root = Path(config["data_dir"])
    # Override every data path before importing the application/container.
    os.environ.update({
        "APP_ENVIRONMENT": "desktop", "APP_DATA_DIR": str(root),
        "APP_DB_PATH": str(root / "app.db"),
        "APP_VAULT_PATH": str(root / "unbound-vault"),
        "APP_ATTACHMENTS_PATH": str(root / "attachments"),
        "APP_EXPORTS_PATH": str(root / "exports"),
        "APP_BENCHMARK_DATASETS_PATH": str(root / "benchmarks"),
    })
    import uvicorn
    from app import host_bridge
    host_bridge.active = host_bridge.HostBridge(channel, handshake)
    from app.main import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    app.openapi_url = None
    app.router.routes[:] = [r for r in app.router.routes
                           if getattr(r, "path", "") not in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}]
    server = uvicorn.Server(uvicorn.Config(
        SessionAuth(app, config["secret"], config["generation"], port),
        log_config=None, access_log=False, lifespan="on", timeout_graceful_shutdown=5,
    ))

    def watch_parent():
        host_bridge.active.listen(lambda: setattr(server, "should_exit", True))

    threading.Thread(target=watch_parent, name="host-lifetime", daemon=True).start()

    async def run():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        for _ in range(3000):
            if task.done():
                await task
                return
            if server.started:
                payload = {"protocol": PROTOCOL, "pid": os.getpid(), "port": port,
                           "generation": config["generation"], "launcher_pid": config["launcher_pid"],
                           "proof": proof(config["secret"], config["challenge"],
                                          config["generation"], os.getpid(), port, config["launcher_pid"])}
                handshake.write(json.dumps(payload, separators=(",", ":")) + "\n")
                handshake.flush()
                await task
                return
            await asyncio.sleep(0.01)
        server.should_exit = True
        await task
        raise RuntimeError("CORE_READY_TIMEOUT")
    try:
        asyncio.run(run())
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
